"""Core configuration dataclasses for Atlas.

This module provides type-safe configuration classes for all Atlas components.
Configuration can be loaded from YAML files or created programmatically.
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class PrivacyLevel(Enum):
    """Privacy levels for LLM routing."""
    LOCAL_ONLY = "local_only"      # Never leave device
    REMOTE_ALLOWED = "remote_ok"   # User consented to remote
    SENSITIVE = "sensitive"        # Requires local + extra care


@dataclass
class MemoryConfig:
    """Configuration for Atlas memory management.
    
    Attributes:
        max_context_tokens: Maximum tokens to include in context (default: 2000)
        max_message_length: Truncate messages longer than this (default: 500 chars)
        summarize_threshold: Summarize responses longer than this (default: 300 chars)
        hot_memory_size: Number of recent messages to keep in full (default: 10)
        warm_memory_sessions: Number of recent sessions to keep summaries (default: 5)
        embedding_model: Sentence transformer model for semantic search
        use_embeddings: Whether to use semantic search (requires sentence-transformers)
        clean_thinking_tags: Remove <think> artifacts from stored content
        retention_days: How long to keep conversation history
    """
    
    # Token management
    max_context_tokens: int = 2000
    max_message_length: int = 500
    summarize_threshold: int = 300
    
    # Tiered memory sizes
    hot_memory_size: int = 10
    warm_memory_sessions: int = 5
    
    # Embedding settings
    embedding_model: str = "all-MiniLM-L6-v2"
    use_embeddings: bool = True
    
    # Cleaning settings
    clean_thinking_tags: bool = True
    
    # Retention
    retention_days: int = 365
    
    # Daily log settings
    daily_log_load_days: int = 2  # Load today + yesterday at session start
    
    # Compaction settings
    compaction_threshold: float = 0.8  # Trigger at 80% of context budget
    compaction_keep_last: int = 5      # Keep last N messages uncompacted
    
    # Session pruning
    prune_tool_results: bool = True
    prune_soft_trim_chars: int = 4000
    
    # Approximate tokens per character (conservative estimate)
    chars_per_token: float = 4.0
    
    def estimate_tokens(self, text: str) -> int:
        """Estimate token count for a piece of text."""
        return int(len(text) / self.chars_per_token)
    
    def fits_in_budget(self, text: str, current_tokens: int = 0) -> bool:
        """Check if text fits within remaining token budget."""
        estimated = self.estimate_tokens(text)
        return (current_tokens + estimated) <= self.max_context_tokens


@dataclass
class LLMConfig:
    """Configuration for LLM providers and routing."""
    
    # Default model
    default_model: str = "qwen3:14b"
    privacy_mode: PrivacyLevel = PrivacyLevel.LOCAL_ONLY
    
    # Local LLM (Ollama)
    local_provider: str = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    local_models: list[str] = field(default_factory=lambda: ["qwen3:14b", "llama3.2", "mistral"])
    
    # Remote LLM (optional)
    remote_provider: str = "openai"
    # API key from environment or keyring


@dataclass
class SecurityConfig:
    """Configuration for security and permissions."""
    
    require_confirmation_level: str = "high"  # low, medium, high, critical
    permission_config_path: str = "~/.config/atlas/permissions.yaml"
    audit_log_dir: str = "~/.local/share/atlas/audit"
    
    # Tool deny list — tool display names to block from loading
    # e.g. ["Web search", "Web reader"]
    tools_deny: list[str] = field(default_factory=list)
    
    # Slack user allowlist — Slack user IDs allowed to interact
    # Empty list = allow all (no restriction)
    # e.g. ["U01ABC123", "U02DEF456"]
    slack_allowed_users: list[str] = field(default_factory=list)


@dataclass
class ToolConfig:
    """Configuration for a single tool."""
    enabled: bool = True
    # Additional tool-specific settings can be added


@dataclass
class ToolsConfig:
    """Configuration for Atlas tools."""
    
    tavily: ToolConfig = field(default_factory=ToolConfig)
    gmail: ToolConfig = field(default_factory=ToolConfig)
    calendar: ToolConfig = field(default_factory=ToolConfig)
    google_drive: ToolConfig = field(default_factory=ToolConfig)


@dataclass
class MCPServerConfig:
    """Configuration for an MCP server."""
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    auto_connect: bool = False


@dataclass
class BriefingConfig:
    """Configuration for daily briefing."""
    news_topics: list[str] = field(default_factory=lambda: ["technology", "AI"])
    max_headlines_per_topic: int = 3


@dataclass
class AtlasConfig:
    """Master configuration for Atlas agent.
    
    This is the top-level configuration that contains all component configs.
    """
    
    # Agent identity
    agent_name: str = "Atlas"
    
    # Component configurations
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    briefing: BriefingConfig = field(default_factory=BriefingConfig)
    
    # MCP servers
    mcp_servers: list[MCPServerConfig] = field(default_factory=list)
    
    # Data directory
    data_dir: str = "~/.local/share/atlas"
    config_dir: str = "~/.config/atlas"
    
    @property
    def data_path(self) -> Path:
        """Get expanded data directory path."""
        return Path(self.data_dir).expanduser()
    
    @property
    def config_path(self) -> Path:
        """Get expanded config directory path."""
        return Path(self.config_dir).expanduser()


# API key configurations for secrets management
API_KEY_CONFIGS: dict[str, dict[str, str]] = {
    "tavily": {
        "keyring_name": "tavily_api_key",
        "env_var": "TAVILY_API_KEY",
        "description": "API key for Tavily web search",
        "url": "https://tavily.com",
    },
    "openai": {
        "keyring_name": "openai_api_key",
        "env_var": "OPENAI_API_KEY",
        "description": "API key for OpenAI",
        "url": "https://platform.openai.com/api-keys",
    },
    "anthropic": {
        "keyring_name": "anthropic_api_key",
        "env_var": "ANTHROPIC_API_KEY",
        "description": "API key for Anthropic Claude",
        "url": "https://console.anthropic.com/settings/keys",
    },
    "google": {
        "keyring_name": "google_api_key",
        "env_var": "GOOGLE_API_KEY",
        "description": "Google AI API key",
        "url": "https://makersuite.google.com/app/apikey",
    },
    "google_oauth": {
        "keyring_name": "google_oauth_credentials_path",
        "env_var": "GOOGLE_OAUTH_CREDENTIALS_PATH",
        "description": "Google OAuth2 credentials JSON content/path for all Google APIs",
        "url": "https://console.cloud.google.com/apis/credentials",
    },
    "slack_bot_token": {
        "keyring_name": "slack_bot_token",
        "env_var": "SLACK_BOT_TOKEN",
        "description": "Slack Bot User OAuth Token (xoxb-...)",
        "url": "https://api.slack.com/apps",
    },
    "slack_app_token": {
        "keyring_name": "slack_app_token",
        "env_var": "SLACK_APP_TOKEN",
        "description": "Slack App-Level Token (xapp-...)",
        "url": "https://api.slack.com/apps",
    },
}


# Default configuration instance
DEFAULT_CONFIG = AtlasConfig()
DEFAULT_MEMORY_CONFIG = MemoryConfig()
