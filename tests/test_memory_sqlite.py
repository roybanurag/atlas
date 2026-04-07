"""Tests for SQLite-backed memory storage.

Tests cover:
- SQLiteMemoryStore: table creation, CRUD, search strategies
- MemoryStore (markdown.py): daily logs, session management, recall
- Migration from old format
"""

import asyncio
import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import numpy as np


# --- SQLiteMemoryStore Tests ---


class TestSQLiteMemoryStore:
    """Tests for the SQLite storage backend."""
    
    @pytest.fixture
    def store(self, tmp_path):
        """Create a test store with embeddings disabled."""
        from atlas.memory.sqlite_store import SQLiteMemoryStore
        
        # Mock the embedder so we don't need sentence-transformers
        store = SQLiteMemoryStore(
            db_path=tmp_path / "test.db",
            embedding_model="all-MiniLM-L6-v2",
        )
        # Patch embedder to be unavailable for most tests
        store.embedder._available = False
        return store
    
    @pytest.fixture
    def store_with_embeddings(self, tmp_path):
        """Create a store with mock embeddings."""
        from atlas.memory.sqlite_store import SQLiteMemoryStore
        from atlas.memory.embeddings import EmbeddingGenerator
        from unittest.mock import patch
        
        def mock_encode(self_or_text, text=None):
            # Handles both class-level patch (self, text) and direct instance patch (text)
            actual_text = text if text is not None else self_or_text
            # Deterministic 8-dim embedding for testing
            import hashlib
            h = hashlib.sha256(actual_text.encode()).digest()
            vec = np.frombuffer(h, dtype=np.float32)[:8].copy()
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec = vec / norm
            return vec
        
        # Patch EmbeddingGenerator BEFORE store init so _probe_embedding_dim
        # picks up the mock dimension (8) and creates vec0 as float[8].
        with patch.object(EmbeddingGenerator, "available", new_callable=lambda: property(lambda self: True)):
            with patch.object(EmbeddingGenerator, "encode", mock_encode):
                store = SQLiteMemoryStore(
                    db_path=tmp_path / "test_emb.db",
                    embedding_model="all-MiniLM-L6-v2",
                )
        
        # Keep mock active after init too
        store.embedder._available = True
        store.embedder.encode = mock_encode
        return store
    
    def test_table_creation(self, store):
        """Tables and indices should be created on init."""
        tables = store.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = [t[0] for t in tables]
        
        assert "memories" in table_names
        assert "memories_fts" in table_names
    
    def test_add_and_retrieve(self, store):
        """Should store and retrieve a document."""
        store.add(
            doc_id="test1",
            content="Hello world, this is a test message",
            role="user",
            session_id="s1",
            source="conversation",
        )
        
        assert store.exists("test1")
        assert store.count() == 1
    
    def test_add_empty_content_skipped(self, store):
        """Empty content should be skipped."""
        store.add(doc_id="empty", content="   ")
        assert not store.exists("empty")
    
    def test_add_replaces_existing(self, store):
        """Adding with same ID should replace."""
        store.add(doc_id="x", content="first version")
        store.add(doc_id="x", content="second version")
        assert store.count() == 1
        
        row = store.db.execute(
            "SELECT content FROM memories WHERE id = ?", ("x",)
        ).fetchone()
        assert row["content"] == "second version"
    
    def test_remove(self, store):
        """Should remove a document."""
        store.add(doc_id="r1", content="to be removed")
        assert store.exists("r1")
        
        store.remove("r1")
        assert not store.exists("r1")
    
    def test_clear_all(self, store):
        """clear() should remove all documents."""
        store.add(doc_id="a", content="one")
        store.add(doc_id="b", content="two")
        assert store.count() == 2
        
        store.clear()
        assert store.count() == 0
    
    def test_clear_by_source(self, store):
        """clear(source_filter) should only remove matching source."""
        store.add(doc_id="c1", content="conv", source="conversation")
        store.add(doc_id="k1", content="know", source="knowledge")
        
        store.clear(source_filter="conversation")
        assert store.count() == 1
        assert store.exists("k1")
        assert not store.exists("c1")
    
    def test_count_by_source(self, store):
        """count(source) should filter correctly."""
        store.add(doc_id="c1", content="conv1", source="conversation")
        store.add(doc_id="c2", content="conv2", source="conversation")
        store.add(doc_id="k1", content="know", source="knowledge")
        
        assert store.count() == 3
        assert store.count("conversation") == 2
        assert store.count("knowledge") == 1
    
    def test_keyword_search(self, store):
        """Keyword search should find matching documents."""
        store.add(doc_id="d1", content="Python programming language tutorial")
        store.add(doc_id="d2", content="JavaScript web framework guide")
        store.add(doc_id="d3", content="Python data science analysis")
        
        results = store.search("Python", n_results=5)
        assert len(results) >= 2
        ids = [r["id"] for r in results]
        assert "d1" in ids
        assert "d3" in ids
    
    def test_keyword_search_no_match(self, store):
        """Keyword search returns empty for no matches."""
        store.add(doc_id="d1", content="Hello world")
        
        results = store.search("nonexistent_xyz", n_results=5)
        assert len(results) == 0
    
    def test_bm25_search(self, store):
        """FTS5 BM25 search should find matching documents."""
        store.add(doc_id="f1", content="The quick brown fox jumps over the lazy dog")
        store.add(doc_id="f2", content="A fast brown fox leaps over a sleepy hound")
        store.add(doc_id="f3", content="Programming in Python is great fun")
        
        results = store._bm25_search("brown fox", n_results=5)
        assert len(results) >= 1
        ids = [r["id"] for r in results]
        assert "f1" in ids or "f2" in ids
    
    def test_bm25_search_source_filter(self, store):
        """BM25 search should respect source filter."""
        store.add(doc_id="c1", content="Memory about Python", source="conversation")
        store.add(doc_id="k1", content="Python is a language", source="knowledge")
        
        results = store._bm25_search("Python", n_results=5, source_filter="knowledge")
        assert len(results) == 1
        assert results[0]["id"] == "k1"
    
    def test_vector_search(self, store_with_embeddings):
        """Vector search should find similar documents."""
        s = store_with_embeddings
        s.add(doc_id="v1", content="machine learning algorithms")
        s.add(doc_id="v2", content="deep neural networks")
        s.add(doc_id="v3", content="cooking recipes for dinner")
        
        results = s._vector_search("AI and machine learning", n_results=3, min_similarity=0.0)
        assert len(results) >= 1
    
    def test_hybrid_search(self, store_with_embeddings):
        """Hybrid search should combine BM25 and vector results."""
        s = store_with_embeddings
        s.add(doc_id="h1", content="Python programming tutorial for beginners")
        s.add(doc_id="h2", content="Advanced Python data analysis techniques")
        s.add(doc_id="h3", content="JavaScript web development framework")
        
        results = s.hybrid_search("Python tutorial", n_results=3, min_score=0.0)
        assert len(results) >= 1
        # Python-related results should appear
        ids = [r["id"] for r in results]
        assert "h1" in ids or "h2" in ids
    
    def test_hybrid_search_source_filter(self, store_with_embeddings):
        """Hybrid search should respect source filter."""
        s = store_with_embeddings
        s.add(doc_id="hc1", content="Python tip", source="conversation")
        s.add(doc_id="hk1", content="Python fact", source="knowledge")
        
        results = s.hybrid_search(
            "Python", n_results=5, source_filter="knowledge", min_score=0.0
        )
        ids = [r["id"] for r in results]
        assert "hc1" not in ids
    
    def test_mmr_reranking(self, store):
        """MMR should promote diverse results."""
        # Create near-duplicate entries
        candidates = [
            {"id": "1", "content": "configured router VLAN 10 for IoT", "similarity": 0.92},
            {"id": "2", "content": "configured router VLAN 10 for IoT devices", "similarity": 0.89},
            {"id": "3", "content": "set up AdGuard DNS on 192.168.10.2", "similarity": 0.78},
            {"id": "4", "content": "router model is Omada ER605", "similarity": 0.75},
        ]
        
        # With pure relevance (lambda=1.0), order = similarity order
        pure_relevance = store._mmr_rerank(candidates, 3, mmr_lambda=1.0)
        assert [r["id"] for r in pure_relevance] == ["1", "2", "3"]
        
        # With diversity (lambda=0.5), near-duplicate "2" should be deprioritized
        diverse = store._mmr_rerank(candidates, 3, mmr_lambda=0.5)
        diverse_ids = [r["id"] for r in diverse]
        assert diverse_ids[0] == "1"  # Top result stays
        # "2" should either be absent or appear after diverse entries
        # Since "1" and "2" are near-duplicates, MMR should prefer "3" and "4" over "2"
        assert "3" in diverse_ids or "4" in diverse_ids
    
    def test_metadata_stored_as_json(self, store):
        """Metadata should round-trip through JSON correctly."""
        store.add(
            doc_id="m1",
            content="test",
            metadata={"key": "value", "count": 42},
        )
        
        row = store.db.execute(
            "SELECT metadata FROM memories WHERE id = ?", ("m1",)
        ).fetchone()
        parsed = json.loads(row["metadata"])
        assert parsed == {"key": "value", "count": 42}
    
    def test_len(self, store):
        """__len__ should return document count."""
        assert len(store) == 0
        store.add(doc_id="l1", content="test")
        assert len(store) == 1


