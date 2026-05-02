"""Out-of-process HTTP Server for APIGateway.

This server completely isolates credential injection and execution from the agent.
It exposes two endpoints on localhost:18080:

  POST /v1/proxy        — for REST API services (Tavily, OpenWeatherMap etc.)
  POST /v1/google       — for Google API services (Gmail, Calendar, Drive, Tasks)

Tools POST requests with no credentials. The gateway resolves credentials,
executes the call, and returns a sanitised response.
"""

import logging
from typing import Any, Dict, List, Optional

import uvicorn
import secrets
import os
from fastapi import FastAPI, HTTPException, Depends, Security
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel

from atlas.config.paths import get_data_dir

from atlas.gateway.gateway import APIGateway
from atlas.gateway.models import GatewayRequest
from atlas.security.permissions import PermissionManager

logger = logging.getLogger(__name__)

app = FastAPI(title="Atlas APIGateway Vault", version="2.0")

# Singleton instances, initialised at startup
_gateway: APIGateway = None
_perm_manager: PermissionManager = None

# ---------------------------------------------------------------------------
# Ephemeral Token Security
# ---------------------------------------------------------------------------

API_KEY_NAME = "Authorization"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=True)
_EPHEMERAL_TOKEN: str = ""

def _generate_ephemeral_token() -> str:
    """Generate and store the transient boot token for Gateway IPC."""
    global _EPHEMERAL_TOKEN
    import os
    _EPHEMERAL_TOKEN = f"Bearer {secrets.token_hex(32)}"
    
    token_path = get_data_dir() / ".gateway_token"
    token_path.write_text(_EPHEMERAL_TOKEN)
    os.chmod(str(token_path), 0o600)
    return _EPHEMERAL_TOKEN

async def verify_gateway_token(api_key: str = Security(api_key_header)):
    if api_key != _EPHEMERAL_TOKEN:
        raise HTTPException(
            status_code=403, 
            detail="Invalid or missing Ephemeral Gateway authorization token. Background access denied."
        )
    return api_key


# ---------------------------------------------------------------------------
# shared dependency helpers
# ---------------------------------------------------------------------------

def _require_gateway() -> APIGateway:
    if _gateway is None:
        raise HTTPException(status_code=503, detail="Gateway not initialised")
    return _gateway

# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup_event():
    global _gateway, _perm_manager
    _generate_ephemeral_token()
    _perm_manager = PermissionManager()
    _gateway = APIGateway(permission_manager=_perm_manager)
    logger.info("Atlas Gateway Vault started on :18080 with ephemeral JWT lockdown.")

@app.on_event("shutdown")
async def shutdown_event():
    # Clean up ephemeral token file so no stale credential sits on disk
    token_path = get_data_dir() / ".gateway_token"
    if token_path.exists():
        token_path.unlink()
    if _gateway:
        await _gateway.close()

# ---------------------------------------------------------------------------
# Schema: generic REST proxy (Tavily, OpenWeatherMap, …)
# ---------------------------------------------------------------------------

class ProxyRequestDTO(BaseModel):
    service: str
    method: str
    endpoint: str = ""
    params: Optional[Dict[str, Any]] = None
    json_body: Optional[Dict[str, Any]] = None
    headers: Optional[Dict[str, str]] = None
    timeout: float = 30.0

@app.post("/v1/proxy", dependencies=[Depends(verify_gateway_token)])
async def proxy_request(req: ProxyRequestDTO):
    """Proxy REST requests with server-side credential injection."""
    gw = _require_gateway()
    gw_req = GatewayRequest(
        service=req.service,
        method=req.method,
        endpoint=req.endpoint,
        params=req.params,
        json_body=req.json_body,
        headers=req.headers,
        timeout=req.timeout,
    )
    response = await gw.request(gw_req)
    if not response.ok:
        raise HTTPException(status_code=response.status_code or 500, detail=response.error)
    return response.body

# ---------------------------------------------------------------------------
# Schema: Google API proxy
# ---------------------------------------------------------------------------

# Map service→(permission_name, api_name, api_version)
_GOOGLE_SERVICES: Dict[str, tuple] = {
    "gmail":    ("email_read",      "gmail",    "v1"),
    "calendar": ("calendar_read",   "calendar", "v3"),
    "drive":    ("drive_read",      "drive",    "v3"),
    "tasks":    ("tasks_read",      "tasks",    "v1"),
}

