"""SQLite-backed memory storage with FTS5 and vector embeddings.

Features:
- Atomic writes via SQLite transactions (crash-safe)
- FTS5 for BM25 keyword search
- Vector cosine similarity for semantic search
- Hybrid search combining both strategies
- MMR re-ranking for diverse results
- Temporal decay for recency bias
"""

import json
import logging
import math
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import sqlite_vec

from .embeddings import EmbeddingGenerator

logger = logging.getLogger(__name__)


class SQLiteMemoryStore:
    """SQLite-backed memory storage with hybrid search.
    
    Stores memories with both vector embeddings and FTS5 full-text
    index for hybrid search combining semantic and keyword matching.
    """
    
    def __init__(
        self,
        db_path: str | Path,
        embedding_model: str = "all-MiniLM-L6-v2",
    ):
        """Initialize SQLite memory store.
        
        Args:
            db_path: Path to SQLite database file
            embedding_model: Sentence transformer model name
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Initialize embedding generator
        self.embedder = EmbeddingGenerator(model_name=embedding_model)
        
        # Connect and configure SQLite
        self.db = sqlite3.connect(str(self.db_path))
        self.db.row_factory = sqlite3.Row
        self.db.enable_load_extension(True)
        sqlite_vec.load(self.db)
        self.db.enable_load_extension(False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        
        self._vec_dim = self._probe_embedding_dim()
        self._setup_tables()
    
    def _probe_embedding_dim(self) -> int:
        """Return the actual embedding dimension, probing the embedder if needed."""
        if self.embedder.available:
            sample = self.embedder.encode("probe")
            if sample is not None:
                return int(sample.shape[0])
        return 384

    def _setup_tables(self):
        """Create tables and FTS5 index if they don't exist."""
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                role TEXT,
                session_id TEXT,
                source TEXT DEFAULT 'conversation',
                created_at TEXT NOT NULL,
                embedding BLOB,
                metadata TEXT DEFAULT '{}'
            );
            
            CREATE INDEX IF NOT EXISTS idx_memories_session
                ON memories(session_id);
            CREATE INDEX IF NOT EXISTS idx_memories_source
                ON memories(source);
            CREATE INDEX IF NOT EXISTS idx_memories_created
                ON memories(created_at);
        """)
        
        # SQLite-vec virtual table — dimension depends on the loaded model
        try:
            self.db.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_memories USING vec0("
                f"  rowid INTEGER PRIMARY KEY, embedding float[{self._vec_dim}]"
                f")"
            )
        except sqlite3.OperationalError as e:
            logger.warning(f"sqlite-vec not available, vector search disabled: {e}")
        
        # FTS5 content-sync table
        # Using content= for external content (zero-copy from memories table)
        try:
            self.db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
                USING fts5(
                    content,
                    content=memories,
                    content_rowid=rowid
                )
            """)
            self._fts_available = True
        except sqlite3.OperationalError as e:
            logger.warning(f"FTS5 not available, keyword search disabled: {e}")
            self._fts_available = False
        
        self.db.commit()
    
    @property
    def available(self) -> bool:
        """Check if the store is operational."""
        return True  # SQLite is always available
    
    @property
    def embeddings_available(self) -> bool:
        """Check if embedding generation is available."""
        return self.embedder.available
    
    def add(
        self,
        doc_id: str,
        content: str,
        role: str | None = None,
        session_id: str | None = None,
        source: str = "conversation",
        metadata: dict | None = None,
        created_at: str | None = None,
    ):
        """Add a document to the store.
        
        Args:
            doc_id: Unique document identifier
            content: Text content to store and index
            role: Message role (user, assistant, system)
            session_id: Session this message belongs to
            source: Source type (conversation, knowledge, past_session)
            metadata: Optional metadata dict
            created_at: ISO timestamp (defaults to now)
        """
        if not content.strip():
            return
        
        timestamp = created_at or datetime.now().isoformat()
        
        # Generate embedding
        embedding_blob = None
        if self.embedder.available:
            embedding = self.embedder.encode(content)
            if embedding is not None:
                embedding_blob = embedding.tobytes()
        
        # Insert into main table
        self.db.execute(
            """INSERT OR REPLACE INTO memories
               (id, content, role, session_id, source, created_at, embedding, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                doc_id,
                content,
                role,
                session_id,
                source,
                timestamp,
                embedding_blob,
                json.dumps(metadata or {}),
            ),
        )
        
        # Sync to indices
        row = self.db.execute(
            "SELECT rowid FROM memories WHERE id = ?", (doc_id,)
        ).fetchone()
        
        if row:
            if self._fts_available:
                self.db.execute(
                    "INSERT OR REPLACE INTO memories_fts(rowid, content) VALUES (?, ?)",
                    (row[0], content),
                )
            if embedding_blob:
                try:
                    self.db.execute(
                        "INSERT OR REPLACE INTO vec_memories(rowid, embedding) VALUES (?, ?)",
                        (row[0], embedding_blob),
                    )
                except sqlite3.OperationalError as e:
                    logger.debug(f"vec_memories insert skipped (dimension mismatch?): {e}")
        
        self.db.commit()
    
    def add_batch(
        self,
        documents: list[tuple[str, str, dict | None]],
        session_id: str | None = None,
        source: str = "conversation",
    ):
        """Add multiple documents efficiently in a single transaction.
        
        Args:
            documents: List of (doc_id, content, metadata) tuples
            session_id: Session these belong to
            source: Source type
        """
        for doc_id, content, metadata in documents:
            self.add(
                doc_id=doc_id,
                content=content,
                session_id=session_id,
                source=source,
                metadata=metadata,
            )
    
    def exists(self, doc_id: str) -> bool:
        """Check if a document exists."""
        row = self.db.execute(
            "SELECT 1 FROM memories WHERE id = ?", (doc_id,)
        ).fetchone()
        return row is not None
    
    def search(
        self,
        query: str,
        n_results: int = 5,
        min_similarity: float = 0.3,
        source_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """Vector-only search (backward compatible).
        
        Args:
            query: Search query text
            n_results: Maximum results to return
            min_similarity: Minimum cosine similarity threshold
            source_filter: Optional filter by source type
            
        Returns:
            List of matching documents with similarity scores
        """
        if not self.embedder.available:
            return self._keyword_search(query, n_results, source_filter)
        
        return self._vector_search(query, n_results, min_similarity, source_filter)
    
    def hybrid_search(
        self,
        query: str,
        n_results: int = 5,
        vector_weight: float = 0.6,
        text_weight: float = 0.4,
        decay_lambda: float = 0.01,
        mmr_lambda: float = 0.7,
        min_score: float = 0.15,
        source_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """Hybrid BM25 + vector search with temporal decay and MMR.
        
        Args:
            query: Search query text
            n_results: Maximum results to return
            vector_weight: Weight for vector similarity (0-1)
            text_weight: Weight for BM25 keyword score (0-1)
            decay_lambda: Temporal decay rate per day
            mmr_lambda: MMR trade-off: 1.0=pure relevance, 0.0=max diversity
            min_score: Minimum combined score threshold
            source_filter: Optional filter by source type
            
        Returns:
            List of matching documents sorted by hybrid score
        """
        candidate_pool = n_results * 3
        
        # 1. Vector candidates
        vector_results = {}
        if self.embedder.available:
            for r in self._vector_search(query, candidate_pool, 0.1, source_filter):
                vector_results[r['id']] = r['similarity']
        
        # 2. BM25 candidates
        bm25_results = {}
        if self._fts_available:
            for r in self._bm25_search(query, candidate_pool, source_filter):
                bm25_results[r['id']] = r['bm25_score']
        
        # If neither search worked, fall back to keyword search
        if not vector_results and not bm25_results:
            return self._keyword_search(query, n_results, source_filter)
        
        # 3. Union candidates and compute weighted scores
        all_ids = set(vector_results.keys()) | set(bm25_results.keys())
        candidates = []
        
        now = datetime.now()
        
        for doc_id in all_ids:
            v_score = vector_results.get(doc_id, 0.0)
            b_score = bm25_results.get(doc_id, 0.0)
            
            # Weighted merge
            combined = vector_weight * v_score + text_weight * b_score
            
            # 4. Temporal decay
            row = self.db.execute(
                "SELECT content, role, session_id, source, created_at, metadata "
                "FROM memories WHERE id = ?",
                (doc_id,),
            ).fetchone()
            
            if row is None:
                continue
            
            try:
                created = datetime.fromisoformat(row['created_at'])
                days_old = max(0, (now - created).total_seconds() / 86400)
                decay = math.exp(-decay_lambda * days_old)
                combined *= decay
            except (ValueError, TypeError):
                pass  # Keep undecayed score if timestamp parsing fails
            
            if combined < min_score:
                continue
            
            candidates.append({
                'id': doc_id,
                'content': row['content'],
                'metadata': json.loads(row['metadata'] or '{}'),
                'role': row['role'],
                'session_id': row['session_id'],
                'source': row['source'],
                'created_at': row['created_at'],
                'similarity': combined,
                'vector_score': v_score,
                'bm25_score': b_score,
            })
        
        # Sort by combined score
        candidates.sort(key=lambda x: x['similarity'], reverse=True)
        
        # 5. MMR re-ranking for diversity
        if len(candidates) > n_results and mmr_lambda < 1.0:
            candidates = self._mmr_rerank(candidates, n_results, mmr_lambda)
        else:
            candidates = candidates[:n_results]
        
        return candidates
    
    def _vector_search(
        self,
        query: str,
        n_results: int,
        min_similarity: float = 0.3,
        source_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """Pure vector cosine similarity search."""
        query_embedding = self.embedder.encode(query)
        if query_embedding is None:
            return self._keyword_search(query, n_results, source_filter)
        
        query_blob = query_embedding.tobytes()
        
        # sqlite-vec vec0 tables use KNN: ORDER BY distance + LIMIT on the vec0 table
        try:
            knn_rows = self.db.execute(
                "SELECT v.rowid, vec_distance_cosine(v.embedding, ?) AS dist "
                "FROM vec_memories v ORDER BY dist LIMIT ?",
                [query_blob, n_results * 2],
            ).fetchall()
        except sqlite3.OperationalError as e:
            logger.warning(f"Vector search failed (sqlite-vec unavailable?): {e}")
            return self._keyword_search(query, n_results, source_filter)
        
        if not knn_rows:
            return []
        
        rowids = [r[0] for r in knn_rows]
        distances = {r[0]: r[1] for r in knn_rows}
        
        placeholders = ",".join("?" * len(rowids))
        where = f"m.rowid IN ({placeholders})"
        params: list = list(rowids)
        
        if source_filter:
            where += " AND m.source = ?"
            params.append(source_filter)
        
        mem_rows = self.db.execute(
            f"SELECT m.rowid, m.id, m.content, m.role, m.session_id, "
            f"m.source, m.created_at, m.metadata FROM memories m WHERE {where}",
            params,
        ).fetchall()
        
        results = []
        for row in mem_rows:
            distance = distances.get(row["rowid"], 2.0)
            if distance is None:
                distance = 2.0
            similarity = max(0.0, 1.0 - (distance / 2.0))  # normalise to [0, 1]
            if similarity >= min_similarity:
                results.append({
                    "id": row["id"],
                    "content": row["content"],
                    "metadata": json.loads(row["metadata"] or "{}"),
                    "role": row["role"],
                    "session_id": row["session_id"],
                    "source": row["source"],
                    "created_at": row["created_at"],
                    "similarity": float(similarity),
                })
        
        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results[:n_results]
    
    def _bm25_search(
        self,
        query: str,
        n_results: int,
        source_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """FTS5 BM25 keyword search."""
        if not self._fts_available:
            return []
        
        try:
            # FTS5 match query — escape special characters by removing non-alphanumeric
            import re
            clean_query = re.sub(r'[^\w\s]', ' ', query)
            fts_query = " OR ".join(
                f'"{word}"' for word in clean_query.split() if word.strip()
            )
            if not fts_query:
                return []
            
            sql = """
                SELECT m.id, m.content, m.role, m.session_id, m.source,
                       m.created_at, m.metadata,
                       rank AS bm25_rank
                FROM memories_fts fts
                JOIN memories m ON m.rowid = fts.rowid
                WHERE memories_fts MATCH ?
            """
            params: list = [fts_query]
            
            if source_filter:
                sql += " AND m.source = ?"
                params.append(source_filter)
            
            sql += " ORDER BY rank LIMIT ?"
            params.append(n_results)
            
            rows = self.db.execute(sql, params).fetchall()
            
            results = []
            for row in rows:
                # Convert BM25 rank to 0-1 score (lower rank = better match)
                bm25_rank = abs(float(row['bm25_rank']))
                bm25_score = 1.0 / (1.0 + bm25_rank)
                
                results.append({
                    'id': row['id'],
                    'content': row['content'],
                    'metadata': json.loads(row['metadata'] or '{}'),
                    'role': row['role'],
                    'session_id': row['session_id'],
                    'source': row['source'],
                    'created_at': row['created_at'],
                    'bm25_score': bm25_score,
                    'similarity': bm25_score,
                })
            
            return results
            
        except sqlite3.OperationalError as e:
            logger.warning(f"FTS5 search failed: {e}")
            return []
    
    def _keyword_search(
        self,
        query: str,
        n_results: int,
        source_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fallback keyword-based search when neither embeddings nor FTS5 available."""
        query_words = set(query.lower().split())
        if not query_words:
            return []
        
        where_clause = "WHERE 1=1"
        params: list = []
        if source_filter:
            where_clause += " AND source = ?"
            params.append(source_filter)
        
        rows = self.db.execute(
            f"SELECT id, content, role, session_id, source, created_at, metadata "
            f"FROM memories {where_clause}",
            params,
        ).fetchall()
        
        results = []
        for row in rows:
            content_words = set(row['content'].lower().split())
            overlap = len(query_words & content_words)
            
            if overlap > 0:
                score = overlap / max(len(query_words), 1)
                results.append({
                    'id': row['id'],
                    'content': row['content'],
                    'metadata': json.loads(row['metadata'] or '{}'),
                    'role': row['role'],
                    'session_id': row['session_id'],
                    'source': row['source'],
                    'created_at': row['created_at'],
                    'similarity': score,
                })
        
        results.sort(key=lambda x: x['similarity'], reverse=True)
        return results[:n_results]
    
    def _mmr_rerank(
        self,
        candidates: list[dict],
        n_results: int,
        mmr_lambda: float,
    ) -> list[dict]:
        """Maximal Marginal Relevance re-ranking for diversity.
        
        Iteratively selects candidates that maximize:
            mmr_lambda * relevance - (1 - mmr_lambda) * max_similarity_to_selected
        
        Uses Jaccard similarity on tokenized content (cheap, no extra embeddings).
        """
        if not candidates:
            return []
        
        # Tokenize all candidates
        tokenized = [
            set(c['content'].lower().split()) for c in candidates
        ]
        
        selected: list[dict] = []
        selected_indices: list[int] = []
        remaining = list(range(len(candidates)))
        
        for _ in range(min(n_results, len(candidates))):
            best_idx = -1
            best_mmr = -float('inf')
            
            for idx in remaining:
                relevance = candidates[idx]['similarity']
                
                # Max Jaccard similarity to any already-selected result
                max_sim = 0.0
                for sel_idx in selected_indices:
                    intersection = len(tokenized[idx] & tokenized[sel_idx])
                    union = len(tokenized[idx] | tokenized[sel_idx])
                    if union > 0:
                        jaccard = intersection / union
                        max_sim = max(max_sim, jaccard)
                
                mmr_score = mmr_lambda * relevance - (1 - mmr_lambda) * max_sim
                
                if mmr_score > best_mmr:
                    best_mmr = mmr_score
                    best_idx = idx
            
            if best_idx >= 0:
                selected.append(candidates[best_idx])
                selected_indices.append(best_idx)
                remaining.remove(best_idx)
        
        return selected
    
    def remove(self, doc_id: str):
        """Remove a document from the store."""
        if self._fts_available:
            row = self.db.execute(
                "SELECT rowid FROM memories WHERE id = ?", (doc_id,)
            ).fetchone()
            if row:
                self.db.execute(
                    "DELETE FROM memories_fts WHERE rowid = ?", (row[0],)
                )
        
        self.db.execute("DELETE FROM memories WHERE id = ?", (doc_id,))
        self.db.commit()
    
    def clear(self, source_filter: str | None = None):
        """Clear stored data, optionally filtered by source."""
        if source_filter:
            if self._fts_available:
                self.db.execute(
                    "DELETE FROM memories_fts WHERE rowid IN "
                    "(SELECT rowid FROM memories WHERE source = ?)",
                    (source_filter,),
                )
            self.db.execute(
                "DELETE FROM memories WHERE source = ?", (source_filter,)
            )
        else:
            if self._fts_available:
                self.db.execute("DELETE FROM memories_fts")
            self.db.execute("DELETE FROM memories")
        
        self.db.commit()
    
    def count(self, source_filter: str | None = None) -> int:
        """Count documents in the store."""
        if source_filter:
            row = self.db.execute(
                "SELECT COUNT(*) FROM memories WHERE source = ?",
                (source_filter,),
            ).fetchone()
        else:
            row = self.db.execute("SELECT COUNT(*) FROM memories").fetchone()
        return row[0] if row else 0
    
    def index_past_sessions(self, sessions_dir: Path):
        """Index saved session files for cross-session recall.
        
        Scans session JSON files and inserts their messages into
        the memories table with source='past_session'. This enables
        hybrid search across all past conversations.
        
        Already-indexed sessions are skipped (incremental).
        
        Args:
            sessions_dir: Path to the sessions directory containing JSON files
        """
        if not sessions_dir.exists():
            return
        
        indexed_count = 0
        
        for session_file in sessions_dir.glob("*.json"):
            try:
                data = json.loads(session_file.read_text())
                session_id = data.get('id', session_file.stem)
                
                for msg in data.get('messages', []):
                    msg_id = msg.get('id', '')
                    if not msg_id:
                        continue
                    
                    doc_id = f"session_{session_id}_{msg_id}"
                    
                    # Skip already indexed
                    if self.exists(doc_id):
                        continue
                    
                    content = msg.get('content', '').strip()
                    if not content:
                        continue
                    
                    self.add(
                        doc_id=doc_id,
                        content=content,
                        role=msg.get('role'),
                        session_id=session_id,
                        source='past_session',
                        metadata=msg.get('metadata'),
                        created_at=msg.get('timestamp'),
                    )
                    indexed_count += 1
                    
            except (json.JSONDecodeError, KeyError, OSError) as e:
                logger.warning(f"Skipping session file {session_file.name}: {e}")
                continue
        
        if indexed_count > 0:
            logger.info(f"Indexed {indexed_count} messages from past sessions")
    
    def close(self):
        """Close the database connection."""
        self.db.close()
    
    def __len__(self) -> int:
        return self.count()
    
    def __del__(self):
        try:
            self.db.close()
        except Exception:
            pass
