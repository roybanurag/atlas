"""LangGraph nodes for the Atlas agent."""

import logging
from typing import Any

logger = logging.getLogger(__name__)

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import ToolNode

from atlas.config import load_principles
from atlas.security.permissions import PermissionManager


async def orchestrator_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Main reasoning node - decides next action.
    
    This node:
    1. Receives the current state with messages and memory context
    2. Invokes the LLM to decide the next action
    3. Returns updated messages with the LLM response
    """
    from rich.console import Console
    from rich.panel import Panel
    console = Console()
    
    configurable = config.get("configurable", {})
    llm = configurable.get("llm")
    verbose = configurable.get("verbose", False)
    
    if not llm:
        return {"messages": [AIMessage(content="Error: No LLM configured")]}
    
    # Show what we're doing
    if verbose:
        console.print("\n[bold cyan]🤔 Orchestrator: Thinking...[/bold cyan]")
    else:
        console.print("\n[dim]→ Orchestrator: Processing request...[/dim]")
    
    # Load principles and guidelines
    principles = load_principles()
    
    # Build system prompt with principles
    system_prompt = (
        "You are Atlas, a privacy-focused personal AI assistant. "
        "You prioritize user privacy and security. Always request permission before "
        "accessing sensitive resources.\n\n"
    )
    
    if principles:
        if verbose:
            console.print("[dim]  • Including agent principles[/dim]")
        system_prompt += f"{principles}\n\n"
    
    # Add memory context
    memory_context = "\n".join(state.get("memory_context", []) or [])
    if memory_context:
        if verbose:
            console.print(f"[dim]  • Including {len(state.get('memory_context', []))} memory items[/dim]")
        elif not verbose:
            console.print(f"[dim]→ Using {len(state.get('memory_context', []))} memory context items[/dim]")
        
        system_prompt += f"Relevant context from memory:\n{memory_context}\n\n"
    
    if state.get("current_task"):
        if verbose:
            console.print(f"[dim]  • Current task: {state['current_task']}[/dim]")
        system_prompt += f"Current task: {state['current_task']}\n"
    
    # Check guardrails
    from atlas.security.guardrails import GuardrailEngine
    guardrails = GuardrailEngine()
    
    if state.get("messages"):
        last_msg = state["messages"][-1]
        msg_content = ""
        from langchain_core.messages import HumanMessage
        if isinstance(last_msg, HumanMessage):
            msg_content = last_msg.content
        elif isinstance(last_msg, dict) and last_msg.get("role") == "user":
            msg_content = str(last_msg.get("content", ""))
            
        if msg_content:
            action = {"type": "user_input", "text": msg_content}
            allowed, violation_msg = guardrails.evaluate_sync(action)
            if not allowed:
                console.print(f"[red]⚠ Security blocked input: {violation_msg}[/red]")
                return {"messages": [AIMessage(content=f"I cannot process that request: {violation_msg}")]}

    # Prepare messages for LLM
    messages = [SystemMessage(content=system_prompt)] + list(state.get("messages", []) or [])
    
    if verbose:
        console.print("[dim]  • Invoking LLM...[/dim]")
    else:
        console.print("[dim]→ Invoking LLM...[/dim]")
    
    # Get LLM response
    response = await llm.ainvoke(messages)
    
    # Show what the LLM said
    if hasattr(response, "content"):
        # In verbose mode, run_agent handles the full display
        # In non-verbose, we show a preview
        if not verbose:
            preview = response.content[:200] + "..." if len(response.content) > 200 else response.content
            console.print(f"[dim]→ LLM response: {preview}[/dim]")
    
    return {"messages": [response]}


async def memory_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Retrieve relevant memories for current context.
    
    This node queries the memory store using semantic search to find
    relevant past conversations and knowledge that might help with
    the current request. Uses token-efficient context formatting.
    """
    from rich.console import Console
    console = Console()
    
    configurable = config.get("configurable", {})
    memory_store = configurable.get("memory_store")
    verbose = configurable.get("verbose", False)
    
    if not memory_store:
        if verbose:
            console.print("[dim]  • Memory: No memory store configured[/dim]")
        else:
            console.print("[dim]→ Memory: No memory store configured[/dim]")
        return {"needs_memory_refresh": False}
    
    # Get the last user message to use as query
    messages = state.get("messages", []) or []
    if not messages:
        return {"needs_memory_refresh": False}
    
    last_message = messages[-1]
    query = ""
    
    if isinstance(last_message, HumanMessage):
        query = last_message.content
    elif isinstance(last_message, dict) and last_message.get("role") == "user":
        query = last_message.get("content", "")
    elif hasattr(last_message, "content"):
        query = last_message.content
    
    if not query:
        return {"needs_memory_refresh": False}
    
    if verbose:
        console.print(f"[dim]  • Memory: Searching for '{query[:50]}...'[/dim]")
    else:
        console.print(f"[dim]→ Memory: Searching for relevant context...[/dim]")
    
    # Auto-compaction: summarize old messages if approaching context limit
    try:
        if hasattr(memory_store, 'needs_compaction') and memory_store.needs_compaction():
            llm = configurable.get("llm")
            summary = await memory_store.compact(llm=llm)
            if summary:
                console.print("[dim]→ Memory: Compacted old messages into summary[/dim]")
        
        # Prune large tool results in older messages
        if hasattr(memory_store, 'prune_tool_results'):
            memory_store.prune_tool_results()
    except Exception as e:
        logger.warning(f"Memory maintenance failed: {e}")
    
    # Retrieve relevant memories using semantic search
    try:
        memories = await memory_store.recall(query, n_results=5)
        
        # Format memories for context injection (token-efficient)
        memory_context = []
        for m in memories:
            content = m.get("content", "")
            if content:
                # Already formatted compactly by the memory store
                memory_context.append(content)
        
        if memory_context:
            # Check if embeddings are being used
            sources = set(m.get("metadata", {}).get("source", "unknown") for m in memories)
            source_info = ", ".join(sources)
            
            if verbose:
                console.print(f"[dim]  • Memory: Found {len(memory_context)} relevant items ({source_info})[/dim]")
            else:
                console.print(f"[dim]→ Memory: Found {len(memory_context)} relevant items[/dim]")
        else:
            if verbose:
                console.print("[dim]  • Memory: No relevant memories found[/dim]")
            else:
                console.print("[dim]→ Memory: No relevant memories found[/dim]")
        
        return {
            "memory_context": memory_context,
            "needs_memory_refresh": False
        }
    except Exception as e:
        console.print(f"[dim]→ Memory: Retrieval failed ({str(e)})[/dim]")
        # If memory retrieval fails, continue without context
        return {"needs_memory_refresh": False}



