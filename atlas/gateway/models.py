"""Data models for the API Gateway."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AuthType(Enum):
    """How the gateway injects credentials into outbound requests."""
    HEADER = "header"           # API key in a custom header
    QUERY_PARAM = "query_param" # API key as a URL query parameter
    BEARER = "bearer"           # Bearer token in Authorization header
    OAUTH2 = "oauth2"           # Google OAuth2 (tokens managed by gateway)
    CONSTRUCTOR = "constructor" # SDK client that takes api_key in constructor


@dataclass
class ServiceConfig:
    """Configuration for authenticating with an external service.
    
    Defines the base URL, auth strategy, and permission requirements
    for a registered external API.
    """
    name: str                        # e.g. "tavily"
    base_url: str                    # e.g. "https://api.tavily.com"
    auth_type: AuthType              # how to inject credentials
    auth_key_name: str               # keyring key name for the secret
    permission: str                  # required permission name
    auth_header: str | None = None   # header name (for HEADER/BEARER)
    auth_param: str | None = None    # query param name (for QUERY_PARAM)
    rate_limit: int | None = None    # max requests per minute
    cache_ttl: int | None = None     # response cache TTL in seconds
    allowed_endpoints: list[str] | None = None  # restrict to specific paths


@dataclass
class GatewayRequest:
    """A declarative API request from a tool to the gateway.
    
    Tools submit these instead of making HTTP calls directly.
    The gateway resolves credentials and executes the actual request.
    """
    service: str                     # registered service name
    method: str = "GET"              # HTTP method
    endpoint: str = ""               # path after base_url
    params: dict[str, Any] | None = None
    json_body: dict[str, Any] | None = None
    headers: dict[str, str] | None = None
    timeout: float = 10.0


@dataclass
class GatewayResponse:
    """Sanitised response returned to the tool.
    
    Contains the HTTP response without any credential information.
    """
    status_code: int
    body: dict | str | None
    headers: dict[str, str]
    service: str
    request_id: str                  # for audit log correlation
    ok: bool = True                  # convenience: status < 400
    error: str | None = None         # human-readable error if not ok
