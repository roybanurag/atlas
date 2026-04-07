"""Thread-safe in-memory cache for keyring values.

This module provides a caching layer for system keyring access to reduce
password prompts during a session. Works with:
- macOS Keychain
- Windows Credential Manager  
- Linux Secret Service (via keyring library)

Values are cached in memory and cleared when the process exits.
"""

import threading
from typing import Optional

import keyring

class KeyringCache:
    """Thread-safe singleton cache for keyring values.
    
    Caches keyring.get_password() results in memory to avoid repeated
    system keyring password prompts within a single session.
    
    Security notes:
    - Cache is cleared when process exits
    - Thread-safe for concurrent access
    - Only caches successfully retrieved values
    """
    
    _instance: Optional['KeyringCache'] = None
    _lock = threading.Lock()
    
    def __init__(self):
        """Initialize the cache. Use get_instance() instead."""
        self._cache: dict[str, str] = {}
        self._cache_lock = threading.Lock()
    
    @classmethod
    def get_instance(cls) -> 'KeyringCache':
        """Get the singleton instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance
    
    def get_password(self, service_name: str, key_name: str) -> Optional[str]:
        """Get password from cache or keyring.
        
        Args:
            service_name: Service name for keyring storage
            key_name: Key name within the service
            
        Returns:
            Password value or None if not found
        """
        cache_key = f"{service_name}:{key_name}"
        
        # Check cache first
        with self._cache_lock:
            if cache_key in self._cache:
                return self._cache[cache_key]
        
        # Fetch from keyring (system native abstraction)
        try:
            value = keyring.get_password(service_name, key_name)
                
            if value:
                # Cache the value
                with self._cache_lock:
                    self._cache[cache_key] = value
            return value
        except Exception:
            return None
    
    def set_password(self, service_name: str, key_name: str, value: str) -> bool:
        """Set password in keyring and cache.
        
        Args:
            service_name: Service name for keyring storage
            key_name: Key name within the service
            value: Password value to store
            
        Returns:
            True if successful, False otherwise
        """
        cache_key = f"{service_name}:{key_name}"
        
        try:
            # Store in keyring natively
            keyring.set_password(service_name, key_name, value)
            
            # Update cache
            with self._cache_lock:
                self._cache[cache_key] = value
            
            return True
        except Exception:
            return False
    
    def delete_password(self, service_name: str, key_name: str) -> bool:
        """Delete password from keyring and cache.
        
        Args:
            service_name: Service name for keyring storage
            key_name: Key name within the service
            
        Returns:
            True if successful, False otherwise
        """
        cache_key = f"{service_name}:{key_name}"
        
        # Remove from cache
        with self._cache_lock:
            self._cache.pop(cache_key, None)
        
        try:
            # Delete from keyring natively
            keyring.delete_password(service_name, key_name)
            
            return True
        except Exception:
            return False
    
    def clear_cache(self):
        """Clear the in-memory cache.
        
        Useful for testing or when you want to force re-authentication.
        """
        with self._cache_lock:
            self._cache.clear()
    
    def invalidate(self, service_name: str, key_name: str):
        """Invalidate a specific cache entry.
        
        Args:
            service_name: Service name for keyring storage
            key_name: Key name within the service
        """
        cache_key = f"{service_name}:{key_name}"
        with self._cache_lock:
            self._cache.pop(cache_key, None)


# Convenience functions for direct use
_cache = KeyringCache.get_instance()


def get_password(service_name: str, key_name: str) -> Optional[str]:
    """Get password from cache or keyring.
    
    Convenience function that uses the singleton cache instance.
    
    Args:
        service_name: Service name for keyring storage
        key_name: Key name within the service
        
    Returns:
        Password value or None if not found
    """
    return _cache.get_password(service_name, key_name)


def set_password(service_name: str, key_name: str, value: str) -> bool:
    """Set password in keyring and cache.
    
    Convenience function that uses the singleton cache instance.
    
    Args:
        service_name: Service name for keyring storage
        key_name: Key name within the service
        value: Password value to store
        
    Returns:
        True if successful, False otherwise
    """
    return _cache.set_password(service_name, key_name, value)


def delete_password(service_name: str, key_name: str) -> bool:
    """Delete password from keyring and cache.
    
    Convenience function that uses the singleton cache instance.
    
    Args:
        service_name: Service name for keyring storage
        key_name: Key name within the service
        
    Returns:
        True if successful, False otherwise
    """
    return _cache.delete_password(service_name, key_name)


def clear_cache():
    """Clear the in-memory cache.
    
    Convenience function that uses the singleton cache instance.
    """
    _cache.clear_cache()