async def permission_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Handle permission requests for sensitive actions.
    
    This node is invoked when the agent needs to perform an action
    that requires explicit user permission.
    """
    configurable = config.get("configurable", {})
    permission_manager = configurable.get("permission_manager")
    pending = state.get("pending_confirmations", []) or []
    
    if not pending or not permission_manager:
        return {"pending_confirmations": []}
    
    # Process pending permission requests
    granted_permissions = dict(state.get("permissions_granted", {}) or {})
    
    for request in pending:
        permission_name = request.get("permission")
        scope = request.get("scope", "*")
        
        # Check if already granted
        key = f"{permission_name}:{scope}"
        if key not in granted_permissions:
            # Request permission from user
            granted = await permission_manager.request(permission_name, scope, request)
            granted_permissions[key] = granted
    
    return {
        "permissions_granted": granted_permissions,
        "pending_confirmations": []
    }


class SecureToolNode(ToolNode):
    """Tool node that enforces permissions before execution."""
    
    async def ainvoke(self, input: dict, config: RunnableConfig, **kwargs):
        """Execute tools with permission checks."""
        from atlas.security.permissions import _DECORATOR_PERMISSIONS
        
        # Get permission manager from config
        perm_manager = config.get("configurable", {}).get("permission_manager")
        if not perm_manager:
            # Fallback to standard execution if no security context
            return await super().ainvoke(input, config, **kwargs)
            
        messages = input.get("messages", [])
        if not messages:
            return await super().ainvoke(input, config, **kwargs)
            
        last_message = messages[-1]
        tool_calls = getattr(last_message, "tool_calls", [])
        
        pending_permissions = []
        
        # Check permissions for all tool calls
        for call in tool_calls:
            tool_name = call.get("name")
            
            # Check decorator registry first, then static dict
            perm_config = (
                _DECORATOR_PERMISSIONS.get(tool_name)
                or PermissionManager.TOOL_PERMISSIONS.get(tool_name)
            )
            
            if perm_config:
                perm_name, scope = perm_config
                
                # Get security context (e.g., Slack user ID)
                security_context = config.get("configurable", {}).get("security_context", {})
                
                # Check if permission is granted
                has_perm = await perm_manager.check(perm_name, scope, context=security_context)
                
                if not has_perm:
                    pending_permissions.append({
                        "permission": perm_name,
                        "scope": scope,
                        "tool_call_id": call.get("id"),
                        "description": f"Tool '{tool_name}' requested access",
                        "level": "MEDIUM",
                        **security_context,
                    })

        if pending_permissions:
            from rich.console import Console
            console = Console()
            console.print(f"[yellow]⚠ Security: Blocking {len(pending_permissions)} tool call(s) for permission check[/yellow]")
            return {
                "pending_confirmations": pending_permissions,
            }

        # If all permissions granted (or tools don't need permissions), execute
        return await super().ainvoke(input, config, **kwargs)
