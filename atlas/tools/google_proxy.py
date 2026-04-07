"""Thin client for calling Google APIs via the Atlas Gateway proxy.

All Google tool code calls this module instead of importing google_auth or
building googleapiclient service objects directly. Credentials never enter
tool process memory — they are managed entirely by the gateway server.

Usage::

    from atlas.tools.google_proxy import google_call

    result = google_call(
        service="gmail",
        resource_path="users.messages",
        method="list",
        params={"userId": "me", "q": "is:unread", "maxResults": 10},
    )
"""

from typing import Any, Optional

import httpx
from atlas.gateway.headers import get_gateway_headers

_GATEWAY_URL = "http://127.0.0.1:18080/v1/google"
_TIMEOUT = 30.0


def google_call(
    service: str,
    resource_path: str,
    method: str,
    params: Optional[dict] = None,
    body: Optional[dict] = None,
) -> Any:
    """Execute a Google API call through the Atlas Gateway proxy.

    Args:
        service: Google service name — "gmail", "calendar", "drive", or "tasks".
        resource_path: Dot-separated resource path, e.g. "users.messages",
            "events", "files", "tasklists".
        method: API method name, e.g. "list", "get", "insert", "send".
        params: Query/path parameters (excluding body) passed to the method.
        body: Request body dict, passed as ``body=`` to the API method.

    Returns:
        The parsed JSON response from the Google API.

    Raises:
        RuntimeError: If the gateway is not running or returns an error.
        PermissionError: If the required permission has not been granted.
    """
    payload: dict = {
        "service": service,
        "resource_path": resource_path,
        "method": method,
    }
    if params:
        payload["params"] = params
    if body is not None:
        payload["body"] = body

    try:
        resp = httpx.post(_GATEWAY_URL, json=payload, headers=get_gateway_headers(), timeout=_TIMEOUT)
    except httpx.ConnectError:
        raise RuntimeError(
            "Atlas Gateway is not running. "
            "Start 'atlas chat' or 'atlas slack' to launch it automatically."
        )

    if resp.status_code == 403:
        detail = resp.json().get("detail", "Permission denied")
        raise PermissionError(detail)

    if resp.status_code == 401:
        detail = resp.json().get("detail", "Authentication error")
        raise ValueError(detail)  # Triggers "not configured" message in tools

    if not resp.is_success:
        detail = resp.json().get("detail", resp.text)
        raise RuntimeError(f"Google API error ({resp.status_code}): {detail}")

    return resp.json()
