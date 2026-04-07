"""LangGraph agent definition for Atlas."""

from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from .edges import route_after_tools, should_continue
from .nodes import memory_node, orchestrator_node, permission_node, SecureToolNode
from .state import AgentState


def create_agent_graph(
    tools: list | None = None,
    checkpointer=None,
) -> Any:
    """Create the LangGraph agent.
    
    Args:
        tools: List of tools available to the agent
        checkpointer: LangGraph checkpointer for persistence
        
    Returns:
        Compiled LangGraph ready for execution
    """
    # Initialize graph with state schema
    graph = StateGraph(AgentState)
    
    # Add nodes
    graph.add_node("orchestrator", orchestrator_node)
    graph.add_node("memory", memory_node)
    graph.add_node("permission", permission_node)
    
    # Add tool node if tools provided
    if tools:
        graph.add_node("tools", SecureToolNode(tools))
    
    # Set entry point
    graph.set_entry_point("orchestrator")
    
    # Add conditional edges from orchestrator
    graph.add_conditional_edges(
        "orchestrator",
        should_continue,
        {
            "tools": "tools" if tools else END,  # Route to END if no tools
            "memory": "memory",
            "permission": "permission",
            "end": END,
        }
    )
    
    # Memory returns to orchestrator
    graph.add_edge("memory", "orchestrator")
    
    # Permission returns to orchestrator
    graph.add_edge("permission", "orchestrator")
    
    # Tools route based on whether permissions are needed
    if tools:
        graph.add_conditional_edges(
            "tools",
            route_after_tools,
            {
                "orchestrator": "orchestrator",
                "permission": "permission",
            }
        )
    
    # Compile with optional checkpointer for persistence
    return graph.compile(checkpointer=checkpointer)


async def run_agent(
    graph,
    message: str,
    llm,
    memory_store=None,
    permission_manager=None,
    thread_id: str = "default",
    verbose: bool = False,
) -> str:
    """Run the agent with a user message.
    
    Args:
        graph: Compiled LangGraph agent
        message: User's input message
        llm: LLM instance for reasoning
        memory_store: Optional memory store for context
        permission_manager: Optional permission manager
        thread_id: Thread ID for conversation persistence
        verbose: Enable verbose logging of LLM responses and tool calls
        
    Returns:
        Agent's response as a string
    """
    from langchain_core.messages import HumanMessage
    from rich.console import Console
    from rich.panel import Panel
    from rich.syntax import Syntax
    
    console = Console()
    
    # Prepare input state
    input_state = {
        "messages": [HumanMessage(content=message)],
        "needs_memory_refresh": memory_store is not None,
    }
    
    # Configuration for nodes
    config = {
        "configurable": {
            "thread_id": thread_id,
            "llm": llm,
            "memory_store": memory_store,
            "permission_manager": permission_manager,
            "verbose": verbose,
        }
    }
    
    if not verbose:
        console.print("[dim]→ Starting graph execution...[/dim]")
    else:
        console.print("\n[bold cyan]═══ Graph Execution Started ═══[/bold cyan]\n")
    
    # Stream the graph execution to show progress
    # Use stream_mode="values" to get full state after each step
    final_state = None
    step_count = 0
    async for state in graph.astream(input_state, config, stream_mode="values"):
        step_count += 1
        final_state = state
        
        if verbose:
            # Show detailed state information
            messages = state.get("messages", [])
            if messages:
                last_msg = messages[-1]
                msg_type = last_msg.__class__.__name__ if hasattr(last_msg, "__class__") else "Unknown"
                
                console.print(f"\n[bold yellow]Step {step_count}:[/bold yellow] {msg_type}")
                
                # Show AI messages with tool calls
                if msg_type == "AIMessage":
                    if hasattr(last_msg, "content") and last_msg.content:
                        console.print(Panel(
                            last_msg.content,
                            title="[bold blue]🤖 LLM Response[/bold blue]",
                            border_style="blue",
                        ))
                    
                    # Show tool calls if present
                    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
                        for tool_call in last_msg.tool_calls:
                            tool_name = tool_call.get("name", "unknown")
                            tool_args = tool_call.get("args", {})
                            console.print(f"\n[bold magenta]🔧 Tool Call:[/bold magenta] {tool_name}")
                            if tool_args:
                                import json
                                args_json = json.dumps(tool_args, indent=2)
                                syntax = Syntax(args_json, "json", theme="monokai", line_numbers=False)
                                console.print(syntax)
                
                # Show tool messages (responses)
                elif msg_type == "ToolMessage":
                    if hasattr(last_msg, "content"):
                        tool_name = getattr(last_msg, "name", "unknown")
                        console.print(Panel(
                            last_msg.content,
                            title=f"[bold green]✓ Tool Response: {tool_name}[/bold green]",
                            border_style="green",
                        ))
    
    if not verbose:
        console.print("[dim]→ Graph execution complete[/dim]")
    else:
        console.print(f"\n[bold cyan]═══ Graph Execution Complete ({step_count} steps) ═══[/bold cyan]\n")
    
    # Extract the final AI response from state messages
    if final_state:
        messages = final_state.get("messages", [])
        if messages:
            # Walk backwards to find the last AIMessage with content.
            # This is more reliable than picking the longest message,
            # which could incorrectly return a verbose tool response
            # instead of the agent's final answer.
            for message in reversed(messages):
                # Check message type
                is_ai = False
                if hasattr(message, "__class__"):
                    is_ai = message.__class__.__name__ == "AIMessage"
                elif isinstance(message, dict):
                    is_ai = message.get("type") == "ai"
                
                if not is_ai:
                    continue
                
                # Extract content
                content = ""
                if hasattr(message, "content") and message.content:
                    content = message.content
                elif isinstance(message, dict) and message.get("content"):
                    content = message.get("content")
                
                if content:
                    return content
    
    return "I encountered an issue processing your request."
