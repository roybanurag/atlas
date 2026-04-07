"""Unified tool loader for Atlas.

Loads all available tools with graceful fallbacks — used by both
the CLI chat command and the Slack bot. Single source of truth
for what tools are available and how they're initialized.
"""

from pathlib import Path
from typing import Callable, Optional

from rich.console import Console


# Each entry: (display_name, loader_callable)
# Loaders that need `data_dir` are wrapped at call time.
def _build_tool_registry(data_dir: Path, gateway=None) -> list[tuple[str, Callable]]:
    """Build the list of (name, loader) pairs for all tools."""
    from atlas.tools import (
        create_briefing_tool,
        create_calendar_tools,
        create_drive_tools,
        create_gmail_tools,
        create_google_tasks_tools,
        create_notes_tools,
        create_tavily_search_tool,
        create_web_reader_tool,
    )
    from atlas.tools.sandbox import create_sandbox_tools
    
    return [
        ("Web search",    lambda: [create_tavily_search_tool(gateway=gateway)]),
        ("Web reader",    lambda: create_web_reader_tool()),
        ("Python Sandbox",lambda: create_sandbox_tools()),
        ("Gmail",         lambda: create_gmail_tools()),
        ("Calendar",      lambda: create_calendar_tools()),
        ("Google Drive",  lambda: create_drive_tools()),
        ("Google Tasks",  lambda: create_google_tasks_tools()),
        ("Notes",         lambda: create_notes_tools(data_dir)),
        ("Briefing",      lambda: create_briefing_tool(data_dir)),
    ]


def load_all_tools(
    data_dir: Path,
    console: Optional[Console] = None,
    gateway=None,
    deny_list: Optional[list[str]] = None,
) -> list:
    """Load all available tools, skipping any that fail or are denied.
    
    Args:
        data_dir: Atlas data directory (needed by Notes, Briefing).
        console: Optional Rich console for status output.
        gateway: Optional API gateway for tools that need it.
        deny_list: Optional list of tool display names to skip.
    
    Returns:
        Flat list of LangChain tool instances.
    """
    registry = _build_tool_registry(data_dir, gateway=gateway)
    tools = []
    deny_set = set(deny_list or [])
    
    for name, loader in registry:
        # Skip denied tools
        if name in deny_set:
            if console:
                console.print(f"[dim]  ✗ {name} denied by security config[/dim]")
            continue
        
        try:
            result = loader()
            if isinstance(result, list):
                tools.extend(result)
                count = len(result)
            else:
                tools.append(result)
                count = 1
            
            if console:
                console.print(f"[dim]  ✓ {name} loaded ({count} tool{'s' if count != 1 else ''})[/dim]")
        except Exception as e:
            if console:
                console.print(f"[dim]  ⚠ {name} not available: {e}[/dim]")
    
    return tools
