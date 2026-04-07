"""Atlas API Gateway — secure credential injection for outbound API calls.

Usage::

    from atlas.gateway import APIGateway, GatewayRequest, GatewayResponse

    gateway = APIGateway(permission_manager=pm)
    resp = await gateway.request(GatewayRequest(service="tavily", ...))
"""

from atlas.gateway.gateway import APIGateway
from atlas.gateway.models import (
    AuthType,
    GatewayRequest,
    GatewayResponse,
    ServiceConfig,
)
from atlas.gateway.registry import DEFAULT_SERVICES, build_registry

__all__ = [
    "APIGateway",
    "AuthType",
    "GatewayRequest",
    "GatewayResponse",
    "ServiceConfig",
    "DEFAULT_SERVICES",
    "build_registry",
]
