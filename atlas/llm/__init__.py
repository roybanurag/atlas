"""LLM module for Atlas."""

from .local import OllamaAdapter
from .router import ModelRouter, PrivacyLevel

__all__ = [
    "OllamaAdapter",
    "ModelRouter",
    "PrivacyLevel",
]
