"""Principles loader for Atlas agent."""

import importlib.resources
from pathlib import Path
from typing import Optional

from atlas.config.paths import get_config_dir


def load_principles() -> str:
    """Load the principles.md file containing agent guidelines.
    
    Searches for principles.md in the following locations (in order):
    1. atlas/config/principles.md (same directory as this module)
    2. ~/.config/atlas/principles.md (user config)
    
    Returns:
        The content of principles.md, or an empty string if not found
    """
    # Try package config directory first (atlas.config context)
    try:
        content = importlib.resources.read_text("atlas.config", "principles.md", encoding="utf-8")
        if content:
            return content
    except Exception:
        pass
    
    # Try user config
    user_principles = get_config_dir() / "principles.md"
    if user_principles.exists():
        try:
            return user_principles.read_text(encoding="utf-8")
        except Exception:
            pass
    
    return ""


def get_principles_path() -> Optional[Path]:
    """Get the path to the principles.md file.
    
    Returns:
        Path to principles.md if found, None otherwise
    """
    # Try package config directory first via importlib
    try:
        # Note: In modern Python, files() is preferred but read_text directly reads it.
        # We can extract the physical Path using as_file
        with importlib.resources.as_file(importlib.resources.files("atlas.config").joinpath("principles.md")) as p:
            if p.exists():
                return Path(p)
    except Exception:
        pass
    
    # Try user config
    user_principles = get_config_dir() / "principles.md"
    if user_principles.exists():
        return user_principles
    
    return None

