"""Configuration module for Atlas.

Provides unified, type-safe configuration for all Atlas components.
Configuration can be loaded from YAML files or created programmatically.

Usage:
    from atlas.config import get_config, AtlasConfig, MemoryConfig
    
    # Get global config (loaded from YAML)
    config = get_config()
    
    # Access component configs
    print(config.memory.max_context_tokens)
    print(config.llm.default_model)
"""

from .core import (
    API_KEY_CONFIGS,
    AtlasConfig,
    DEFAULT_CONFIG,
    DEFAULT_MEMORY_CONFIG,
    LLMConfig,
    MCPServerConfig,
    MemoryConfig,
    PrivacyLevel,
    SecurityConfig,
    ToolConfig,
    ToolsConfig,
)
from .loader import get_config, load_config, reload_config
from .principles import get_principles_path, load_principles
from .settings import get_config_dir, get_data_dir

__all__ = [
    # Core config classes
    "AtlasConfig",
    "MemoryConfig",
    "LLMConfig",
    "SecurityConfig",
    "ToolsConfig",
    "ToolConfig",
    "MCPServerConfig",
    "PrivacyLevel",
    # Config data
    "API_KEY_CONFIGS",
    "DEFAULT_CONFIG",
    "DEFAULT_MEMORY_CONFIG",
    # Loaders
    "load_config",
    "get_config",
    "reload_config",
    # Utilities
    "load_principles",
    "get_principles_path",
    "get_data_dir",
    "get_config_dir",
]
