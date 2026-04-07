"""Security module for Atlas."""

from .audit import AuditLogger
from .guardrails import GuardrailEngine
from .permissions import (
    GrantDuration,
    PermissionGrant,
    PermissionLevel,
    PermissionManager,
    _DECORATOR_PERMISSIONS,
    requires_permission,
)
from .secrets import API_KEY_CONFIGS, SecretManager, get_api_key, set_api_key

__all__ = [
    "AuditLogger",
    "GuardrailEngine",
    "PermissionManager",
    "PermissionLevel",
    "PermissionGrant",
    "GrantDuration",
    "SecretManager",
    "get_api_key",
    "set_api_key",
    "API_KEY_CONFIGS",
    "requires_permission",
    "_DECORATOR_PERMISSIONS",
]

