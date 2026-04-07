"""Tests for the enhanced memory system."""

import asyncio
import tempfile
from pathlib import Path


def test_memory_config():
    """Test MemoryConfig defaults and token estimation."""
    from atlas.memory import MemoryConfig
    
    config = MemoryConfig()
    
    # Check defaults
    assert config.max_context_tokens == 2000
    assert config.max_message_length == 500
    assert config.hot_memory_size == 10
    assert config.clean_thinking_tags is True
    
    # Test token estimation
    text = "Hello world, this is a test message."
    tokens = config.estimate_tokens(text)
    assert tokens > 0
    assert tokens < len(text)  # Should be fewer tokens than chars
    
    # Test budget checking
    assert config.fits_in_budget("short text", 0) is True
    assert config.fits_in_budget("x" * 10000, 1990) is False
    
    print("✓ MemoryConfig works correctly")


def test_memory_store_creation():
    """Test MemoryStore initialization."""
    from atlas.memory import MemoryStore, MemoryConfig
    
    with tempfile.TemporaryDirectory() as tmpdir:
        config = MemoryConfig(use_embeddings=False)  # Disable for test
        store = MemoryStore(tmpdir, config=config)
        
        # Check files/dirs created
        assert store.daily_dir.exists()
        assert store.knowledge_file.exists()
        assert store.sessions_dir.exists()
        
        # Check session initialized
        assert store.session_id is not None
        assert len(store.session_messages) == 0
        
    print("✓ MemoryStore creates files correctly")


def test_content_cleaning():
    """Test that thinking tags are removed."""
    from atlas.memory import MemoryStore, MemoryConfig
    
    with tempfile.TemporaryDirectory() as tmpdir:
        config = MemoryConfig(use_embeddings=False)
        store = MemoryStore(tmpdir, config=config)
        
        # Test cleaning
        dirty_content = """<think>
        Let me think about this...
        I should process the request.
        </think>
        
        Here is my clean response to your question."""
        
        cleaned = store._clean_content(dirty_content)
        
        assert "<think>" not in cleaned
        assert "</think>" not in cleaned
        assert "Let me think" not in cleaned
        assert "clean response" in cleaned
        
        # Test unclosed tags
        unclosed = "Some text <think> more thinking..."
        cleaned_unclosed = store._clean_content(unclosed)
        assert "<think>" not in cleaned_unclosed
        assert "Some text" in cleaned_unclosed
        
    print("✓ Content cleaning removes thinking tags")


def test_content_truncation():
    """Test that long content is truncated."""
    from atlas.memory import MemoryStore, MemoryConfig
    
    with tempfile.TemporaryDirectory() as tmpdir:
        config = MemoryConfig(max_message_length=100, use_embeddings=False)
        store = MemoryStore(tmpdir, config=config)
        
        long_content = "This is a very long message. " * 20
        truncated = store._truncate_content(long_content)
        
        assert len(truncated) <= 100
        assert "[truncated...]" in truncated
        
    print("✓ Content truncation works correctly")


def test_compact_summary():
    """Test compact summary generation."""
    from atlas.memory import MemoryStore, MemoryConfig
    
    with tempfile.TemporaryDirectory() as tmpdir:
        config = MemoryConfig(use_embeddings=False)
        store = MemoryStore(tmpdir, config=config)
        
        summary = store._create_compact_summary("user", "What is the weather today?")
        
        assert "User:" in summary
        assert "weather" in summary
        assert "]" in summary  # Timestamp bracket
        
        summary2 = store._create_compact_summary("assistant", "The weather is sunny.")
        assert "Atlas:" in summary2
        
    print("✓ Compact summary generation works")


def test_store_and_recall():
    """Test storing and recalling messages."""
    from atlas.memory import MemoryStore, MemoryConfig
    
    with tempfile.TemporaryDirectory() as tmpdir:
        config = MemoryConfig(use_embeddings=False)
        store = MemoryStore(tmpdir, config=config)
        
        # Store messages
        async def do_test():
            await store.store_message("user", "Hello, how are you?")
            await store.store_message("assistant", "I'm doing well, thank you!")
            await store.store_message("user", "What's the weather?")
            
            assert len(store.session_messages) == 3
            
            # Recall
            memories = await store.recall("weather", n_results=5)
            assert len(memories) > 0
            
            # Check content format
            for m in memories:
                assert "content" in m
                assert "metadata" in m
        
        asyncio.run(do_test())
        
    print("✓ Store and recall works correctly")


def test_embedding_generator_fallback():
    """Test that EmbeddingGenerator falls back gracefully."""
    from atlas.memory.embeddings import EmbeddingGenerator
    
    gen = EmbeddingGenerator(model_name="all-MiniLM-L6-v2")
    
    # Should either work or gracefully report unavailable
    result = gen.encode("test text")
    if gen.available:
        assert result is not None
        assert len(result) > 0
    else:
        assert result is None
    
    # Batch encoding
    results = gen.encode_batch(["text one", "text two"])
    assert len(results) == 2
    
    print(f"✓ EmbeddingGenerator works (available: {gen.available})")


def test_session_management():
    """Test session tracking."""
    from atlas.memory import MemoryStore, MemoryConfig
    
    with tempfile.TemporaryDirectory() as tmpdir:
        config = MemoryConfig(use_embeddings=False)
        store = MemoryStore(tmpdir, config=config)
        
        # Generate session ID
        assert store.session_id is not None
        assert "_" in store.session_id  # timestamp_uuid format
        
        # Store and save session
        async def do_test():
            await store.store_message("user", "Test message")
            summary = await store.get_session_summary()
            assert "Test message" in summary or "1 messages" in summary
        
        asyncio.run(do_test())
        
        # Check session file created
        assert store.session_file.exists()
        
    print("✓ Session management works correctly")


if __name__ == "__main__":
    print("Testing Enhanced Memory System\n")
    print("=" * 50)
    
    test_memory_config()
    print()
    
    test_memory_store_creation()
    print()
    
    test_content_cleaning()
    print()
    
    test_content_truncation()
    print()
    
    test_compact_summary()
    print()
    
    test_store_and_recall()
    print()
    
    test_embedding_index_fallback()
    print()
    
    test_session_management()
    print()
    
    print("=" * 50)
    print("\nAll tests passed! ✓")
    print("\nMemory improvements implemented:")
    print("- Clean storage (removes <think> tags)")
    print("- Token-efficient compact format")
    print("- Session-based organization")
    print("- Semantic search with embeddings")
    print("- Tiered memory (hot/warm/cold)")
    print("\nTo enable semantic search, install: pip install sentence-transformers")