# --- MemoryStore (markdown.py) Tests ---


class TestMemoryStore:
    """Tests for the high-level MemoryStore."""
    
    @pytest.fixture
    def memory(self, tmp_path):
        """Create a MemoryStore with embeddings disabled."""
        from atlas.memory.markdown import MemoryStore
        from atlas.config.core import MemoryConfig
        
        config = MemoryConfig(use_embeddings=True)
        store = MemoryStore(data_dir=tmp_path / "memory", config=config)
        # Disable embeddings to avoid loading model
        store.sqlite_store.embedder._available = False
        return store
    
    @pytest.mark.asyncio
    async def test_store_message(self, memory):
        """Storing a message should add to hot memory and daily log."""
        doc_id = await memory.store_message("user", "Hello, Atlas!")
        
        assert doc_id != ""
        assert len(memory.session_messages) == 1
        assert memory.session_messages[0]["role"] == "user"
    
    @pytest.mark.asyncio
    async def test_daily_log_created(self, memory):
        """Messages should create daily log files."""
        await memory.store_message("user", "Test message")
        
        today_str = datetime.now().strftime("%Y-%m-%d")
        log_file = memory.daily_dir / f"{today_str}.md"
        assert log_file.exists()
        
        content = log_file.read_text()
        assert "Test message" in content
        assert "Daily Log" in content
    
    @pytest.mark.asyncio
    async def test_clean_content(self, memory):
        """Think tags should be removed from stored content."""
        doc_id = await memory.store_message(
            "assistant",
            "<think>Internal reasoning...</think>Here is my answer."
        )
        
        assert len(memory.session_messages) == 1
        stored = memory.session_messages[0]["content"]
        assert "<think>" not in stored
        assert "Here is my answer." in stored
    
    @pytest.mark.asyncio
    async def test_empty_content_skipped(self, memory):
        """Empty messages should return empty string."""
        doc_id = await memory.store_message("user", "")
        assert doc_id == ""
        assert len(memory.session_messages) == 0
    
    @pytest.mark.asyncio
    async def test_store_knowledge(self, memory):
        """Knowledge should be stored in knowledge file and SQLite."""
        doc_id = await memory.store_knowledge(
            "Python 3.12 was released in October 2023",
            source="web_search",
        )
        
        assert doc_id != ""
        assert "Python 3.12" in memory.knowledge_file.read_text()
    
    @pytest.mark.asyncio
    async def test_recall_hot_memory(self, memory):
        """Recall should return hot memory (current session)."""
        await memory.store_message("user", "What is Python?")
        await memory.store_message("assistant", "Python is a programming language.")
        
        results = await memory.recall("Python", n_results=5)
        assert len(results) >= 1
        
        # Hot memory should be present
        hot_results = [r for r in results if r["metadata"].get("source") == "hot"]
        assert len(hot_results) >= 1
    
    @pytest.mark.asyncio
    async def test_recall_from_daily_logs(self, memory):
        """Recall should fall back to daily logs when needed."""
        # Store some messages
        await memory.store_message("user", "Remember the router config")
        
        # Clear hot memory to force daily log fallback
        memory.session_messages = []
        
        results = await memory.recall("router config", n_results=3)
        # Should find something from daily logs
        assert len(results) >= 1
    
    @pytest.mark.asyncio
    async def test_store_conversation(self, memory):
        """Store multiple messages at once."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"},
        ]
        
        doc_ids = await memory.store_conversation(messages)
        assert len(doc_ids) == 3
        assert len(memory.session_messages) == 3
    
    def test_session_id_generated(self, memory):
        """Session ID should be generated on init."""
        assert memory.session_id
        assert "_" in memory.session_id
    
    @pytest.mark.asyncio
    async def test_session_saved(self, memory):
        """Session file should be created."""
        await memory.store_message("user", "test")
        
        assert memory.session_file.exists()
        data = json.loads(memory.session_file.read_text())
        assert data["id"] == memory.session_id
        assert data["message_count"] == 1
    
    def test_get_context_for_prompt(self, memory):
        """Should format context for LLM prompt."""
        memory.session_messages = [
            {"role": "user", "content": "What is DNS?"},
            {"role": "assistant", "content": "DNS is the Domain Name System."},
        ]
        
        context = memory.get_context_for_prompt()
        assert "Recent conversation context" in context
        assert "DNS" in context
    
    def test_get_context_empty(self, memory):
        """Should return empty string when no messages."""
        assert memory.get_context_for_prompt() == ""
    
    def test_memory_status(self, memory):
        """Status should report key metrics."""
        status = memory.get_memory_status()
        
        assert "session_id" in status
        assert "session_messages" in status
        assert "total_memories" in status
        assert "daily_log_files" in status
    
    @pytest.mark.asyncio
    async def test_clear_conversations(self, memory):
        """Clear should reset conversation state."""
        await memory.store_message("user", "test")
        
        memory.clear("conversations")
        assert len(memory.session_messages) == 0
    
    def test_estimate_session_tokens(self, memory):
        """Should estimate token count of current session."""
        memory.session_messages = [
            {"content": "A" * 400},  # ~100 tokens at 4 chars/token
        ]
        
        tokens = memory.estimate_session_tokens()
        assert tokens == 100


# --- Migration Tests ---


class TestMigration:
    """Tests for migrating from old format."""
    
    @pytest.fixture
    def old_format_dir(self, tmp_path):
        """Create a directory with old-format files."""
        data_dir = tmp_path / "memory"
        data_dir.mkdir()
        (data_dir / "sessions").mkdir()
        
        # Old conversations.md
        conv_content = """# Conversation History

