import pytest
import os
from pathlib import Path
from unittest.mock import patch, AsyncMock

from atlas.gateway.server import shutdown_event

@pytest.mark.asyncio
async def test_gateway_token_cleanup(tmp_path):
    """Test that the ephemeral gateway token is deleted on shutdown."""
    with patch("atlas.gateway.server.get_data_dir", return_value=tmp_path):
        token_path = tmp_path / ".gateway_token"
        token_path.write_text("fake_token")
        assert token_path.exists()
        
        # Call shutdown event
        await shutdown_event()
        
        # Token file should be deleted
        assert not token_path.exists()

def test_slack_skill_no_write():
    """Test that the Slack bot's skill generation uses the safe skill_agent path."""
    import inspect
    import atlas.integrations.slack.bot as bot_module
    src = inspect.getsource(bot_module)
    
    assert "_validate_generated_code" in src, "Slack bot must use AST validation for generated skills"
    assert "interactive=False" in src or "cannot write tools to disk" in src, "Slack bot must not write code to disk in non-interactive mode"
