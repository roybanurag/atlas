"""Tests for ModelManager — provider:model parsing, creation, and listing."""

import pytest
from unittest.mock import patch, MagicMock

from atlas.config.model_manager import ModelManager, PROVIDERS


class TestParseModelString:
    """Tests for parsing provider:model strings."""
    
    def test_explicit_ollama(self):
        mgr = ModelManager()
        provider, model = mgr.parse_model_string("ollama:qwen3:14b")
        assert provider == "ollama"
        assert model == "qwen3:14b"
    
    def test_explicit_openai(self):
        mgr = ModelManager()
        provider, model = mgr.parse_model_string("openai:gpt-4o")
        assert provider == "openai"
        assert model == "gpt-4o"
    
    def test_explicit_anthropic(self):
        mgr = ModelManager()
        provider, model = mgr.parse_model_string("anthropic:claude-sonnet-4-20250514")
        assert provider == "anthropic"
        assert model == "claude-sonnet-4-20250514"
    
    def test_explicit_google(self):
        mgr = ModelManager()
        provider, model = mgr.parse_model_string("google:gemini-2.5-flash")
        assert provider == "google"
        assert model == "gemini-2.5-flash"
    
    def test_bare_model_defaults_to_ollama(self):
        mgr = ModelManager()
        provider, model = mgr.parse_model_string("qwen3:14b")
        assert provider == "ollama"
        assert model == "qwen3:14b"
    
    def test_bare_model_no_tag(self):
        mgr = ModelManager()
        provider, model = mgr.parse_model_string("llama3.1")
        assert provider == "ollama"
        assert model == "llama3.1"
    
    def test_custom_default_provider(self):
        mgr = ModelManager(default_provider="openai")
        provider, model = mgr.parse_model_string("gpt-4o")
        assert provider == "openai"
        assert model == "gpt-4o"
    
    def test_whitespace_stripped(self):
        mgr = ModelManager()
        provider, model = mgr.parse_model_string("  openai:gpt-4o  ")
        assert provider == "openai"
        assert model == "gpt-4o"
    
    def test_empty_model_after_prefix_defaults(self):
        """If someone types just 'openai:', treat as bare string."""
        mgr = ModelManager()
        provider, model = mgr.parse_model_string("openai:")
        assert provider == "ollama"  # No model after prefix, so not matched
        assert model == "openai:"


class TestCreateLlm:
    """Tests for LLM creation."""
    
    def test_creates_ollama(self):
        mgr = ModelManager()
        llm = mgr.create_llm("qwen3:14b")
        assert mgr.current_provider == "ollama"
        assert mgr.current_model == "qwen3:14b"
    
    def test_bare_unknown_model_uses_default(self):
        """Unknown prefix is treated as an Ollama model name, not an error."""
        mgr = ModelManager()
        # 'fakeprovider:model' → ollama provider, model='fakeprovider:model'
        provider, model = mgr.parse_model_string("fakeprovider:model")
        assert provider == "ollama"
        assert model == "fakeprovider:model"
    
    def test_missing_package_raises(self):
        mgr = ModelManager()
        with patch("importlib.import_module", side_effect=ImportError("nope")):
            with pytest.raises(ImportError, match="requires package"):
                mgr.create_llm("openai:gpt-4o")
    
    def test_missing_api_key_raises(self):
        """Remote providers should raise if API key is not found."""
        mgr = ModelManager()
        
        # Mock the import to succeed with a fake module
        mock_module = MagicMock()
        mock_chat_class = MagicMock()
        mock_module.ChatOpenAI = mock_chat_class
        
        with patch("importlib.import_module", return_value=mock_module):
            with patch.object(ModelManager, "_get_api_key", return_value=None):
                with pytest.raises(ValueError, match="API key required"):
                    mgr.create_llm("openai:gpt-4o")
    
    def test_create_without_tools(self):
        """Creating without tools should still track state."""
        mgr = ModelManager()
        llm = mgr.create_llm("ollama:llama3.1")
        assert mgr.current_provider == "ollama"
        assert mgr.current_model == "llama3.1"
    
    def test_tracks_current_state(self):
        mgr = ModelManager()
        mgr.create_llm("ollama:llama3.1")
        assert mgr.current_provider == "ollama"
        assert mgr.current_model == "llama3.1"
        
        mgr.create_llm("ollama:qwen3:14b")
        assert mgr.current_model == "qwen3:14b"


class TestListProviders:
    """Tests for provider listing."""
    
    def test_lists_all_providers(self):
        mgr = ModelManager()
        providers = mgr.list_available_providers()
        names = [p["name"] for p in providers]
        assert "ollama" in names
        assert "openai" in names
        assert "anthropic" in names
        assert "google" in names
    
    def test_ollama_is_installed(self):
        mgr = ModelManager()
        providers = mgr.list_available_providers()
        ollama = next(p for p in providers if p["name"] == "ollama")
        assert ollama["installed"] is True
        assert ollama["needs_key"] is False
    
    def test_remote_providers_need_key(self):
        mgr = ModelManager()
        providers = mgr.list_available_providers()
        for p in providers:
            if p["name"] != "ollama":
                assert p["needs_key"] is True


class TestGetStatus:
    """Tests for status reporting."""
    
    def test_initial_status_is_none(self):
        mgr = ModelManager()
        status = mgr.get_status()
        assert status["current"] is None
        assert status["provider"] is None
    
    def test_status_after_create(self):
        mgr = ModelManager()
        mgr.create_llm("qwen3:14b")
        status = mgr.get_status()
        assert status["current"] == "ollama:qwen3:14b"
        assert status["provider"] == "ollama"
        assert status["model"] == "qwen3:14b"
        assert "ollama" in status["available_providers"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
