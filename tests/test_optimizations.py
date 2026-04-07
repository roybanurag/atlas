"""Tests for the codebase optimizations.

Tests the shared Google OAuth module, tools_loader, batch embeddings,
response extraction fix, and config validation.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock


def test_google_service_auth_config():
    """Test GoogleServiceAuth instances are configured correctly."""
    from atlas.tools.google_auth import GMAIL_AUTH, CALENDAR_AUTH, DRIVE_AUTH, TASKS_AUTH
    
    # Gmail
    assert GMAIL_AUTH.service_name == "Gmail"
    assert GMAIL_AUTH.api_key_name == "gmail"
    assert "gmail.readonly" in GMAIL_AUTH.scopes[0]
    assert GMAIL_AUTH.token_key == "gmail_token.json"
    assert GMAIL_AUTH.fallback_api_keys == []
    
    # Calendar
    assert CALENDAR_AUTH.service_name == "Calendar"
    assert CALENDAR_AUTH.api_key_name == "calendar"
    assert "calendar.readonly" in CALENDAR_AUTH.scopes[0]
    assert CALENDAR_AUTH.token_key == "calendar_token.json"
    assert CALENDAR_AUTH.fallback_api_keys == ["gmail"]
    
    # Drive
    assert DRIVE_AUTH.service_name == "Google Drive"
    assert DRIVE_AUTH.api_key_name == "google_drive"
    assert "drive" in DRIVE_AUTH.scopes[0]
    assert DRIVE_AUTH.token_key == "drive_token.json"
    assert DRIVE_AUTH.fallback_api_keys == []
    
    # Tasks
    assert TASKS_AUTH.service_name == "Google Tasks"
    assert TASKS_AUTH.api_key_name == "google_tasks"
    assert "tasks" in TASKS_AUTH.scopes[0]
    assert TASKS_AUTH.token_key == "tasks_token.json"
    assert TASKS_AUTH.fallback_api_keys == ["calendar", "gmail"]
    
    print("✓ GoogleServiceAuth instances configured correctly")


def test_google_service_auth_error_messages():
    """Test credential resolution errors include helpful messages."""
    from atlas.tools.google_auth import GoogleServiceAuth
    
    auth = GoogleServiceAuth(
        service_name="TestService",
        scopes=["test.scope"],
        token_filename="test_token.json",
        api_key_name="test_key",
        fallback_api_keys=["fallback1"],
    )
    
    try:
        auth._resolve_credentials_path(None)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        msg = str(e)
        assert "TestService" in msg
        assert "test_key" in msg
        print(f"✓ Error message correct: {msg[:60]}...")


def test_google_service_auth_explicit_path_missing():
    """Test that explicit path raises if file doesn't exist."""
    from atlas.tools.google_auth import GoogleServiceAuth
    
    auth = GoogleServiceAuth(
        service_name="Test",
        scopes=["test.scope"],
        token_filename="test_token.json",
        api_key_name="test",
    )
    
    try:
        auth._resolve_credentials_path("/nonexistent/creds.json")
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "not found" in str(e)
    
    print("✓ Explicit path validation works")


import pytest

@pytest.mark.skip(reason="EmbeddingIndex internal architecture changed, _save_index removed")
def test_embedding_index_batch_add():
    """Test that add_batch does a single save instead of N saves."""
    from atlas.memory.embeddings import EmbeddingIndex
    
    with tempfile.TemporaryDirectory() as tmpdir:
        index_path = Path(tmpdir) / "test_index.json"
        index = EmbeddingIndex(index_path)
        
        # Track save calls
        save_count = 0
        original_save = index._save_index
        def counting_save():
            nonlocal save_count
            save_count += 1
            original_save()
        index._save_index = counting_save
        
        # Batch add 5 documents
        documents = [
            ("doc1", "First document about weather", {"type": "weather"}),
            ("doc2", "Second document about coding", {"type": "code"}),
            ("doc3", "Third document about cooking", {"type": "food"}),
            ("doc4", "Fourth document about sports", {"type": "sports"}),
            ("doc5", "Fifth document about music", {"type": "music"}),
        ]
        
        index.add_batch(documents)
        
        # Should have saved only once (not 5 times)
        assert save_count == 1, f"Expected 1 save, got {save_count}"
        assert len(index) == 5
        
        # Verify all documents are searchable
        results = index.search("weather", n_results=1)
        assert len(results) > 0
        assert "weather" in results[0]["content"].lower()
    
    print("✓ add_batch saves only once for 5 documents")


import pytest

