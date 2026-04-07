"""Unified token encryption key management.

Uses a single master encryption key for all OAuth tokens, stored in the system
keyring. This reduces keychain prompts to a single access per session.
"""

import base64
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet

from atlas.security import keyring_cache


# Constants
SERVICE_NAME = "atlas-agent"
MASTER_KEY_NAME = "master-encryption-key"

# Module-level cache for the master key (loaded once per process)
_master_key: Optional[bytes] = None
_key_loaded = False


def _load_master_key() -> bytes:
    """Load or create the master encryption key from keyring.
    
    This is called once per process and cached in memory.
    """
    global _master_key, _key_loaded
    
    if _key_loaded and _master_key:
        return _master_key
    
    # Try to get existing key from keyring (via cache)
    key_b64 = keyring_cache.get_password(SERVICE_NAME, MASTER_KEY_NAME)
    
    if key_b64:
        _master_key = base64.b64decode(key_b64)
    else:
        # Generate new key
        _master_key = Fernet.generate_key()
        key_b64 = base64.b64encode(_master_key).decode('utf-8')
        
        # Store in keyring (via cache)
        try:
            keyring_cache.set_password(SERVICE_NAME, MASTER_KEY_NAME, key_b64)
        except Exception as e:
            print(f"Warning: Failed to store master key in keyring: {e}")
    
    _key_loaded = True
    return _master_key


def get_encryption_key() -> bytes:
    """Get the master encryption key for token encryption.
    
    This function is called by all Google API tools (Gmail, Calendar, Drive)
    to encrypt/decrypt OAuth tokens. Using a single master key means only
    ONE keychain access per session.
    
    Returns:
        bytes: The Fernet encryption key
    """
    return _load_master_key()


def get_fernet() -> Fernet:
    """Get a Fernet instance using the master encryption key.
    
    Returns:
        Fernet: Ready-to-use Fernet instance for encryption/decryption
    """
    return Fernet(get_encryption_key())


def preload_key():
    """Pre-load the master key into cache.
    
    Call this at CLI startup to trigger any keychain prompts early,
    before actual tool usage.
    """
    _load_master_key()
