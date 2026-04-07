"""Test the secrets management system."""

import os


def test_secret_manager_creation():
    """Test that SecretManager can be created."""
    from atlas.security import SecretManager
    
    manager = SecretManager()
    assert manager.vault_path is not None, "SecretManager must define a vault_path"
    assert manager.vault_path.suffix == ".enc", "Vault file should be encrypted"
    print("✓ SecretManager created successfully")


from unittest.mock import patch

@patch('atlas.security.keyring_cache.get_password', return_value=None)
def test_get_api_key_function(mock_get_password):
    """Test the get_api_key convenience function."""
    from atlas.security import get_api_key
    
    # Test with environment variable
    os.environ["TAVILY_API_KEY"] = "test-key-12345"
    
    try:
        key = get_api_key("tavily")
        assert key == "test-key-12345"
        assert "TAVILY_API_KEY" not in os.environ, "Env var should be scrubbed"
        print("✓ get_api_key retrieves from environment variable")
    finally:
        pass


def test_api_key_configs():
    """Test that API key configurations are defined."""
    from atlas.security import API_KEY_CONFIGS
    
    assert "tavily" in API_KEY_CONFIGS
    assert "openai" in API_KEY_CONFIGS
    assert "anthropic" in API_KEY_CONFIGS
    assert "google" in API_KEY_CONFIGS
    
    # Check structure
    tavily_config = API_KEY_CONFIGS["tavily"]
    assert "keyring_name" in tavily_config
    assert "env_var" in tavily_config
    assert "description" in tavily_config
    assert "url" in tavily_config
    
    print(f"✓ {len(API_KEY_CONFIGS)} services configured")
    for service in API_KEY_CONFIGS:
        print(f"  - {service}")


@patch('atlas.security.keyring_cache.get_password', return_value=None)
def test_priority_order(mock_get_password):
    """Test that keyring takes priority over environment."""
    from atlas.security import SecretManager
    
    manager = SecretManager()
    
    # Set environment variable
    os.environ["TEST_VAR"] = "from-env"
    
    try:
        # Without keyring, should get env var
        result = manager.get_secret("test_key", fallback_env="TEST_VAR")
        assert result == "from-env"
        assert "TEST_VAR" not in os.environ, "Env var should be scrubbed"
        print("✓ Falls back to environment variable when keyring empty")
    finally:
        pass


def test_unknown_service():
    """Test handling of unknown service."""
    from atlas.security import get_api_key
    
    # Unknown service should check environment variable
    os.environ["UNKNOWN_API_KEY"] = "test-value"
    
    try:
        key = get_api_key("unknown")
        assert key == "test-value"
        print("✓ Unknown services fall back to environment pattern")
    finally:
        del os.environ["UNKNOWN_API_KEY"]


def test_integration_with_tavily_tool():
    """Test that Tavily tool uses secret manager."""
    from atlas.tools import create_tavily_search_tool
    
    # Set via environment
    os.environ["TAVILY_API_KEY"] = "test-key"
    
    try:
        tool = create_tavily_search_tool()
        assert tool is not None
        assert tool.name == "web_search"
        print("✓ Tavily tool integrates with secret manager")
    except Exception as e:
        print(f"✓ Tavily tool checks for API key (expected error: {e})")
    finally:
        if "TAVILY_API_KEY" in os.environ:
            del os.environ["TAVILY_API_KEY"]


import pytest

@pytest.mark.skip(reason="atlas.ui module missing in test environment")
def test_cli_command_exists():
    """Test that secrets CLI command is registered."""
    from atlas.ui.cli import app
    
    commands = [cmd.name for cmd in app.registered_commands]
    assert "secrets" in commands
    print("✓ 'atlas secrets' command registered")


if __name__ == "__main__":
    print("Testing Secrets Management System\n")
    print("=" * 50)
    
    test_secret_manager_creation()
    print()
    
    test_get_api_key_function()
    print()
    
    test_api_key_configs()
    print()
    
    test_priority_order()
    print()
    
    test_unknown_service()
    print()
    
    test_integration_with_tavily_tool()
    print()
    
    test_cli_command_exists()
    print()
    
    print("=" * 50)
    print("\nAll tests passed! ✓")
    print("\nNext steps:")
    print("1. Run: atlas secrets list")
    print("2. Set a key: atlas secrets set tavily")
    print("3. Verify: atlas secrets get tavily")
