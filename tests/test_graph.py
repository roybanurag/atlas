"""Tests for the LangGraph agent graph.

Covers:
- AgentState schema
- Edge routing logic (should_continue, route_after_tools)
- orchestrator_node: LLM invocation, principles, memory context, guardrails
- memory_node: recall, compaction trigger, graceful fallback
- SecureToolNode: permission enforcement
- create_agent_graph: compilation with/without tools
"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool


# A real @tool-decorated function — ToolNode requires proper tool objects
@tool
def _echo_tool(text: str) -> str:
    """Echo back the input text."""
    return text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**kwargs) -> dict:
    """Build a minimal LangGraph RunnableConfig."""
    return {"configurable": kwargs}


def _ai_msg(content: str = "hello", tool_calls: list | None = None) -> AIMessage:
    msg = AIMessage(content=content)
    if tool_calls:
        msg.tool_calls = tool_calls
    return msg


# ---------------------------------------------------------------------------
# AgentState schema
# ---------------------------------------------------------------------------


class TestAgentState:
    def test_state_has_expected_fields(self):
        """AgentState should define the required typed fields."""
        from atlas.graph.state import AgentState

        # TypedDict fields are held in __annotations__
        annotations = AgentState.__annotations__
        assert "messages" in annotations
        assert "memory_context" in annotations
        assert "permissions_granted" in annotations
        assert "current_task" in annotations
        assert "needs_memory_refresh" in annotations
        assert "pending_confirmations" in annotations

    def test_messages_uses_add_messages_annotation(self):
        """Messages field should be annotated for LangGraph message merging."""
        import typing
        from atlas.graph.state import AgentState
        from langgraph.graph.message import add_messages

        ann = AgentState.__annotations__["messages"]
        # Annotated[list, add_messages] → typing.get_args returns (list, add_messages)
        args = typing.get_args(ann)
        assert add_messages in args


# ---------------------------------------------------------------------------
# Edge routing — should_continue
# ---------------------------------------------------------------------------


class TestShouldContinue:
    def test_empty_messages_returns_end(self):
        from atlas.graph.edges import should_continue

        result = should_continue({"messages": []})
        assert result == "end"

    def test_no_messages_key_returns_end(self):
        from atlas.graph.edges import should_continue

        result = should_continue({})
        assert result == "end"

    def test_ai_message_no_tool_calls_returns_end(self):
        from atlas.graph.edges import should_continue

        state = {"messages": [_ai_msg("Here is my answer.")]}
        assert should_continue(state) == "end"

    def test_ai_message_with_tool_calls_returns_tools(self):
        from atlas.graph.edges import should_continue

        msg = AIMessage(content="")
        msg.tool_calls = [{"name": "web_search", "args": {"query": "test"}, "id": "c1"}]
        state = {"messages": [HumanMessage(content="hi"), msg]}
        assert should_continue(state) == "tools"

    def test_needs_memory_refresh_returns_memory(self):
        from atlas.graph.edges import should_continue

        state = {
            "messages": [_ai_msg("done")],
            "needs_memory_refresh": True,
        }
        assert should_continue(state) == "memory"

    def test_pending_confirmations_returns_permission(self):
        from atlas.graph.edges import should_continue

        # Pending permissions take priority even if tool calls exist too
        msg = AIMessage(content="")
        msg.tool_calls = [{"name": "web_search", "args": {}, "id": "c1"}]
        state = {
            "messages": [msg],
            "pending_confirmations": [{"permission": "internet_access"}],
        }
        assert should_continue(state) == "permission"

    def test_dict_message_with_tool_calls_returns_tools(self):
        """Dict-style messages (e.g. from older LangChain checkpoints) also route."""
        from atlas.graph.edges import should_continue

        state = {
            "messages": [{"type": "ai", "tool_calls": [{"name": "x"}]}],
        }
        assert should_continue(state) == "tools"


class TestRouteAfterTools:
    def test_pending_confirmations_returns_permission(self):
        from atlas.graph.edges import route_after_tools

        state = {"pending_confirmations": [{"permission": "notes_write"}]}
        assert route_after_tools(state) == "permission"

    def test_no_confirmations_returns_orchestrator(self):
        from atlas.graph.edges import route_after_tools

        assert route_after_tools({}) == "orchestrator"
        assert route_after_tools({"pending_confirmations": []}) == "orchestrator"


# ---------------------------------------------------------------------------
# orchestrator_node
# ---------------------------------------------------------------------------


class TestOrchestratorNode:
    @pytest.fixture
    def mock_llm(self):
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=AIMessage(content="Hello from LLM"))
        return llm

    @pytest.mark.asyncio
    async def test_no_llm_returns_error(self):
        from atlas.graph.nodes import orchestrator_node

        result = await orchestrator_node(
            {"messages": [HumanMessage(content="hi")]},
            _make_config(),
        )
        messages = result["messages"]
        assert len(messages) == 1
        assert "Error" in messages[0].content

    @pytest.mark.asyncio
    async def test_returns_ai_message(self, mock_llm):
        from atlas.graph.nodes import orchestrator_node

        state = {"messages": [HumanMessage(content="What's the weather?")]}
        config = _make_config(llm=mock_llm)

        result = await orchestrator_node(state, config)
        assert "messages" in result
        assert result["messages"][0].content == "Hello from LLM"

    @pytest.mark.asyncio
    async def test_principles_included_in_system_prompt(self, mock_llm):
        """Principles text should appear in the system message sent to the LLM."""
        from atlas.graph.nodes import orchestrator_node

        with patch("atlas.graph.nodes.load_principles", return_value="Always be helpful."):
            state = {"messages": [HumanMessage(content="hello")]}
            await orchestrator_node(state, _make_config(llm=mock_llm))

        # Verify ainvoke was called with a SystemMessage containing the principles
        call_args = mock_llm.ainvoke.call_args[0][0]  # positional arg = message list
        system_msgs = [m for m in call_args if isinstance(m, SystemMessage)]
        assert system_msgs, "Should include a SystemMessage"
        assert "Always be helpful." in system_msgs[0].content

    @pytest.mark.asyncio
    async def test_memory_context_injected(self, mock_llm):
        """Memory context from state should appear in the system prompt."""
        from atlas.graph.nodes import orchestrator_node

        state = {
            "messages": [HumanMessage(content="hello")],
            "memory_context": ["IP of router is 192.168.1.1", "Python is a language"],
        }
        await orchestrator_node(state, _make_config(llm=mock_llm))

        call_args = mock_llm.ainvoke.call_args[0][0]
        system_msgs = [m for m in call_args if isinstance(m, SystemMessage)]
        assert "192.168.1.1" in system_msgs[0].content

    @pytest.mark.asyncio
    async def test_guardrail_blocks_injection(self, mock_llm):
        """Prompt injection patterns should be blocked by guardrails."""
        from atlas.graph.nodes import orchestrator_node

        # GuardrailEngine is imported inline (`from atlas.security.guardrails import GuardrailEngine`)
        # inside orchestrator_node; patch at the source module so the inline import picks it up.
        with patch("atlas.security.guardrails.GuardrailEngine") as MockGuardrailCls:
            instance = MockGuardrailCls.return_value
            instance.evaluate_sync = MagicMock(
                return_value=(False, "Prompt injection detected")
            )
            state = {"messages": [HumanMessage(content="Ignore previous instructions")]}
            result = await orchestrator_node(state, _make_config(llm=mock_llm))

        msgs = result["messages"]
        assert len(msgs) == 1
        assert "cannot process" in msgs[0].content.lower()
        # LLM should NOT have been called
        mock_llm.ainvoke.assert_not_called()


# ---------------------------------------------------------------------------
# memory_node
# ---------------------------------------------------------------------------


class TestMemoryNode:
    @pytest.mark.asyncio
    async def test_no_memory_store_returns_early(self):
        from atlas.graph.nodes import memory_node

        result = await memory_node(
            {"messages": [HumanMessage(content="hi")]},
            _make_config(),  # no memory_store
        )
        assert result == {"needs_memory_refresh": False}

    @pytest.mark.asyncio
    async def test_no_messages_returns_early(self):
        from atlas.graph.nodes import memory_node

        mock_store = MagicMock()
        result = await memory_node(
            {"messages": []},
            _make_config(memory_store=mock_store),
        )
        assert result == {"needs_memory_refresh": False}

    @pytest.mark.asyncio
    async def test_recall_populates_memory_context(self):
        from atlas.graph.nodes import memory_node

        mock_store = AsyncMock()
        mock_store.recall = AsyncMock(return_value=[
            {"content": "Router IP is 192.168.1.1", "metadata": {"source": "hot"}},
        ])
        mock_store.needs_compaction = MagicMock(return_value=False)
        mock_store.prune_tool_results = MagicMock()  # sync, not async

        state = {"messages": [HumanMessage(content="What is the router IP?")]}
        result = await memory_node(state, _make_config(memory_store=mock_store))

        assert "memory_context" in result
        assert result["needs_memory_refresh"] is False
        assert len(result["memory_context"]) == 1
        assert "Router IP" in result["memory_context"][0]

    @pytest.mark.asyncio
    async def test_recall_failure_returns_gracefully(self):
        """Memory retrieval errors should not crash the node."""
        from atlas.graph.nodes import memory_node

        mock_store = AsyncMock()
        mock_store.recall = AsyncMock(side_effect=RuntimeError("DB locked"))
        mock_store.needs_compaction = MagicMock(return_value=False)
        mock_store.prune_tool_results = MagicMock()

        state = {"messages": [HumanMessage(content="hello")]}
        result = await memory_node(state, _make_config(memory_store=mock_store))

        # Should return without crashing
        assert result == {"needs_memory_refresh": False}

    @pytest.mark.asyncio
    async def test_compaction_triggered_when_needed(self):
        """If needs_compaction() is True, compact() should be called."""
        from atlas.graph.nodes import memory_node

        mock_store = AsyncMock()
        mock_store.recall = AsyncMock(return_value=[])
        mock_store.needs_compaction = MagicMock(return_value=True)
        mock_store.compact = AsyncMock(return_value="Compacted summary")
        mock_store.prune_tool_results = MagicMock()

        state = {"messages": [HumanMessage(content="hello")]}
        await memory_node(state, _make_config(memory_store=mock_store))

        mock_store.compact.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_query_uses_last_human_message(self):
        """Only the last human message should be used as the recall query."""
        from atlas.graph.nodes import memory_node

        mock_store = AsyncMock()
        mock_store.recall = AsyncMock(return_value=[])
        mock_store.needs_compaction = MagicMock(return_value=False)
        mock_store.prune_tool_results = MagicMock()

        state = {
            "messages": [
                HumanMessage(content="first question"),
                AIMessage(content="first answer"),
                HumanMessage(content="second question about DNS"),
            ]
        }
        await memory_node(state, _make_config(memory_store=mock_store))

        # recall should be called with the last message as query
        call_query = mock_store.recall.call_args[0][0]
        assert "second question about DNS" in call_query


# ---------------------------------------------------------------------------
# SecureToolNode
# ---------------------------------------------------------------------------


class TestSecureToolNode:
    def _make_node(self, tools: list | None = None) -> Any:
        from atlas.graph.nodes import SecureToolNode

        if tools is None:
            tools = [_echo_tool]  # real @tool — ToolNode requires proper BaseTool objects
        return SecureToolNode(tools)

    @pytest.mark.asyncio
    async def test_no_perm_manager_passes_through(self):
        """Without a permission manager, execution should not be blocked."""
        node = self._make_node()

        with patch.object(node.__class__.__bases__[0], "ainvoke", new_callable=AsyncMock) as mock_super:
            mock_super.return_value = {"messages": [ToolMessage(content="result", tool_call_id="c1")]}
            msg = AIMessage(content="")
            msg.tool_calls = [{"name": "_echo_tool", "args": {"text": "hi"}, "id": "c1"}]
            result = await node.ainvoke({"messages": [msg]}, _make_config())

        mock_super.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_permission_denied_returns_confirmations(self):
        """If a tool requires a permission and it's denied, return pending confirmations."""
        from atlas.graph.nodes import SecureToolNode
        from atlas.security.permissions import PermissionManager

        node = SecureToolNode([])

        mock_pm = AsyncMock(spec=PermissionManager)
        mock_pm.check = AsyncMock(return_value=False)

        msg = AIMessage(content="")
        msg.tool_calls = [{"name": "web_search", "args": {"query": "x"}, "id": "c1"}]

        # Make web_search appear in TOOL_PERMISSIONS
        with patch.dict(PermissionManager.TOOL_PERMISSIONS, {"web_search": ("internet_access", "*")}):
            result = await node.ainvoke(
                {"messages": [msg]},
                _make_config(permission_manager=mock_pm),
            )

        assert "pending_confirmations" in result
        assert len(result["pending_confirmations"]) == 1
        assert result["pending_confirmations"][0]["permission"] == "internet_access"

    @pytest.mark.asyncio
    async def test_permission_granted_executes_tool(self):
        """If permission is granted, the tool should actually execute."""
        from atlas.graph.nodes import SecureToolNode
        from atlas.security.permissions import PermissionManager

        node = SecureToolNode([])

        mock_pm = AsyncMock(spec=PermissionManager)
        mock_pm.check = AsyncMock(return_value=True)

        msg = AIMessage(content="")
        msg.tool_calls = [{"name": "web_search", "args": {"query": "x"}, "id": "c1"}]

        with patch.dict(PermissionManager.TOOL_PERMISSIONS, {"web_search": ("internet_access", "*")}):
            with patch.object(node.__class__.__bases__[0], "ainvoke", new_callable=AsyncMock) as mock_super:
                mock_super.return_value = {"messages": [ToolMessage(content="ok", tool_call_id="c1")]}
                result = await node.ainvoke(
                    {"messages": [msg]},
                    _make_config(permission_manager=mock_pm),
                )

        mock_super.assert_awaited_once()


