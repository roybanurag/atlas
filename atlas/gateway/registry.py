"""Pre-configured service definitions for all external API integrations."""

from atlas.gateway.models import AuthType, ServiceConfig


DEFAULT_SERVICES: list[ServiceConfig] = [
    # --- Web / Search ---
    ServiceConfig(
        name="tavily",
        base_url="https://api.tavily.com",
        auth_type=AuthType.CONSTRUCTOR,
        auth_key_name="tavily",
        permission="internet_access",
    ),
    ServiceConfig(
        name="openweathermap",
        base_url="https://api.openweathermap.org/data/2.5",
        auth_type=AuthType.QUERY_PARAM,
        auth_key_name="openweathermap",
        auth_param="appid",
        permission="internet_access",
        cache_ttl=1800,  # 30 min
    ),
    
    # --- Google Services (OAuth2) ---
    ServiceConfig(
        name="google_gmail",
        base_url="https://gmail.googleapis.com",
        auth_type=AuthType.OAUTH2,
        auth_key_name="gmail",
        permission="email_read",
    ),
    ServiceConfig(
        name="google_calendar",
        base_url="https://www.googleapis.com/calendar",
        auth_type=AuthType.OAUTH2,
        auth_key_name="calendar",
        permission="calendar_read",
    ),
    ServiceConfig(
        name="google_drive",
        base_url="https://www.googleapis.com/drive",
        auth_type=AuthType.OAUTH2,
        auth_key_name="google_drive",
        permission="drive_read",
    ),
    ServiceConfig(
        name="google_tasks",
        base_url="https://tasks.googleapis.com",
        auth_type=AuthType.OAUTH2,
        auth_key_name="google_tasks",
        permission="calendar_read",
    ),
]


def build_registry() -> dict[str, ServiceConfig]:
    """Build the service registry from default definitions.
    
    Returns:
        Dict mapping service name to its ServiceConfig.
    """
    return {svc.name: svc for svc in DEFAULT_SERVICES}
