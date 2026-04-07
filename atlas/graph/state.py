"""Agent state schema for LangGraph."""

from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    """State schema for the Atlas agent.
    
    This state is passed between nodes in the LangGraph and maintains
    context throughout the agent's execution.
    
    Using TypedDict instead of Pydantic BaseModel for LangGraph compatibility.
    """
    
    # Conversation messages with automatic message merging
    messages: Annotated[list, add_messages]
    
    # Memory context retrieved from long-term storage
    memory_context: list[str]
    
    # Permission state
    permissions_granted: dict[str, bool]
    
    # Current task being worked on
    current_task: str | None
    
    # Flag to trigger memory refresh
    needs_memory_refresh: bool
    
    # Pending actions that require user confirmation
    pending_confirmations: list[dict[str, Any]]