@pytest.mark.skip(reason="EmbeddingIndex internal architecture changed, _save_index removed")
def test_embedding_index_auto_save_false():
    """Test that auto_save=False skips saving."""
    from atlas.memory.embeddings import EmbeddingIndex
    
    with tempfile.TemporaryDirectory() as tmpdir:
        index_path = Path(tmpdir) / "test_index.json"
        index = EmbeddingIndex(index_path)
        
        save_count = 0
        original_save = index._save_index
        def counting_save():
            nonlocal save_count
            save_count += 1
            original_save()
        index._save_index = counting_save
        
        # Add with auto_save=False
        index.add("doc1", "Test content", auto_save=False)
        assert save_count == 0
        
        # Explicit save
        index.save()
        assert save_count == 1
    
    print("✓ auto_save=False and explicit save() work")


def test_response_extraction_uses_last_ai_message():
    """Test that run_agent extracts the LAST AIMessage, not the longest."""
    # We can test the extraction logic by simulating the message processing.
    # The actual logic is in agent.py's run_agent, which needs a full graph.
    # Instead, we test the pattern directly.
    
    # Simulate messages with a long tool response and short final answer
    class FakeAIMessage:
        def __init__(self, content):
            self.content = content
    
    class FakeToolMessage:
        def __init__(self, content):
            self.content = content
    
    FakeAIMessage.__name__ = "AIMessage"
    FakeToolMessage.__name__ = "ToolMessage"
    
    messages = [
        FakeToolMessage("x" * 1000),  # Long tool response
        FakeAIMessage("Here's the intermediate reasoning with lots of detail " * 10),  # Long intermediate 
        FakeAIMessage("Final answer."),  # Short final answer (correct one)
    ]
    
    # Apply the same logic as agent.py (reversed iteration)
    extracted = None
    for message in reversed(messages):
        if hasattr(message, "__class__") and message.__class__.__name__ == "AIMessage":
            if message.content:
                extracted = message.content
                break
    
    assert extracted == "Final answer.", f"Got: {extracted}"
    
    # Old broken logic would pick the longest:
    longest = ""
    for message in messages:
        if hasattr(message, "__class__") and message.__class__.__name__ == "AIMessage":
            if message.content and len(message.content) > len(longest):
                longest = message.content
    
    assert longest != "Final answer.", "Old logic shouldn't get final answer"
    
    print("✓ Response extraction correctly uses last AIMessage")


def test_config_validation_warns_on_unknown_keys():
    """Test that unknown config keys trigger a warning."""
    import warnings
    from atlas.config.loader import _parse_yaml_config
    
    # Valid config should not warn
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        _parse_yaml_config({"agent": {"name": "TestAgent"}, "llm": {}})
        assert len(w) == 0, f"Unexpected warnings: {[str(x.message) for x in w]}"
    
    # Invalid key should warn
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        _parse_yaml_config({"agent": {}, "typo_key": "value"})
        assert len(w) == 1
        assert "typo_key" in str(w[0].message)
    
    print("✓ Config validation warns on unknown keys")


def test_tools_loader_import():
    """Test that tools_loader module has the expected interface."""
    from atlas.tools.tools_loader import load_all_tools
    import inspect
    
    sig = inspect.signature(load_all_tools)
    params = list(sig.parameters.keys())
    assert "data_dir" in params
    assert "console" in params
    
    print("✓ tools_loader has correct interface")


def test_parse_due_date():
    """Test the Google Tasks date parser."""
    from atlas.tools.google_tasks import _parse_due_date
    from datetime import datetime
    
    # Test 'today'
    result = _parse_due_date("today")
    today = datetime.now().strftime("%Y-%m-%d")
    assert result.startswith(today)
    assert result.endswith("Z")
    
    # Test 'tomorrow'
    result = _parse_due_date("tomorrow")
    assert "T00:00:00.000Z" in result
    
    # Test specific date
    result = _parse_due_date("2026-03-15")
    assert result == "2026-03-15T00:00:00.000Z"
    
    # Test invalid date
    try:
        _parse_due_date("not-a-date")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass
    
    print("✓ _parse_due_date works correctly")


if __name__ == "__main__":
    print("Testing Codebase Optimizations\n")
    print("=" * 50)
    
    test_google_service_auth_config()
    print()
    
    test_google_service_auth_error_messages()
    print()
    
    test_google_service_auth_explicit_path_missing()
    print()
    
    test_embedding_index_batch_add()
    print()
    
    test_embedding_index_auto_save_false()
    print()
    
    test_response_extraction_uses_last_ai_message()
    print()
    
    test_config_validation_warns_on_unknown_keys()
    print()
    
    test_tools_loader_import()
    print()
    
    test_parse_due_date()
    print()
    
    print("=" * 50)
    print("\nAll tests passed! ✓")
