"""Model router with privacy controls."""

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage

from atlas.config import PrivacyLevel


class ModelRouter:
    """Routes requests to appropriate LLM based on privacy requirements.
    
    By default, all requests go to local LLMs. Remote LLMs are only
    used when explicitly allowed and for complex reasoning tasks.
    """
    
    def __init__(
        self,
        local_llm: Any,
        remote_llm: Any | None = None,
        default_privacy: PrivacyLevel = PrivacyLevel.LOCAL_ONLY,
    ):
        """Initialize router.
        
        Args:
            local_llm: Local LLM instance (Ollama, llama.cpp, etc.)
            remote_llm: Optional remote LLM instance (OpenAI, Anthropic)
            default_privacy: Default privacy level for requests
        """
        self.local_llm = local_llm
        self.remote_llm = remote_llm
        self.default_privacy = default_privacy
    
    async def route(
        self,
        messages: list[BaseMessage],
        privacy_level: PrivacyLevel | None = None,
        complexity: str = "medium",
        **kwargs,
    ) -> AIMessage:
        """Route messages to appropriate LLM.
        
        Args:
            messages: Messages to send
            privacy_level: Override privacy level
            complexity: Task complexity (low, medium, high)
            **kwargs: Additional args for LLM
            
        Returns:
            AI response message
        """
        level = privacy_level or self.default_privacy
        
        # Local-only or sensitive: always use local
        if level in (PrivacyLevel.LOCAL_ONLY, PrivacyLevel.SENSITIVE):
            return await self._invoke_local(messages, **kwargs)
        
        # Remote allowed: choose based on complexity
        if level == PrivacyLevel.REMOTE_ALLOWED:
            if complexity == "high" and self.remote_llm:
                return await self._invoke_remote(messages, **kwargs)
            return await self._invoke_local(messages, **kwargs)
        
        # Default to local
        return await self._invoke_local(messages, **kwargs)
    
    async def _invoke_local(
        self,
        messages: list[BaseMessage],
        **kwargs,
    ) -> AIMessage:
        """Invoke local LLM."""
        if hasattr(self.local_llm, "ainvoke"):
            return await self.local_llm.ainvoke(messages, **kwargs)
        elif hasattr(self.local_llm, "invoke"):
            return self.local_llm.invoke(messages, **kwargs)
        else:
            raise ValueError("Local LLM must have invoke or ainvoke method")
    
    async def _invoke_remote(
        self,
        messages: list[BaseMessage],
        **kwargs,
    ) -> AIMessage:
        """Invoke remote LLM."""
        if not self.remote_llm:
            # Fallback to local if remote not available
            return await self._invoke_local(messages, **kwargs)
        
        if hasattr(self.remote_llm, "ainvoke"):
            return await self.remote_llm.ainvoke(messages, **kwargs)
        elif hasattr(self.remote_llm, "invoke"):
            return self.remote_llm.invoke(messages, **kwargs)
        else:
            raise ValueError("Remote LLM must have invoke or ainvoke method")
    
    def set_privacy(self, level: PrivacyLevel):
        """Set default privacy level."""
        self.default_privacy = level
    
    @property
    def is_remote_available(self) -> bool:
        """Check if remote LLM is configured."""
        return self.remote_llm is not None