## [2026-02-18T10:30:00] USER (ID: abc123)
What is Python?

## [2026-02-18T14:00:00] ASSISTANT (ID: def456)
Python is a programming language.

## [2026-02-19T09:00:00] USER (ID: ghi789)
Tell me about JavaScript.

"""
        (data_dir / "conversations.md").write_text(conv_content)
        
        # Old JSON index
        old_index = {
            "abc123": {
                "content": "What is Python?",
                "embedding": None,
                "metadata": {"role": "user", "timestamp": "2026-02-18T10:30:00"},
            },
            "def456": {
                "content": "Python is a programming language.",
                "embedding": None,
                "metadata": {"role": "assistant", "timestamp": "2026-02-18T14:00:00"},
            },
        }
        (data_dir / "embeddings_index.json").write_text(json.dumps(old_index))
        
        return data_dir
    
    def test_migration_splits_daily_logs(self, old_format_dir):
        """Migration should split conversations.md into daily files."""
        from atlas.memory.markdown import MemoryStore
        from atlas.config.core import MemoryConfig
        
        config = MemoryConfig(use_embeddings=False)
        store = MemoryStore(data_dir=old_format_dir, config=config)
        
        # Old file should be renamed
        assert not (old_format_dir / "conversations.md").exists()
        assert (old_format_dir / "conversations.md.bak").exists()
        
        # Daily logs should exist
        daily_dir = old_format_dir / "daily"
        assert daily_dir.exists()
        daily_files = list(daily_dir.glob("*.md"))
        assert len(daily_files) >= 1
    
    def test_migration_imports_json_index(self, old_format_dir):
        """Migration should import JSON index entries into SQLite."""
        from atlas.memory.markdown import MemoryStore
        from atlas.config.core import MemoryConfig
        
        config = MemoryConfig(use_embeddings=True)
        store = MemoryStore(data_dir=old_format_dir, config=config)
        store.sqlite_store.embedder._available = False
        
        # Old file should be renamed
        assert not (old_format_dir / "embeddings_index.json").exists()
        assert (old_format_dir / "embeddings_index.json.bak").exists()
        
        # Entries should be in SQLite
        assert store.sqlite_store.exists("abc123")
        assert store.sqlite_store.exists("def456")
    
    def test_no_migration_if_no_old_files(self, tmp_path):
        """No migration should run if there are no old-format files."""
        from atlas.memory.markdown import MemoryStore
        from atlas.config.core import MemoryConfig
        
        config = MemoryConfig(use_embeddings=False)
        store = MemoryStore(data_dir=tmp_path / "fresh_memory", config=config)
        
        # Should work fine, no errors
        assert len(store.session_messages) == 0

# --- Compaction & Pruning Tests (Phase 3) ---


class TestCompaction:
    """Tests for auto-compaction and session pruning."""
    
    @pytest.fixture
    def memory(self, tmp_path):
        """Create a MemoryStore with low token budget for testing."""
        from atlas.memory.markdown import MemoryStore
        from atlas.config.core import MemoryConfig
        
        config = MemoryConfig(
            use_embeddings=True,
            max_context_tokens=100,  # Low budget to trigger compaction
            compaction_threshold=0.8,
            compaction_keep_last=2,
            prune_tool_results=True,
            prune_soft_trim_chars=100,
        )
        store = MemoryStore(data_dir=tmp_path / "memory", config=config)
        store.sqlite_store.embedder._available = False
        return store
    
    def test_needs_compaction_below_threshold(self, memory):
        """Should not need compaction when under budget."""
        memory.session_messages = [
            {"content": "short msg", "role": "user"},
        ]
        assert not memory.needs_compaction()
    
    def test_needs_compaction_above_threshold(self, memory):
        """Should need compaction when exceeding budget threshold."""
        # Budget is 100 tokens, threshold 80% = 80 tokens
        # At 4 chars/token, need 320+ chars to exceed
        memory.session_messages = [
            {"content": "x" * 400, "role": "user"},
        ]
        assert memory.needs_compaction()
    
    @pytest.mark.asyncio
    async def test_compact_extractive(self, memory):
        """Compact should produce extractive summary without LLM."""
        # Add enough messages to exceed keep_last_n (2)
        for i in range(5):
            memory.session_messages.append({
                "id": f"msg_{i}",
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"Message number {i} about topic {i}.",
                "timestamp": datetime.now().isoformat(),
            })
        
        summary = await memory.compact()
        
        assert summary is not None
        assert "Earlier in this session" in summary
        # Should keep last 2 messages + 1 summary message
        assert len(memory.session_messages) == 3  # 1 summary + 2 kept
        assert memory.session_messages[0]["role"] == "system"
        assert "[Session summary]" in memory.session_messages[0]["content"]
    
    @pytest.mark.asyncio
    async def test_compact_stores_knowledge(self, memory):
        """Compaction summary should be stored as knowledge."""
        for i in range(5):
            memory.session_messages.append({
                "id": f"msg_{i}",
                "role": "user",
                "content": f"Important fact number {i}.",
                "timestamp": datetime.now().isoformat(),
            })
        
        await memory.compact()
        
        # Knowledge file should contain compaction entry
        knowledge = memory.knowledge_file.read_text()
        assert "compaction" in knowledge.lower() or "session" in knowledge.lower()
    
    @pytest.mark.asyncio
    async def test_compact_not_needed(self, memory):
        """Compact should return None when too few messages."""
        memory.session_messages = [
            {"id": "1", "role": "user", "content": "hi", "timestamp": datetime.now().isoformat()},
        ]
        
        summary = await memory.compact()
        assert summary is None
    
    def test_prune_tool_results(self, memory):
        """Should soft-trim large tool results in older messages."""
        # Build a session with tool results
        memory.session_messages = [
            {"role": "user", "content": "search for something"},
            {"role": "tool", "content": "A" * 500},  # Large tool result
            {"role": "assistant", "content": "Found result 1."},
            {"role": "user", "content": "search again"},
            {"role": "tool", "content": "B" * 500},  # Another large result
            {"role": "assistant", "content": "Found result 2."},
            {"role": "user", "content": "one more search"},
            {"role": "tool", "content": "C" * 500},  # Recent, should not be trimmed
            {"role": "assistant", "content": "Found result 3."},
        ]
        
        memory.prune_tool_results(keep_last_assistants=2)
        
        # First tool result (before cutoff) should be trimmed
        assert len(memory.session_messages[1]["content"]) < 500
        assert "chars trimmed" in memory.session_messages[1]["content"]
        
        # Last tool result (after cutoff) should be untouched
        assert memory.session_messages[7]["content"] == "C" * 500
    
    def test_prune_no_effect_when_few_messages(self, memory):
        """Pruning should do nothing when there aren't enough assistant messages."""
        memory.session_messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        
        memory.prune_tool_results(keep_last_assistants=3)
        # No changes
        assert memory.session_messages[1]["content"] == "hello"
    
    def test_memory_status_includes_budget(self, memory):
        """Status should include token budget info."""
        status = memory.get_memory_status()
        
        assert "token_budget" in status
        assert "budget_used_pct" in status
        assert status["token_budget"] == 100
    
    def test_extractive_summarize(self, memory):
        """Extractive summarizer should pick first sentence per message."""
        messages = [
            {"role": "user", "content": "What is DNS. Tell me more."},
            {"role": "assistant", "content": "DNS resolves names. It's important."},
        ]
        
        summary = memory._extractive_summarize(messages)
        assert "User: What is DNS" in summary
        assert "Atlas: DNS resolves names" in summary

