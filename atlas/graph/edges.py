"""Conditional edge logic for LangGraph routing."""

from typing import Any, Literal

from langchain_core.messages import AIMessage


def should_continue(state: dict[str, Any]) -> Literal["tools", "memory", "permission", "end"]:
    """Determine next step based on agent state.
    
    Routes to:
    - "tools": If the LLM requested tool calls
    - "memory": If memory context needs refresh
    - "permission": If there are pending permission requests
    - "end": If the response is complete or max tool rounds reached
    """
    from rich.console import Console
    console = Console()
    
    MAX_TOOL_ROUNDS = 15
    
    messages = state.get("messages", [])
    
    if not messages:
        console.print("[dim]→ Router: No messages, ending[/dim]")
        return "end"
    
    last_message = messages[-1]
    
    # Check for pending permission requests first (highest priority)
    if state.get("pending_confirmations"):
        console.print("[dim]→ Router: Pending permissions → permission node[/dim]")
        return "permission"
    
    # Enforce tool-call loop limit
    tool_rounds = state.get("tool_rounds", 0)
    if tool_rounds >= MAX_TOOL_ROUNDS:
        console.print(f"[yellow]⚠ Router: Maximum tool rounds ({MAX_TOOL_ROUNDS}) reached → ending[/yellow]")
        return "end"
    
    # Check if LLM requested tool calls
    if isinstance(last_message, AIMessage):
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            console.print(f"[dim]→ Router: {len(last_message.tool_calls)} tool calls → tools node (round {tool_rounds + 1}/{MAX_TOOL_ROUNDS})[/dim]")
            return "tools"
    elif isinstance(last_message, dict):
        if last_message.get("tool_calls"):
            console.print("[dim]→ Router: Tool calls requested → tools node[/dim]")
            return "tools"
    
    # Check if we need to refresh memory context
    if state.get("needs_memory_refresh"):
        console.print("[dim]→ Router: Memory refresh needed → memory node[/dim]")
        return "memory"
    
    # Otherwise, we're done
    console.print("[dim]→ Router: Response complete → ending[/dim]")
    return "end"


def route_after_tools(state: dict[str, Any]) -> Literal["orchestrator", "permission"]:
    """Route after tool execution.
    
    If tools generated permission requests, go to permission node.
    Otherwise, return to orchestrator for next reasoning step.
    Increments the tool_rounds counter to enforce the loop limit.
    """
    # Increment tool round counter
    state["tool_rounds"] = state.get("tool_rounds", 0) + 1
    
    if state.get("pending_confirmations"):
        return "permission"
    return "orchestrator"
