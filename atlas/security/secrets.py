"""Secure API key management using system keyring."""

import os
import json
from typing import Optional

from atlas.security.token_encryption import get_fernet
from atlas.config.paths import get_data_dir


class SecretManager:
    """Secure storage and retrieval of API keys and secrets.
    
    Uses a master-key encrypted local vault at `~/.local/share/atlas/secrets.enc`.
    The master key is securely stored in the OS keyring. This reduces
    prompts to a single master-key authentication per session.
    """
    
    def __init__(self):
        """Initialize the secret manager."""
        self.vault_path = get_data_dir() / "secrets.enc"
        self._vault_cache: Optional[dict] = None

    def _load_vault(self) -> dict:
        """Load and decrypt the vault from disk into memory."""
        if self._vault_cache is not None:
            return self._vault_cache
            
        if not self.vault_path.exists():
            self._vault_cache = {}
            return self._vault_cache
            
        try:
            fernet = get_fernet()
            encrypted_blob = self.vault_path.read_bytes()
            raw_json = fernet.decrypt(encrypted_blob)
            self._vault_cache = json.loads(raw_json)
        except Exception as e:
            print(f"Failed to load encrypted vault: {e}")
            self._vault_cache = {}
            
        return self._vault_cache

    def _save_vault(self):
        """Encrypt and save the memory dictionary to disk."""
        if self._vault_cache is None:
            return
            
        try:
            fernet = get_fernet()
            raw_json = json.dumps(self._vault_cache).encode("utf-8")
            encrypted_blob = fernet.encrypt(raw_json)
            self.vault_path.write_bytes(encrypted_blob)
        except Exception as e:
            print(f"Failed to save encrypted vault: {e}")
            
    def get_secret(self, key_name: str, fallback_env: Optional[str] = None) -> Optional[str]:
        """Get a secret from the encrypted vault or environment variable.
        
        Args:
            key_name: Name of the secret (e.g., "tavily_api_key")
            fallback_env: Optional environment variable name to check
            
        Returns:
            The secret value or None if not found
        """
        vault = self._load_vault()
        if key_name in vault:
            return vault[key_name]
        
        # Fall back to environment variable
        if fallback_env:
            val = os.getenv(fallback_env)
            if val:
                # Security: Explicitly scrub from os.environ immediately after reading
                os.environ.pop(fallback_env, None)
                return val
        
        return None

    def set_secret(self, key_name: str, value: str) -> bool:
        """Store a secret in the encrypted vault.
        
        Args:
            key_name: Name of the secret
            value: Secret value to store
            
        Returns:
            True if successful
        """
        vault = self._load_vault()
        vault[key_name] = value
        self._save_vault()
        return True

    def delete_secret(self, key_name: str) -> bool:
        """Delete a secret from the vault.
        
        Args:
            key_name: Name of the secret to delete
            
        Returns:
            True if successful, False if not found
        """
        vault = self._load_vault()
        if key_name in vault:
            del vault[key_name]
            self._save_vault()
            return True
        return False

    def list_secrets(self) -> list[str]:
        """List all stored secret names from the vault.
        
        Returns:
            List of secret names
        """
        vault = self._load_vault()
        return list(vault.keys())


# Import API key configurations from central config
from atlas.config import API_KEY_CONFIGS


def get_api_key(service: str, secret_manager: Optional[SecretManager] = None) -> Optional[str]:
    """Get an API key for a service.
    
    Convenience function that handles the common pattern of getting API keys
    with proper fallback to environment variables.
    
    Args:
        service: Service name (e.g., "tavily", "openai")
        secret_manager: Optional SecretManager instance (creates one if not provided)
        
    Returns:
        API key or None if not found
    """
    if secret_manager is None:
        secret_manager = SecretManager()
    
    config = API_KEY_CONFIGS.get(service)
    if not config:
        # Unknown service, try environment variable
        env_var = f"{service.upper()}_API_KEY"
        return os.getenv(env_var)
    
    return secret_manager.get_secret(
        config["keyring_name"],
        fallback_env=config["env_var"]
    )


def set_api_key(
    service: str,
    api_key: str,
    secret_manager: Optional[SecretManager] = None
) -> bool:
    """Set an API key for a service.
    
    Args:
        service: Service name (e.g., "tavily", "openai")
        api_key: The API key to store
        secret_manager: Optional SecretManager instance
        
    Returns:
        True if successful
    """
    if secret_manager is None:
        secret_manager = SecretManager()
    
    config = API_KEY_CONFIGS.get(service)
    if not config:
        raise ValueError(f"Unknown service: {service}")
    
    return secret_manager.set_secret(config["keyring_name"], api_key)
