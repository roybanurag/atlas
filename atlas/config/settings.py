"""Settings and configuration for Atlas.

DEPRECATED: Use atlas.config.paths directly.
This module is kept for backward compatibility.
"""

from atlas.config.paths import get_config_dir, get_data_dir

__all__ = ["get_data_dir", "get_config_dir"]

