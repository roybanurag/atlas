"""Memory module for Atlas.

Provides tiered memory storage with:
- Clean content storage (removes LLM artifacts)
- Session-based organization
- SQLite-backed semantic + keyword search
- Hybrid BM25 + vector search with MMR diversity
- Token-efficient context management
- Daily log files for human-readable history
- Auto-compaction and session pruning
- Cross-session recall and memory consolidation
"""

from atlas.config import DEFAULT_MEMORY_CONFIG, MemoryConfig

from .consolidation import consolidate_memories
from .embeddings import EmbeddingGenerator
from .markdown import MemoryStore
from .sqlite_store import SQLiteMemoryStore

# Backward compatibility
EmbeddingIndex = EmbeddingGenerator

__all__ = [
    "MemoryStore",
    "SQLiteMemoryStore",
    "EmbeddingGenerator",
    "EmbeddingIndex",  # backward compat
    "MemoryConfig",
    "DEFAULT_MEMORY_CONFIG",
    "consolidate_memories",
]
