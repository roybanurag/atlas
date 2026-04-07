"""Cross-platform directory path resolution for Atlas.

This module provides platform-aware directory paths following OS conventions:
- macOS: ~/Library/Application Support/atlas
- Windows: %LOCALAPPDATA%\\atlas (e.g., C:\\Users\\<user>\\AppData\\Local\\atlas)
- Linux: ~/.local/share/atlas
"""

from pathlib import Path

import platformdirs


def get_data_dir() -> Path:
    """Get platform-appropriate data directory.
    
    Returns:
        Path: Platform-specific data directory
            - macOS: ~/Library/Application Support/atlas
            - Windows: %LOCALAPPDATA%\\atlas
            - Linux: ~/.local/share/atlas
    """
    data_dir = Path(platformdirs.user_data_dir("atlas", appauthor=False))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_config_dir() -> Path:
    """Get platform-appropriate config directory.
    
    Returns:
        Path: Platform-specific config directory
            - macOS: ~/Library/Application Support/atlas
            - Windows: %LOCALAPPDATA%\\atlas
            - Linux: ~/.config/atlas
    """
    config_dir = Path(platformdirs.user_config_dir("atlas", appauthor=False))
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir
