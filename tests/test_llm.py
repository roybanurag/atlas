"""Tests for the LLM routing layer.

Covers:
- ModelRouter: privacy-based routing, remote fallback, complexity routing
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from langchain_core.messages import AIMessage, HumanMessage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_llm(response: str = "response") -> AsyncMock:
    llm = AsyncMock()
    llm.ainvoke = AsyncMock(return_value=AIMessage(content=response))
    llm.invoke = MagicMock(return_value=AIMessage(content=response))
    return llm


# ---------------------------------------------------------------------------
# ModelRouter
# ---------------------------------------------------------------------------


class TestModelRouter:
    def _make_router(self, local_response="local", remote_response="remote"):
        from atlas.llm.router import ModelRouter
        from atlas.config import PrivacyLevel

        local = _make_llm(local_response)
        remote = _make_llm(remote_response)
        return ModelRouter(local_llm=local, remote_llm=remote), local, remote

    @pytest.mark.asyncio
    async def test_local_only_always_uses_local(self):
        from atlas.config import PrivacyLevel

        router, local, remote = self._make_router()
        messages = [HumanMessage(content="hello")]

        result = await router.route(messages, privacy_level=PrivacyLevel.LOCAL_ONLY)
        assert result.content == "local"
        local.ainvoke.assert_awaited_once()
        remote.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_sensitive_always_uses_local(self):
        from atlas.config import PrivacyLevel

        router, local, remote = self._make_router()
        messages = [HumanMessage(content="my password is secret")]

        result = await router.route(messages, privacy_level=PrivacyLevel.SENSITIVE)
        assert result.content == "local"
        remote.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_remote_allowed_high_complexity_uses_remote(self):
        from atlas.config import PrivacyLevel

        router, local, remote = self._make_router()
        messages = [HumanMessage(content="complex reasoning")]

        result = await router.route(
            messages,
            privacy_level=PrivacyLevel.REMOTE_ALLOWED,
            complexity="high",
        )
        assert result.content == "remote"
        remote.ainvoke.assert_awaited_once()
        local.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_remote_allowed_medium_complexity_uses_local(self):
        from atlas.config import PrivacyLevel

        router, local, remote = self._make_router()
        messages = [HumanMessage(content="simple question")]

        result = await router.route(
            messages,
            privacy_level=PrivacyLevel.REMOTE_ALLOWED,
            complexity="medium",
        )
        assert result.content == "local"
        remote.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_remote_llm_falls_back_to_local(self):
        from atlas.llm.router import ModelRouter
        from atlas.config import PrivacyLevel

        local = _make_llm("local only")
        router = ModelRouter(local_llm=local, remote_llm=None)

        result = await router.route(
            [HumanMessage(content="hi")],
            privacy_level=PrivacyLevel.REMOTE_ALLOWED,
            complexity="high",
        )
        assert result.content == "local only"

    def test_set_privacy_updates_default(self):
        from atlas.llm.router import ModelRouter
        from atlas.config import PrivacyLevel

        local = _make_llm()
        router = ModelRouter(local_llm=local)
        assert router.default_privacy == PrivacyLevel.LOCAL_ONLY

        router.set_privacy(PrivacyLevel.REMOTE_ALLOWED)
        assert router.default_privacy == PrivacyLevel.REMOTE_ALLOWED

    def test_is_remote_available_reflects_config(self):
        from atlas.llm.router import ModelRouter

        local = _make_llm()
        router_no_remote = ModelRouter(local_llm=local)
        assert router_no_remote.is_remote_available is False

        router_with_remote = ModelRouter(local_llm=local, remote_llm=_make_llm())
        assert router_with_remote.is_remote_available is True

    @pytest.mark.asyncio
    async def test_default_privacy_routes_to_local(self):
        """Default privacy (LOCAL_ONLY) should always route to local LLM."""
        from atlas.llm.router import ModelRouter

        local = _make_llm("local answer")
        remote = _make_llm("remote answer")
        router = ModelRouter(local_llm=local, remote_llm=remote)

        result = await router.route([HumanMessage(content="hi")])
        assert result.content == "local answer"
        remote.ainvoke.assert_not_called()


# ---------------------------------------------------------------------------
# Run directly
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
