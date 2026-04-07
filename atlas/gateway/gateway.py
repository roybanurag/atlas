"""Secure API Gateway — injects credentials without exposing them to the agent.

All outbound API calls from tools pass through this gateway. The gateway:
1. Resolves credentials from the system keyring
2. Checks permissions via PermissionManager
3. Injects auth into the HTTP request
4. Returns a sanitised response (no secrets leaked)
"""

import logging
import re
import time
import uuid
from typing import Any

import httpx

from atlas.gateway.models import (
    AuthType,
    GatewayRequest,
    GatewayResponse,
    ServiceConfig,
)
from atlas.gateway.registry import build_registry

logger = logging.getLogger(__name__)


class APIGateway:
    """Secure API gateway that injects credentials into outbound requests.
    
    Usage::
    
        gateway = APIGateway(permission_manager=pm)
        resp = await gateway.request(GatewayRequest(
            service="tavily",
            method="POST",
            endpoint="/search",
            json_body={"query": "latest news"},
        ))
    """
    
    def __init__(
        self,
        permission_manager: Any = None,
        audit_logger: Any = None,
    ):
        self._registry = build_registry()
        self._pm = permission_manager
        self._audit = audit_logger
        self._http = httpx.AsyncClient(follow_redirects=True)
        self._cache: dict[str, tuple[float, GatewayResponse]] = {}
    
    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    
    async def request(self, req: GatewayRequest) -> GatewayResponse:
        """Execute an API call with auto-injected credentials.
        
        Args:
            req: Declarative request (service name, endpoint, params).
                 Must NOT contain credentials — those are injected.
        
        Returns:
            GatewayResponse with status, body, and request_id.
        
        Raises:
            ValueError: If service is not registered.
            PermissionError: If permission check fails.
        """
        request_id = uuid.uuid4().hex[:12]
        
        # 1. Resolve service config
        config = self._registry.get(req.service)
        if not config:
            return GatewayResponse(
                status_code=0,
                body=None,
                headers={},
                service=req.service,
                request_id=request_id,
                ok=False,
                error=f"Unknown service: {req.service}",
            )
        
        # 2. Permission check
        if self._pm and config.permission:
            scope = config.base_url
            granted = await self._pm.check(config.permission, scope)
            if not granted:
                self._log_audit(request_id, config, req, "denied")
                return GatewayResponse(
                    status_code=403,
                    body=None,
                    headers={},
                    service=req.service,
                    request_id=request_id,
                    ok=False,
                    error=f"Permission '{config.permission}' denied for {config.base_url}",
                )
        
        # 3. Check cache for idempotent GET requests
        if req.method.upper() == "GET" and config.cache_ttl:
            cached = self._check_cache(req, config.cache_ttl)
            if cached:
                self._log_audit(request_id, config, req, "cache_hit")
                cached.request_id = request_id
                return cached
        
        # 4. Resolve credentials (never returned to caller)
        try:
            secret = self._resolve_secret(config)
        except Exception as e:
            return GatewayResponse(
                status_code=0,
                body=None,
                headers={},
                service=req.service,
                request_id=request_id,
                ok=False,
                error=f"Credential resolution failed for '{req.service}': {e}",
            )
        
        # 5. Build and execute HTTP request
        try:
            response = await self._execute(req, config, secret)
        except httpx.TimeoutException:
            self._log_audit(request_id, config, req, "timeout")
            return GatewayResponse(
                status_code=0,
                body=None,
                headers={},
                service=req.service,
                request_id=request_id,
                ok=False,
                error=f"Request to {req.service} timed out after {req.timeout}s",
            )
        except Exception as e:
            self._log_audit(request_id, config, req, "error", str(e))
            return GatewayResponse(
                status_code=0,
                body=None,
                headers={},
                service=req.service,
                request_id=request_id,
                ok=False,
                error=f"Request failed: {e}",
            )
        
        # 6. Build sanitised response
        try:
            body = response.json()
        except Exception:
            body = response.text
        
        gw_response = GatewayResponse(
            status_code=response.status_code,
            body=self._sanitize(body, config),
            headers=dict(response.headers),
            service=req.service,
            request_id=request_id,
            ok=response.is_success,
            error=None if response.is_success else f"HTTP {response.status_code}",
        )
        
        # 7. Cache if applicable
        if req.method.upper() == "GET" and config.cache_ttl and gw_response.ok:
            self._store_cache(req, gw_response)
        
        self._log_audit(request_id, config, req, "success", f"HTTP {response.status_code}")
        return gw_response
    
    def register_service(self, config: ServiceConfig) -> None:
        """Register or override a service configuration."""
        self._registry[config.name] = config
    
    async def close(self) -> None:
        """Shut down the HTTP client. Call on application exit."""
        await self._http.aclose()
    
    # ------------------------------------------------------------------
    # Credential resolution (private — secrets never leave this layer)
    # ------------------------------------------------------------------
    
    def _resolve_secret(self, config: ServiceConfig) -> str | None:
        """Fetch the API key / secret from the system keyring.
        
        Returns the raw secret for injection. This value is NEVER
        included in any return value or log entry.
        """
        from atlas.security.secrets import get_api_key
        
        secret = get_api_key(config.auth_key_name)
        if not secret and config.auth_type not in (AuthType.OAUTH2,):
            raise ValueError(
                f"No credential found for service '{config.name}' "
                f"(keyring key: {config.auth_key_name}). "
                f"Run: atlas secrets set {config.auth_key_name}"
            )
        return secret
    
    # ------------------------------------------------------------------
    # HTTP execution (private)
    # ------------------------------------------------------------------
    
    async def _execute(
        self,
        req: GatewayRequest,
        config: ServiceConfig,
        secret: str | None,
    ) -> httpx.Response:
        """Build the authenticated HTTP request and execute it."""
        url = f"{config.base_url.rstrip('/')}/{req.endpoint.lstrip('/')}" if req.endpoint else config.base_url
        headers = dict(req.headers or {})
        params = dict(req.params or {})
        
        # Inject credentials based on auth type
        if config.auth_type == AuthType.HEADER and secret:
            header_name = config.auth_header or "X-API-Key"
            headers[header_name] = secret
        
        elif config.auth_type == AuthType.QUERY_PARAM and secret:
            param_name = config.auth_param or "api_key"
            params[param_name] = secret
        
        elif config.auth_type == AuthType.BEARER and secret:
            headers["Authorization"] = f"Bearer {secret}"
        
        elif config.auth_type == AuthType.CONSTRUCTOR and secret:
            # For SDK-based services (e.g. TavilyClient), we make
            # a direct HTTP call instead so the tool never sees the key.
            # Tavily's REST API accepts the key in the JSON body.
            if req.json_body is not None:
                req.json_body["api_key"] = secret
            else:
                params["api_key"] = secret
        
        elif config.auth_type == AuthType.OAUTH2:
            # OAuth2 services are handled via GoogleServiceAuth
            # which manages its own tokens. For now, pass through.
            pass
        
        return await self._http.request(
            method=req.method.upper(),
            url=url,
            params=params or None,
            json=req.json_body,
            headers=headers or None,
            timeout=req.timeout,
        )
    
    # ------------------------------------------------------------------
    # Response sanitization (private)
    # ------------------------------------------------------------------
    
    _SECRET_PATTERN = re.compile(
        r'(api[_\-]?key|token|secret|password|credential)'
        r'[\"\s:=]+[\"\']?([\w\-\.]{20,})',
        re.IGNORECASE,
    )
    
    def _sanitize(self, body: Any, config: ServiceConfig) -> Any:
        """Remove any echoed secrets from the response body."""
        if isinstance(body, str):
            return self._SECRET_PATTERN.sub(r'\1=***REDACTED***', body)
        if isinstance(body, dict):
            return self._sanitize_dict(body)
        return body
    
    def _sanitize_dict(self, d: dict) -> dict:
        """Recursively sanitize dictionary values."""
        result = {}
        sensitive_keys = {"api_key", "apikey", "token", "secret", "password", "credential"}
        for k, v in d.items():
            if k.lower().replace("-", "_") in sensitive_keys:
                result[k] = "***REDACTED***"
            elif isinstance(v, dict):
                result[k] = self._sanitize_dict(v)
            elif isinstance(v, list):
                result[k] = [
                    self._sanitize_dict(item) if isinstance(item, dict) else item
                    for item in v
                ]
            else:
                result[k] = v
        return result
    
    # ------------------------------------------------------------------
    # Cache (private)
    # ------------------------------------------------------------------
    
    def _cache_key(self, req: GatewayRequest) -> str:
        """Generate a deterministic cache key."""
        import json
        parts = [req.service, req.method, req.endpoint]
        if req.params:
            parts.append(json.dumps(req.params, sort_keys=True))
        return ":".join(parts)
    
    def _check_cache(self, req: GatewayRequest, ttl: int) -> GatewayResponse | None:
        key = self._cache_key(req)
        if key in self._cache:
            ts, resp = self._cache[key]
            if time.monotonic() - ts < ttl:
                return resp
            del self._cache[key]
        return None
    
    def _store_cache(self, req: GatewayRequest, resp: GatewayResponse) -> None:
        key = self._cache_key(req)
        self._cache[key] = (time.monotonic(), resp)
    
    # ------------------------------------------------------------------
    # Audit logging (private)
    # ------------------------------------------------------------------
    
    def _log_audit(
        self,
        request_id: str,
        config: ServiceConfig,
        req: GatewayRequest,
        outcome: str,
        detail: str = "",
    ) -> None:
        """Log an audit entry. Never logs the actual secret."""
        logger.info(
            "gateway_request | id=%s service=%s endpoint=%s method=%s outcome=%s %s",
            request_id, config.name, req.endpoint, req.method, outcome, detail,
        )
        if self._audit:
            self._audit.log("gateway_request", {
                "request_id": request_id,
                "service": config.name,
                "endpoint": req.endpoint,
                "method": req.method,
                "outcome": outcome,
                "detail": detail,
            })