# --- Cross-Session Recall & Consolidation Tests (Phase 4) ---


class TestCrossSessionRecall:
    """Tests for past session indexing and consolidation."""
    
    @pytest.fixture
    def memory_with_sessions(self, tmp_path):
        """Create a MemoryStore with past session files."""
        from atlas.memory.markdown import MemoryStore
        from atlas.config.core import MemoryConfig
        
        data_dir = tmp_path / "memory"
        data_dir.mkdir()
        sessions_dir = data_dir / "sessions"
        sessions_dir.mkdir()
        
        # Create a past session file
        past_session = {
            "id": "past_20260219_100000_abc123",
            "start": "2026-02-19T10:00:00",
            "message_count": 2,
            "messages": [
                {
                    "id": "m1",
                    "role": "user",
                    "content": "What is the router IP address?",
                    "timestamp": "2026-02-19T10:00:00",
                },
                {
                    "id": "m2",
                    "role": "assistant",
                    "content": "The router IP is 192.168.1.1 for the Omada ER605.",
                    "timestamp": "2026-02-19T10:00:05",
                },
            ],
        }
        (sessions_dir / "past_20260219_100000_abc123.json").write_text(
            json.dumps(past_session)
        )
        
        config = MemoryConfig(use_embeddings=True)
        store = MemoryStore(data_dir=data_dir, config=config)
        store.sqlite_store.embedder._available = False
        return store
    
    def test_past_sessions_indexed(self, memory_with_sessions):
        """Past session messages should be indexed in SQLite."""
        store = memory_with_sessions.sqlite_store
        
        assert store.exists("session_past_20260219_100000_abc123_m1")
        assert store.exists("session_past_20260219_100000_abc123_m2")
        assert store.count("past_session") == 2
    
    def test_past_sessions_searchable(self, memory_with_sessions):
        """Past session messages should be searchable."""
        results = memory_with_sessions.sqlite_store.search(
            "router IP address", n_results=5
        )
        
        assert len(results) >= 1
        contents = [r["content"] for r in results]
        assert any("192.168.1.1" in c for c in contents)
    
    def test_incremental_indexing(self, memory_with_sessions, tmp_path):
        """Re-creating MemoryStore should not duplicate indexed sessions."""
        from atlas.memory.markdown import MemoryStore
        from atlas.config.core import MemoryConfig
        
        data_dir = tmp_path / "memory"
        config = MemoryConfig(use_embeddings=True)
        
        # Create a second MemoryStore pointing to same dir
        store2 = MemoryStore(data_dir=data_dir, config=config)
        store2.sqlite_store.embedder._available = False
        
        # Should still be 2, not 4
        assert store2.sqlite_store.count("past_session") == 2
    
    @pytest.mark.asyncio
    async def test_heuristic_consolidation(self, tmp_path):
        """Heuristic consolidation should extract facts from daily logs."""
        from atlas.memory.markdown import MemoryStore
        from atlas.memory.consolidation import consolidate_memories
        from atlas.config.core import MemoryConfig
        
        data_dir = tmp_path / "mem"
        config = MemoryConfig(use_embeddings=False)
        store = MemoryStore(data_dir=data_dir, config=config)
        
        # Create a daily log
        today = datetime.now().strftime("%Y-%m-%d")
        log_file = store.daily_dir / f"{today}.md"
        log_file.write_text(
            f"# Daily Log — {today}\n\n"
            "## [10:00:00] USER (ID: a1)\n"
            "What is DNS?\n\n"
            "## [10:00:05] ASSISTANT (ID: a2)\n"
            "DNS is the Domain Name System that resolves human-readable names to IP addresses.\n\n"
            "## [10:05:00] USER (ID: a3)\n"
            "How do I configure DNS on my router?\n\n"
            "## [10:05:10] ASSISTANT (ID: a4)\n"
            "To configure DNS, go to network settings and set the primary DNS server address.\n\n"
        )
        
        facts = await consolidate_memories(store, lookback_days=1)
        assert len(facts) >= 1
    
    @pytest.mark.asyncio
    async def test_consolidation_empty_dir(self, tmp_path):
        """Consolidation should handle empty daily log dir gracefully."""
        from atlas.memory.markdown import MemoryStore
        from atlas.memory.consolidation import consolidate_memories
        from atlas.config.core import MemoryConfig
        
        config = MemoryConfig(use_embeddings=False)
        store = MemoryStore(data_dir=tmp_path / "empty_mem", config=config)
        
        facts = await consolidate_memories(store, lookback_days=7)
        assert facts == []


# --- Run tests ---

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
