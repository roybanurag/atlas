"""Enhanced Markdown-based memory storage with tiered architecture.

Features:
- Clean storage (removes LLM thinking artifacts)
- Session-based organization
- Tiered memory (hot/warm/cold)
- Token-efficient context format
- SQLite-backed semantic + keyword search
- Daily log files (memory/daily/YYYY-MM-DD.md)
- Auto-compaction support
"""

import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from atlas.config import DEFAULT_MEMORY_CONFIG, MemoryConfig

from .sqlite_store import SQLiteMemoryStore

logger = logging.getLogger(__name__)


class MemoryStore:
    """Enhanced long-term memory storage using Markdown + SQLite.
    
    Stores conversations in a tiered architecture:
    - Hot: Recent messages in full detail (current session, in-memory)
    - Warm: Semantically relevant memories (SQLite hybrid search)
    - Cold: Knowledge base with extracted facts (SQLite)
    
    Human-readable daily markdown logs are maintained alongside
    the SQLite index for inspectability.
    """
    
    def __init__(
        self,
        data_dir: str | Path,
        config: Optional[MemoryConfig] = None,
    ):
        """Initialize memory store.
        
        Args:
            data_dir: Directory for storage
            config: Optional memory configuration
        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config = config or DEFAULT_MEMORY_CONFIG
        
        # Daily log directory
        self.daily_dir = self.data_dir / "daily"
        self.daily_dir.mkdir(parents=True, exist_ok=True)
        
        # Knowledge file
        self.knowledge_file = self.data_dir / "knowledge.md"
        
        # Session tracking
        self.sessions_dir = self.data_dir / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = self._generate_session_id()
        self.session_file = self.sessions_dir / f"{self.session_id}.json"
        self.session_messages: list[dict] = []
        self.session_start = datetime.now()
        
        # Initialize SQLite store (replaces JSON embedding index)
        self.sqlite_store = SQLiteMemoryStore(
            db_path=self.data_dir / "memory.db",
            embedding_model=self.config.embedding_model,
        )
        
        # Run migration if needed (old format → new format)
        self._maybe_migrate()
        
        # Ensure knowledge file exists with header
        if not self.knowledge_file.exists():
            self.knowledge_file.write_text("# Knowledge Base\n\n")
        
        # Index past session files for cross-session recall
        if self.config.use_embeddings:
            self.sqlite_store.index_past_sessions(self.sessions_dir)
    
    @property
    def today_file(self) -> Path:
        """Get today's daily log file path."""
        return self.daily_dir / f"{datetime.now().strftime('%Y-%m-%d')}.md"
    
    def _generate_session_id(self) -> str:
        """Generate unique session ID."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique = uuid.uuid4().hex[:6]
        return f"{timestamp}_{unique}"
    
    def _clean_content(self, content: str) -> str:
        """Clean content before storage.
        
        Removes:
        - LLM thinking tags (<think>, </think>)
        - Excessive whitespace
        - Common artifacts
        """
        if not self.config.clean_thinking_tags:
            return content.strip()
        
        cleaned = content
        
        # Remove <think>...</think> blocks (including multiline)
        cleaned = re.sub(r'<think>.*?</think>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
        
        # Remove unclosed <think> tags and everything after
        cleaned = re.sub(r'<think>.*', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
        
        # Remove standalone </think> tags
        cleaned = re.sub(r'</think>', '', cleaned, flags=re.IGNORECASE)
        
        # Remove excessive whitespace
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
        cleaned = re.sub(r' {2,}', ' ', cleaned)
        
        return cleaned.strip()
    
    def _truncate_content(self, content: str) -> str:
        """Truncate content if too long."""
        if len(content) <= self.config.max_message_length:
            return content
        
        # Truncate with ellipsis
        truncated = content[:self.config.max_message_length - 20]
        # Try to cut at word boundary
        last_space = truncated.rfind(' ')
        if last_space > self.config.max_message_length // 2:
            truncated = truncated[:last_space]
        
        return truncated + " [truncated...]"
    
    def _create_compact_summary(self, role: str, content: str) -> str:
        """Create a compact summary for context injection.
        
        Format: [timestamp] Role: brief content summary
        """
        timestamp = datetime.now().strftime("%b %d %H:%M")
        
        # Truncate for summary
        summary = content[:150]
        if len(content) > 150:
            # Cut at sentence or word boundary
            for sep in ['. ', '? ', '! ', ', ', ' ']:
                idx = summary.rfind(sep)
                if idx > 50:
                    summary = summary[:idx + 1]
                    break
            else:
                summary = summary[:147] + "..."
        
        role_label = "User" if role.lower() == "user" else "Atlas"
        return f"[{timestamp}] {role_label}: {summary}"
    
    async def store_message(
        self,
        role: str,
        content: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        """Store a single message with cleaning and indexing.
        
        Args:
            role: Message role (user, assistant, system)
            content: Message content
            metadata: Optional metadata
            
        Returns:
            Document ID
        """
        # Clean content
        cleaned = self._clean_content(content)
        if not cleaned:
            return ""
        
        # Generate document ID
        timestamp = datetime.now().isoformat()
        doc_id = hashlib.sha256(f"{timestamp}{cleaned}".encode()).hexdigest()[:8]
        
        # Create compact version for storage
        compact = self._truncate_content(cleaned)
        
        # Store in session (hot memory)
        self.session_messages.append({
            'id': doc_id,
            'role': role,
            'content': compact,
            'full_content': cleaned if len(cleaned) != len(compact) else None,
            'timestamp': timestamp,
            'metadata': metadata,
        })
        
        # Index in SQLite for semantic + keyword search
        if self.config.use_embeddings:
            self.sqlite_store.add(
                doc_id=doc_id,
                content=compact,
                role=role,
                session_id=self.session_id,
                source="conversation",
                metadata={
                    'timestamp': timestamp,
                    **(metadata or {}),
                },
                created_at=timestamp,
            )
        
        # Append to daily markdown log (human-readable)
        self._append_to_daily_log(timestamp, role, doc_id, compact)
        
        # Save session state
        self._save_session()
        
        return doc_id
    
    def _append_to_daily_log(
        self, timestamp: str, role: str, doc_id: str, content: str
    ):
        """Append a message entry to today's daily log file."""
        today = self.today_file
        
        # Create file with header if it doesn't exist
        if not today.exists():
            date_str = datetime.now().strftime("%A, %B %d, %Y")
            today.write_text(f"# Daily Log — {date_str}\n\n")
        
        entry = (
            f"## [{timestamp[:19]}] {role.upper()} (ID: {doc_id})\n"
            f"{content}\n\n"
        )
        
        with open(today, "a") as f:
            f.write(entry)
    
    async def store_conversation(
        self,
        messages: list[dict[str, str]],
        metadata: Optional[dict[str, Any]] = None,
    ) -> list[str]:
        """Store multiple messages from a conversation."""
        doc_ids = []
        for msg in messages:
            doc_id = await self.store_message(
                role=msg.get("role", "unknown"),
                content=msg.get("content", ""),
                metadata=metadata,
            )
            if doc_id:
                doc_ids.append(doc_id)
        return doc_ids
    
    async def recall(
        self,
        query: str,
        n_results: int = 5,
        collection: str = "conversations",
    ) -> list[dict[str, Any]]:
        """Retrieve relevant memories using hybrid search.
        
        Combines hot memory (current session) with SQLite hybrid
        search (BM25 + vector similarity + temporal decay + MMR).
        
        Args:
            query: Search query
            n_results: Number of results to return
            collection: Which collection to search
            
        Returns:
            List of relevant memories with content and metadata
        """
        memories = []
        token_count = 0
        
        # First, include recent session messages (hot memory)
        hot_messages = self.session_messages[-self.config.hot_memory_size:]
        for msg in hot_messages:
            content = msg.get('content', '')
            if self.config.fits_in_budget(content, token_count):
                memories.append({
                    'content': self._create_compact_summary(msg['role'], content),
                    'metadata': {
                        'source': 'hot',
                        'role': msg['role'],
                        'timestamp': msg.get('timestamp'),
                    },
                    'relevance': 1.0,  # Hot memory always relevant
                })
                token_count += self.config.estimate_tokens(content)
        
        # Use hybrid search for additional context (warm memory)
        if self.config.use_embeddings:
            source_filter = None
            if collection == "knowledge":
                source_filter = "knowledge"
            
            search_results = self.sqlite_store.hybrid_search(
                query=query,
                n_results=n_results * 2,  # Get extra to filter
                source_filter=source_filter,
            )
            
            # Add semantically relevant memories within budget
            hot_ids = {
                m.get('id')
                for m in self.session_messages[-self.config.hot_memory_size:]
            }
            
            for result in search_results:
                if result['id'] in hot_ids:
                    continue  # Already included in hot memory
                
                content = result['content']
                if self.config.fits_in_budget(content, token_count):
                    role = result.get('role', 'unknown')
                    memories.append({
                        'content': self._create_compact_summary(role, content),
                        'metadata': {
                            'source': 'semantic',
                            'similarity': result['similarity'],
                            'vector_score': result.get('vector_score', 0),
                            'bm25_score': result.get('bm25_score', 0),
                            **(result.get('metadata') or {}),
                        },
                        'relevance': result['similarity'],
                    })
                    token_count += self.config.estimate_tokens(content)
                    
                    if len(memories) >= n_results:
                        break
        
        # Fill remaining slots with recent daily log entries if needed
        if len(memories) < n_results:
            remaining = n_results - len(memories)
            await self._recall_from_daily_logs(query, remaining, memories, token_count)
        
        return memories
    
    async def _recall_from_daily_logs(
        self,
        query: str,
        n_results: int,
        memories: list,
        token_count: int,
    ):
        """Fallback recall from daily log files (recency-based)."""
        # Load today and yesterday's logs
        for days_ago in range(self.config.daily_log_load_days):
            date = datetime.now() - timedelta(days=days_ago)
            log_file = self.daily_dir / f"{date.strftime('%Y-%m-%d')}.md"
            
            if not log_file.exists():
                continue
            
            content = log_file.read_text()
            sections = content.split("\n## ")[1:]  # Skip header
            
            # Get latest entries
            latest = sections[-n_results:] if sections else []
            
            for section in reversed(latest):
                lines = section.strip().split("\n")
                if not lines:
                    continue
                
                header = lines[0]
                body = "\n".join(lines[1:]).strip()
                
                if self.config.fits_in_budget(body, token_count):
                    memories.append({
                        'content': body[:1000],
                        'metadata': {'header': header, 'source': 'daily_log'},
                        'relevance': 0.5,
                    })
                    token_count += self.config.estimate_tokens(body)
                    
                    if len(memories) >= n_results:
                        return
    
    async def store_knowledge(
        self,
        fact: str,
        source: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        """Store a piece of knowledge (cold memory)."""
        timestamp = datetime.now().isoformat()
        doc_id = hashlib.sha256(fact.encode()).hexdigest()[:8]
        
        # Clean the fact
        cleaned = self._clean_content(fact)
        
        # Append to knowledge markdown file
        entry = (
            f"## [{timestamp[:19]}] KNOWLEDGE (ID: {doc_id})\n"
            f"**Source:** {source or 'conversation'}\n\n"
            f"{cleaned}\n\n"
        )
        
        with open(self.knowledge_file, "a") as f:
            f.write(entry)
        
        # Index in SQLite
        if self.config.use_embeddings:
            self.sqlite_store.add(
                doc_id=f"knowledge_{doc_id}",
                content=cleaned,
                source="knowledge",
                metadata={
                    'type': 'knowledge',
                    'fact_source': source,
                    'timestamp': timestamp,
                    **(metadata or {}),
                },
                created_at=timestamp,
            )
        
        return doc_id
    
    async def search_knowledge(
        self,
        query: str,
        n_results: int = 5,
    ) -> list[dict[str, Any]]:
        """Search knowledge base."""
        if self.config.use_embeddings:
            results = self.sqlite_store.hybrid_search(
                query=query,
                n_results=n_results,
                source_filter="knowledge",
            )
            return [
                {
                    'content': r['content'],
                    'metadata': r.get('metadata', {}),
                    'relevance': r['similarity'],
                }
                for r in results
            ]
        
        return await self.recall(query, n_results, collection="knowledge")
    
    def _save_session(self):
        """Save current session state to disk."""
        session_data = {
            'id': self.session_id,
            'start': self.session_start.isoformat(),
            'message_count': len(self.session_messages),
            'messages': self.session_messages[-self.config.hot_memory_size:],
        }
        
        with open(self.session_file, 'w') as f:
            json.dump(session_data, f, indent=2)
    
    def estimate_session_tokens(self) -> int:
        """Estimate total tokens in the current session."""
        total = 0
        for msg in self.session_messages:
            total += self.config.estimate_tokens(msg.get('content', ''))
        return total
    
    async def get_session_summary(self) -> str:
        """Get a summary of the current session."""
        if not self.session_messages:
            return "No messages in current session."
        
        summaries = []
        for msg in self.session_messages[-10:]:
            role = "User" if msg['role'] == 'user' else "Atlas"
            content = msg['content'][:100]
            summaries.append(f"- {role}: {content}")
        
        return (
            f"Session {self.session_id} "
            f"({len(self.session_messages)} messages):\n"
            + "\n".join(summaries)
        )
    
    def get_context_for_prompt(self, query: str = "") -> str:
        """Get formatted context for LLM prompt injection.
        
        Returns a token-efficient context string.
        """
        if not self.session_messages:
            return ""
        
        context_parts = []
        token_count = 0
        
        # Add recent session messages
        for msg in self.session_messages[-self.config.hot_memory_size:]:
            summary = self._create_compact_summary(msg['role'], msg['content'])
            if self.config.fits_in_budget(summary, token_count):
                context_parts.append(summary)
                token_count += self.config.estimate_tokens(summary)
        
        if not context_parts:
            return ""
        
        return "Recent conversation context:\n" + "\n".join(context_parts)
    
    def get_memory_status(self) -> dict[str, Any]:
        """Get status information about the memory system."""
        return {
            'session_id': self.session_id,
            'session_messages': len(self.session_messages),
            'session_tokens': self.estimate_session_tokens(),
            'token_budget': self.config.max_context_tokens,
            'budget_used_pct': round(
                self.estimate_session_tokens() / max(self.config.max_context_tokens, 1) * 100, 1
            ),
            'total_memories': self.sqlite_store.count(),
            'conversation_memories': self.sqlite_store.count("conversation"),
            'knowledge_memories': self.sqlite_store.count("knowledge"),
            'embeddings_available': self.sqlite_store.embeddings_available,
            'daily_log_files': len(list(self.daily_dir.glob("*.md"))),
            'session_files': len(list(self.sessions_dir.glob("*.json"))),
        }
    
    def needs_compaction(self) -> bool:
        """Check if the session needs compaction.
        
        Returns True when session tokens exceed the configured
        compaction threshold (default 80% of context budget).
        """
        current = self.estimate_session_tokens()
        threshold = int(self.config.max_context_tokens * self.config.compaction_threshold)
        return current > threshold
    
    async def compact(self, llm=None, keep_last_n: int | None = None) -> str | None:
        """Summarize old messages and replace them with a compaction summary.
        
        Triggered automatically when session tokens exceed compaction_threshold,
        or manually via `atlas memory compact`.
        
        Args:
            llm: LLM instance for summarization (uses simple extractive
                 summary if None)
            keep_last_n: Number of recent messages to keep uncompacted
                         (defaults to config.compaction_keep_last)
        
        Returns:
            Compaction summary string, or None if compaction was unnecessary
        """
        keep_n = keep_last_n or self.config.compaction_keep_last
        
        if len(self.session_messages) <= keep_n:
            return None
        
        old_messages = self.session_messages[:-keep_n]
        kept_messages = self.session_messages[-keep_n:]
        
        # Generate summary
        summary = await self._summarize_messages(old_messages, llm)
        
        if not summary:
            return None
        
        # Store compaction summary as knowledge
        await self.store_knowledge(
            summary,
            source="compaction",
            metadata={'session_id': self.session_id, 'compacted_count': len(old_messages)},
        )
        
        # Replace old messages with a compact summary message
        summary_msg = {
            'id': f"compact_{self.session_id}",
            'role': 'system',
            'content': f"[Session summary]: {summary}",
            'timestamp': datetime.now().isoformat(),
            'metadata': {'type': 'compaction', 'compacted_count': len(old_messages)},
        }
        
        self.session_messages = [summary_msg] + kept_messages
        self._save_session()
        
        logger.info(
            f"Compacted {len(old_messages)} messages into summary "
            f"(kept last {len(kept_messages)})"
        )
        
        return summary
    
    async def _summarize_messages(
        self,
        messages: list[dict],
        llm=None,
    ) -> str:
        """Generate a summary of messages.
        
        Uses LLM if available, otherwise falls back to simple
        extractive summarization.
        """
        if llm is not None:
            return await self._llm_summarize(messages, llm)
        
        return self._extractive_summarize(messages)
    
    async def _llm_summarize(self, messages: list[dict], llm) -> str:
        """Summarize messages using an LLM."""
        from langchain_core.messages import HumanMessage
        
        # Format messages for the LLM
        formatted = []
        for msg in messages:
            role = msg.get('role', 'unknown')
            content = msg.get('content', '')[:500]  # Limit each message
            formatted.append(f"[{role}]: {content}")
        
        conversation_text = "\n".join(formatted)
        
        prompt = (
            "Summarize the following conversation concisely. "
            "Preserve key facts, decisions, user preferences, and action items. "
            "Be brief but accurate. Focus on information worth remembering.\n\n"
            f"Conversation ({len(messages)} messages):\n"
            f"{conversation_text}\n\n"
            "Summary:"
        )
        
        try:
            response = await llm.ainvoke([HumanMessage(content=prompt)])
            return response.content.strip()
        except Exception as e:
            logger.warning(f"LLM summarization failed, using extractive: {e}")
            return self._extractive_summarize(messages)
    
    def _extractive_summarize(self, messages: list[dict]) -> str:
        """Simple extractive summary when no LLM is available.
        
        Picks key sentences from messages based on role and position.
        """
        parts = []
        
        for msg in messages:
            role = msg.get('role', 'unknown')
            content = msg.get('content', '').strip()
            
            if not content:
                continue
            
            # Take first sentence or first 120 chars
            first_sentence = content.split('.')[0].strip()
            if len(first_sentence) > 120:
                first_sentence = first_sentence[:117] + "..."
            
            role_label = "User" if role == "user" else "Atlas"
            parts.append(f"{role_label}: {first_sentence}")
        
        if not parts:
            return ""
        
        return "Earlier in this session: " + " | ".join(parts)
    
    def prune_tool_results(
        self,
        keep_last_assistants: int = 3,
    ):
        """Trim large tool results in older messages.
        
        Tool/function results tend to be large (e.g. web search output,
        file contents). This method soft-trims them to head+tail for
        messages older than the last N assistant responses.
        
        Args:
            keep_last_assistants: Keep full tool results for messages
                                  after the last N assistant messages
        """
        max_chars = self.config.prune_soft_trim_chars
        if not self.config.prune_tool_results or max_chars <= 0:
            return
        
        head_chars = max_chars * 3 // 8  # ~37.5%
        tail_chars = max_chars * 3 // 8  # ~37.5%
        
        # Find the cutoff point: after the Nth-to-last assistant message
        assistant_indices = [
            i for i, m in enumerate(self.session_messages)
            if m.get('role') == 'assistant'
        ]
        
        if len(assistant_indices) < keep_last_assistants:
            return  # Not enough assistant messages to prune
        
        cutoff = assistant_indices[-keep_last_assistants]
        
        # Soft-trim tool results before cutoff
        trimmed_count = 0
        for i in range(cutoff):
            msg = self.session_messages[i]
            role = msg.get('role', '')
            content = msg.get('content', '')
            
            # Trim tool/function results and large system messages
            if role in ('tool', 'function', 'system') and len(content) > max_chars:
                trimmed = (
                    content[:head_chars]
                    + f"\n\n... [{len(content) - head_chars - tail_chars} chars trimmed] ...\n\n"
                    + content[-tail_chars:]
                )
                self.session_messages[i]['content'] = trimmed
                trimmed_count += 1
        
        if trimmed_count > 0:
            logger.info(f"Pruned {trimmed_count} large tool results")
            self._save_session()
    
    def clear(self, collection: str = "all"):
        """Clear stored data."""
        if collection in ("conversations", "all"):
            self.session_messages = []
            self.sqlite_store.clear("conversation")
        
        if collection in ("knowledge", "all"):
            if self.knowledge_file.exists():
                self.knowledge_file.write_text("# Knowledge Base\n\n")
            self.sqlite_store.clear("knowledge")
        
        if collection in ("embeddings", "all"):
            self.sqlite_store.clear()
        
        if collection in ("sessions", "all"):
            for f in self.sessions_dir.glob("*.json"):
                f.unlink()
    
    # --- Migration ---
    
    def _maybe_migrate(self):
        """Migrate from old format (JSON index + single conversations.md) if needed."""
        old_conv_file = self.data_dir / "conversations.md"
        old_index_file = self.data_dir / "embeddings_index.json"
        
        migrated = False
        
        # Migrate conversations.md → daily logs
        if old_conv_file.exists() and old_conv_file.stat().st_size > 30:
            self._migrate_conversations_to_daily(old_conv_file)
            migrated = True
        
        # Migrate JSON index → SQLite
        if old_index_file.exists() and old_index_file.stat().st_size > 10:
            self._migrate_json_index(old_index_file)
            migrated = True
        
        if migrated:
            logger.info("Memory migration from old format completed")
    
    def _migrate_conversations_to_daily(self, old_file: Path):
        """Split single conversations.md into daily log files."""
        try:
            content = old_file.read_text()
            sections = content.split("\n## ")
            
            # Group sections by date
            daily_entries: dict[str, list[str]] = {}
            for section in sections[1:]:  # Skip header
                # Extract timestamp from section header
                # Format: [2026-02-20T10:30:52] ROLE (ID: abc123)
                match = re.match(r'\[(\d{4}-\d{2}-\d{2})', section)
                if match:
                    date_str = match.group(1)
                    daily_entries.setdefault(date_str, []).append(section)
            
            # Write each day's entries to separate files
            for date_str, entries in daily_entries.items():
                daily_file = self.daily_dir / f"{date_str}.md"
                if not daily_file.exists():
                    try:
                        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
                        header = f"# Daily Log — {date_obj.strftime('%A, %B %d, %Y')}\n\n"
                    except ValueError:
                        header = f"# Daily Log — {date_str}\n\n"
                    
                    daily_file.write_text(
                        header + "\n## ".join([""] + entries)
                    )
            
            # Rename old file
            old_file.rename(old_file.with_suffix('.md.bak'))
            logger.info(
                f"Migrated conversations.md → {len(daily_entries)} daily log files"
            )
            
        except Exception as e:
            logger.warning(f"Failed to migrate conversations.md: {e}")
    
    def _migrate_json_index(self, old_file: Path):
        """Import entries from old JSON embedding index into SQLite."""
        try:
            with open(old_file) as f:
                data = json.load(f)
            
            count = 0
            for doc_id, entry in data.items():
                if not self.sqlite_store.exists(doc_id):
                    self.sqlite_store.add(
                        doc_id=doc_id,
                        content=entry.get('content', ''),
                        metadata=entry.get('metadata', {}),
                        source=entry.get('metadata', {}).get('type', 'conversation'),
                        created_at=entry.get('metadata', {}).get('timestamp'),
                    )
                    count += 1
            
            # Rename old file
            old_file.rename(old_file.with_suffix('.json.bak'))
            logger.info(f"Migrated {count} entries from JSON index → SQLite")
            
        except Exception as e:
            logger.warning(f"Failed to migrate JSON index: {e}")
