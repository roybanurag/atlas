"""Configuration loader for Atlas.

Loads configuration from YAML files and provides a global config instance.
"""

from pathlib import Path
from typing import Optional

import yaml

from atlas.config.paths import get_config_dir

from .core import (
    AtlasConfig,
    BriefingConfig,
    LLMConfig,
    MCPServerConfig,
    MemoryConfig,
    PrivacyLevel,
    SecurityConfig,
    ToolConfig,
    ToolsConfig,
)


# Global cached config
_config: Optional[AtlasConfig] = None


def find_config_file() -> Optional[Path]:
    """Find the configuration file.
    
    Searches in order:
    1. atlas/config/default.yaml (same directory as this module)
    2. ~/.config/atlas/config.yaml (user config)
    
    Returns:
        Path to config file or None if not found
    """
    # Try package config directory first (atlas/config/)
    current = Path(__file__).parent  # atlas/config
    project_config = current / "default.yaml"
    if project_config.exists():
        return project_config
    
    # Try user config
    user_config = get_config_dir() / "config.yaml"
    if user_config.exists():
        return user_config
    
    return None


def load_config(config_path: Optional[Path] = None) -> AtlasConfig:
    """Load configuration from YAML file.
    
    Args:
        config_path: Optional path to config file. If not provided,
                    searches default locations.
    
    Returns:
        AtlasConfig instance with loaded settings
    """
    global _config
    
    # Use cached config if available and no path specified
    if _config is not None and config_path is None:
        return _config
    
    # Find config file
    if config_path is None:
        config_path = find_config_file()
    
    # Start with defaults
    config = AtlasConfig()
    
    # Load from YAML if found
    if config_path and config_path.exists():
        try:
            with open(config_path, 'r') as f:
                data = yaml.safe_load(f) or {}
            
            config = _parse_yaml_config(data)
        except Exception as e:
            # If loading fails, use defaults
            print(f"Warning: Failed to load config from {config_path}: {e}")
    
    # Cache the config
    _config = config
    
    return config


def _parse_yaml_config(data: dict) -> AtlasConfig:
    """Parse YAML data into AtlasConfig."""
    import warnings
    
    # Validate top-level keys to catch typos
    VALID_TOP_LEVEL_KEYS = {
        "agent", "memory", "llm", "security", "tools",
        "briefing", "mcp_servers",
    }
    unknown_keys = set(data.keys()) - VALID_TOP_LEVEL_KEYS
    if unknown_keys:
        warnings.warn(
            f"Unknown config keys in atlas config: {unknown_keys}. "
            f"Valid keys are: {sorted(VALID_TOP_LEVEL_KEYS)}"
        )
    
    # Agent settings
    agent_data = data.get("agent", {})
    agent_name = agent_data.get("name", "Atlas")
    
    # Memory settings
    memory_data = data.get("memory", {})
    memory_config = MemoryConfig(
        retention_days=memory_data.get("retention_days", 365),
    )
    
    # Expand data_dir path
    data_dir = memory_data.get("data_dir", "~/.local/share/atlas")
    
    # LLM settings
    llm_data = data.get("llm", {})
    local_data = llm_data.get("local", {})
    
    privacy_str = agent_data.get("privacy_mode", "local_only")
    try:
        privacy_mode = PrivacyLevel(privacy_str)
    except ValueError:
        privacy_mode = PrivacyLevel.LOCAL_ONLY
    
    llm_config = LLMConfig(
        default_model=agent_data.get("default_model", "qwen3:14b"),
        privacy_mode=privacy_mode,
        local_provider=local_data.get("provider", "ollama"),
        ollama_base_url=local_data.get("base_url", "http://localhost:11434"),
        local_models=local_data.get("models", ["qwen3:14b"]),
        remote_provider=llm_data.get("remote", {}).get("provider", "openai"),
    )
    
    # Security settings
    security_data = data.get("security", {})
    security_config = SecurityConfig(
        require_confirmation_level=security_data.get("require_confirmation_level", "high"),
        permission_config_path=security_data.get("permission_config", "~/.config/atlas/permissions.yaml"),
        audit_log_dir=security_data.get("audit_log_dir", "~/.local/share/atlas/audit"),
    )
    
    # Tools settings
    tools_data = data.get("tools", {})
    tools_config = ToolsConfig(
        tavily=ToolConfig(enabled=tools_data.get("tavily", {}).get("enabled", True)),
        gmail=ToolConfig(enabled=tools_data.get("gmail", {}).get("enabled", True)),
        calendar=ToolConfig(enabled=tools_data.get("calendar", {}).get("enabled", True)),
        google_drive=ToolConfig(enabled=tools_data.get("google_drive", {}).get("enabled", True)),
    )
    
    # Briefing settings
    briefing_data = data.get("briefing", {})
    briefing_config = BriefingConfig(
        news_topics=briefing_data.get("news_topics", ["technology", "AI"]),
        max_headlines_per_topic=briefing_data.get("max_headlines_per_topic", 3),
    )
    
    # MCP servers
    mcp_data = data.get("mcp_servers", [])
    mcp_servers = [
        MCPServerConfig(
            name=server.get("name", ""),
            command=server.get("command", ""),
            args=server.get("args", []),
            auto_connect=server.get("auto_connect", False),
        )
        for server in mcp_data
    ]
    
    return AtlasConfig(
        agent_name=agent_name,
        memory=memory_config,
        llm=llm_config,
        security=security_config,
        tools=tools_config,
        briefing=briefing_config,
        mcp_servers=mcp_servers,
        data_dir=data_dir,
    )


def get_config() -> AtlasConfig:
    """Get the global configuration instance.
    
    Loads from default locations on first call, then returns cached instance.
    """
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reload_config(config_path: Optional[Path] = None) -> AtlasConfig:
    """Force reload configuration.
    
    Args:
        config_path: Optional path to config file
        
    Returns:
        Newly loaded AtlasConfig
    """
    global _config
    _config = None
    return load_config(config_path)