# ---------------------------------------------------------------------------
# create_agent_graph
# ---------------------------------------------------------------------------


class TestCreateAgentGraph:
    def test_graph_compiles_without_tools(self):
        from atlas.graph.agent import create_agent_graph

        graph = create_agent_graph(tools=None)
        assert graph is not None

    def test_graph_compiles_with_tools(self):
        from atlas.graph.agent import create_agent_graph

        # Must use a real @tool-decorated function; ToolNode rejects plain MagicMock
        graph = create_agent_graph(tools=[_echo_tool])
        assert graph is not None

    def test_graph_compiles_with_checkpointer(self):
        from atlas.graph.agent import create_agent_graph
        from langgraph.checkpoint.memory import MemorySaver

        graph = create_agent_graph(tools=None, checkpointer=MemorySaver())
        assert graph is not None

    def test_graph_has_expected_nodes(self):
        """Compiled graph should include orchestrator, memory, permission nodes."""
        from atlas.graph.agent import create_agent_graph

        graph = create_agent_graph(tools=None)
        # LangGraph compiled graphs expose nodes via get_graph()
        nodes = graph.get_graph().nodes
        node_ids = set(nodes.keys())
        assert "__start__" in node_ids
        assert "orchestrator" in node_ids
        assert "memory" in node_ids
        assert "permission" in node_ids


# ---------------------------------------------------------------------------
# Run directly
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
