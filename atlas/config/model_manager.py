"""Model manager — create LLM instances from provider:model strings.

Supports switching between local (Ollama) and remote (OpenAI, Anthropic,
Google) models mid-conversation. Provider packages are lazily imported
to avoid startup penalties.

Usage:
    mgr = ModelManager()
    llm = mgr.create_llm("openai:gpt-4o", tools=tools)
    llm = mgr.create_llm("qwen3:14b")  # defaults to ollama
"""

import importlib
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


# Provider registry: provider_name → (package, class_name, api_key_service)
PROVIDERS: dict[str, dict[str, str]] = {
    "ollama": {
        "package": "langchain_ollama",
        "class": "ChatOllama",
        "key_service": "",  # no API key needed
        "label": "Local (Ollama)",
    },
    "openai": {
        "package": "langchain_openai",
        "class": "ChatOpenAI",
        "key_service": "openai",
        "label": "OpenAI",
    },
    "anthropic": {
        "package": "langchain_anthropic",
        "class": "ChatAnthropic",
        "key_service": "anthropic",
        "label": "Anthropic",
    },
    "google": {
        "package": "langchain_google_genai",
        "class": "ChatGoogleGenerativeAI",
        "key_service": "google",
        "label": "Google AI",
    },
}


class ModelManager:
    """Factory for creating LangChain chat model instances.
    
    Parses 'provider:model' strings and creates the appropriate
    chat model. Handles API key retrieval, lazy imports, and
    tool re-binding on model switch.
    """
    
    def __init__(self, default_provider: str = "ollama"):
        self.default_provider = default_provider
        self.current_provider: str | None = None
        self.current_model: str | None = None
        self._current_llm: Any = None
    
    def parse_model_string(self, model_string: str) -> tuple[str, str]:
        """Parse 'provider:model' string into (provider, model_name).
        
        If no provider prefix, defaults to self.default_provider.
        Handles Ollama model names with colons (e.g. 'qwen3:14b').
        
        Args:
            model_string: String like 'openai:gpt-4o' or 'qwen3:14b'
            
        Returns:
            Tuple of (provider_name, model_name)
        """
        model_string = model_string.strip()
        
        # Check if string starts with a known provider prefix
        for provider in PROVIDERS:
            prefix = f"{provider}:"
            if model_string.startswith(prefix):
                model_name = model_string[len(prefix):]
                if model_name:
                    return provider, model_name
        
        # No known provider prefix — use default
        return self.default_provider, model_string
    
    def create_llm(
        self,
        model_string: str,
        tools: list | None = None,
        **kwargs,
    ) -> Any:
        """Create a chat model from a 'provider:model' string.
        
        Lazy-imports the provider package, retrieves the API key
        if needed, creates the chat model, and optionally binds tools.
        
        Args:
            model_string: Model identifier (e.g. 'openai:gpt-4o')
            tools: Optional list of tools to bind to the model
            **kwargs: Extra kwargs passed to the chat model constructor
            
        Returns:
            LangChain chat model instance (with tools bound if provided)
            
        Raises:
            ValueError: If provider is unknown or API key is missing
            ImportError: If provider package is not installed
        """
        provider, model_name = self.parse_model_string(model_string)
        
        if provider not in PROVIDERS:
            available = ", ".join(PROVIDERS.keys())
            raise ValueError(
                f"Unknown provider '{provider}'. "
                f"Available: {available}"
            )
        
        config = PROVIDERS[provider]
        
        # Lazy import the provider package
        try:
            module = importlib.import_module(config["package"])
        except ImportError:
            raise ImportError(
                f"Provider '{provider}' requires package '{config['package']}'. "
                f"Install with: pip install {config['package'].replace('_', '-')}"
            )
        
        chat_class = getattr(module, config["class"])
        
        # Build constructor kwargs
        llm_kwargs: dict[str, Any] = {"model": model_name, **kwargs}
        
        # Get API key if required
        if config["key_service"]:
            api_key = self._get_api_key(config["key_service"])
            if not api_key:
                from atlas.config import API_KEY_CONFIGS
                key_config = API_KEY_CONFIGS.get(config["key_service"], {})
                url = key_config.get("url", "")
                env_var = key_config.get("env_var", f"{provider.upper()}_API_KEY")
                raise ValueError(
                    f"API key required for {provider}. "
                    f"Set via: atlas secrets set {config['key_service']} "
                    f"or export {env_var}. "
                    f"Get key at: {url}"
                )
            
            # Each provider uses a different kwarg name for the API key
            if provider == "openai":
                llm_kwargs["api_key"] = api_key
            elif provider == "anthropic":
                llm_kwargs["anthropic_api_key"] = api_key
            elif provider == "google":
                llm_kwargs["google_api_key"] = api_key
        
        # Create the LLM instance
        llm = chat_class(**llm_kwargs)
        
        # Bind tools if provided
        if tools:
            llm = llm.bind_tools(tools)
        
        # Track current state
        self.current_provider = provider
        self.current_model = model_name
        self._current_llm = llm
        
        logger.info(f"Model switched to {provider}:{model_name}")
        return llm
    
    def list_available_providers(self) -> list[dict[str, Any]]:
        """Return info about each provider and whether it's importable.
        
        Returns:
            List of dicts with provider name, label, installed status
        """
        result = []
        for name, config in PROVIDERS.items():
            installed = self._is_installed(config["package"])
            result.append({
                "name": name,
                "label": config["label"],
                "installed": installed,
                "package": config["package"].replace("_", "-"),
                "needs_key": bool(config["key_service"]),
            })
        return result
    
    def get_status(self) -> dict[str, Any]:
        """Return current model state and available providers."""
        current = None
        if self.current_provider and self.current_model:
            current = f"{self.current_provider}:{self.current_model}"
        
        available = [
            p["name"] for p in self.list_available_providers()
            if p["installed"]
        ]
        
        return {
            "current": current,
            "provider": self.current_provider,
            "model": self.current_model,
            "available_providers": available,
        }
    
    @staticmethod
    def _get_api_key(service: str) -> str | None:
        """Retrieve API key from keyring/env."""
        try:
            from atlas.security import get_api_key
            return get_api_key(service)
        except Exception:
            return None
    
    @staticmethod
    def _is_installed(package: str) -> bool:
        """Check if a package is importable without actually importing it."""
        try:
            importlib.import_module(package)
            return True
        except ImportError:
            return False