# Write-path operations require elevated permissions
_WRITE_METHODS = {"insert", "create", "update", "patch", "delete", "send", "trash", "modify"}

_WRITE_PERMISSIONS: Dict[str, str] = {
    "gmail":    "email_send",
    "calendar": "calendar_write",
    "drive":    "drive_write",
    "tasks":    "tasks_write",
}

class GoogleRequestDTO(BaseModel):
    """Describes a Google API call.

    resource_path: dot-separated Google API resource path, e.g.
        "users.messages"        → gmail.users().messages()
        "events"                → calendar.events()
        "files"                 → drive.files()
        "tasklists"             → tasks.tasklists()

    method: Google API method name, e.g. "list", "get", "insert", "send"
    params: keyword arguments passed to the method call (excluding 'body')
    body: the request body (passed as `body=` to the API method)
    """
    service: str                                 # "gmail" | "calendar" | "drive" | "tasks"
    resource_path: str                           # e.g. "users.messages"
    method: str                                  # e.g. "list", "get", "insert"
    params: Optional[Dict[str, Any]] = None
    body: Optional[Dict[str, Any]] = None

def _build_google_service(service_name: str):
    """Build an authenticated Google API service using gateway-managed credentials."""
    from atlas.tools.google_auth import (
        GMAIL_AUTH, CALENDAR_AUTH, DRIVE_AUTH, TASKS_AUTH
    )
    auth_map = {
        "gmail":    (GMAIL_AUTH,    "gmail",    "v1"),
        "calendar": (CALENDAR_AUTH, "calendar", "v3"),
        "drive":    (DRIVE_AUTH,    "drive",    "v3"),
        "tasks":    (TASKS_AUTH,    "tasks",    "v1"),
    }
    if service_name not in auth_map:
        raise ValueError(f"Unknown Google service: {service_name}")
    auth, api_name, api_version = auth_map[service_name]
    return auth.get_service(api_name, api_version)

def _execute_google_call(service_name: str, resource_path: str, method: str,
                         params: dict, body: Optional[dict]) -> Any:
    """Build the service, navigate the resource path, and call the method."""
    svc = _build_google_service(service_name)

    # Navigate nested resource: "users.messages" → svc.users().messages()
    resource = svc
    for part in resource_path.split("."):
        resource = getattr(resource, part)()

    # Call the method
    method_fn = getattr(resource, method)
    call_kwargs = dict(params or {})
    if body is not None:
        call_kwargs["body"] = body

    return method_fn(**call_kwargs).execute()

@app.post("/v1/google", dependencies=[Depends(verify_gateway_token)])
async def proxy_google_request(req: GoogleRequestDTO):
    """Execute a Google API call with gateway-managed OAuth credentials.

    Permission checks are enforced before any credential is touched.
    """
    if req.service not in _GOOGLE_SERVICES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown Google service '{req.service}'. "
                   f"Valid: {list(_GOOGLE_SERVICES.keys())}"
        )

    # Determine required permission (write ops need elevated permission)
    base_perm, _, _ = _GOOGLE_SERVICES[req.service]
    is_write = req.method.lower() in _WRITE_METHODS
    required_perm = _WRITE_PERMISSIONS.get(req.service, base_perm) if is_write else base_perm

    # Permission check via PermissionManager
    if _perm_manager:
        granted = await _perm_manager.check(required_perm, f"{req.service}.google.com")
        if not granted:
            raise HTTPException(
                status_code=403,
                detail=f"Permission '{required_perm}' denied for {req.service}"
            )

    try:
        import asyncio
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: _execute_google_call(
                req.service, req.resource_path, req.method,
                req.params or {}, req.body,
            )
        )
        return result
    except ValueError as exc:
        # Configuration / auth errors
        raise HTTPException(status_code=401, detail=str(exc))
    except Exception as exc:
        logger.exception("Google API call failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Background launcher
# ---------------------------------------------------------------------------

def start_server_in_background():
    """Start the uvicorn server as a daemon thread.
    Called by 'atlas chat' and 'atlas slack' at startup.
    """
    import threading

    def run():
        uvicorn.run(app, host="127.0.0.1", port=18080, access_log=False)

    t = threading.Thread(target=run, daemon=True, name="atlas-gateway")
    t.start()
    return t

