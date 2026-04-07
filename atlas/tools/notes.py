"""Quick Notes and Knowledge Capture tools for Atlas.

Provides fast note-taking with:
- Markdown files with YAML frontmatter (human-readable, portable)
- SQLite FTS5 for fast full-text search
- Inline tag extraction (#tag in content)
- Calendar and task linking
"""

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml
from langchain_core.tools import tool


@dataclass
class Note:
    """A note with metadata."""
    id: str
    title: str
    content: str
    tags: list[str] = field(default_factory=list)
    created: datetime = field(default_factory=datetime.now)
    updated: datetime = field(default_factory=datetime.now)
    links: dict = field(default_factory=dict)  # calendar, tasks, notes
    pinned: bool = False
    
    @property
    def file_path(self) -> Path:
        """Generate file path from date and id."""
        date = self.created
        return Path(f"{date.year}/{date.month:02d}/{date.day:02d}/{self.slug}-{self.id[:8]}.md")
    
    @property
    def slug(self) -> str:
        """Generate URL-safe slug from title."""
        slug = re.sub(r'[^a-z0-9]+', '-', self.title.lower()).strip('-')[:50]
        return slug if slug else "note"
    
    def to_markdown(self) -> str:
        """Serialize note to Markdown with YAML frontmatter."""
        frontmatter = {
            "id": self.id,
            "title": self.title,
            "created": self.created.isoformat(),
            "updated": self.updated.isoformat(),
            "tags": self.tags,
            "links": self.links if self.links else {},
            "pinned": self.pinned,
        }
        
        yaml_str = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True)
        return f"---\n{yaml_str}---\n\n{self.content}"
    
    @classmethod
    def from_markdown(cls, content: str, file_path: Path) -> 'Note':
        """Parse note from Markdown file."""
        # Split frontmatter and content
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                frontmatter_str = parts[1]
                body = parts[2].strip()
                frontmatter = yaml.safe_load(frontmatter_str) or {}
            else:
                frontmatter = {}
                body = content.strip()
        else:
            frontmatter = {}
            body = content.strip()
        
        return cls(
            id=frontmatter.get("id", str(uuid.uuid4())),
            title=frontmatter.get("title", file_path.stem),
            content=body,
            tags=frontmatter.get("tags", []) or [],
            created=datetime.fromisoformat(frontmatter["created"]) if "created" in frontmatter else datetime.now(),
            updated=datetime.fromisoformat(frontmatter["updated"]) if "updated" in frontmatter else datetime.now(),
            links=frontmatter.get("links", {}) or {},
            pinned=frontmatter.get("pinned", False),
        )


class NotesManager:
    """Manage notes storage and retrieval."""
    
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.notes_dir = self.data_dir / "notes"
        self.notes_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.notes_dir / "index.db"
        self._init_db()
    
    def _init_db(self):
        """Initialize SQLite database with FTS5."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Main notes table (metadata)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                file_path TEXT NOT NULL,
                tags TEXT,
                created DATETIME,
                updated DATETIME,
                pinned BOOLEAN DEFAULT 0
            )
        """)
        
        # Full-text search table
        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
                id,
                title,
                content,
                tags,
                tokenize='porter unicode61'
            )
        """)
        
        conn.commit()
        conn.close()
    
    def create_note(
        self,
        content: str,
        title: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> Note:
        """Create a new note.
        
        Args:
            content: Note content (can include tags as #tag)
            title: Optional title (auto-generated if not provided)
            tags: Optional explicit tags
            
        Returns:
            Created Note object
        """
        # Extract inline tags from content (e.g., #project-x)
        inline_tags = re.findall(r'#([a-zA-Z0-9_-]+)', content)
        all_tags = list(set((tags or []) + inline_tags))
        
        # Remove inline tags from content for cleaner storage
        clean_content = re.sub(r'\s*#[a-zA-Z0-9_-]+', '', content).strip()
        
        # Auto-generate title if not provided
        if not title:
            # Use first line or first N characters
            first_line = clean_content.split('\n')[0][:60]
            title = first_line if first_line else "Untitled Note"
        
        note = Note(
            id=str(uuid.uuid4()),
            title=title,
            content=clean_content,
            tags=all_tags,
            created=datetime.now(),
            updated=datetime.now(),
        )
        
        # Save to file
        file_path = self.notes_dir / note.file_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(note.to_markdown())
        
        # Index in database
        self._index_note(note, file_path)
        
        return note
    
    def _index_note(self, note: Note, file_path: Path):
        """Add note to search index."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Insert/update metadata
        cursor.execute("""
            INSERT OR REPLACE INTO notes (id, title, file_path, tags, created, updated, pinned)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            note.id,
            note.title,
            str(file_path.relative_to(self.notes_dir)),
            json.dumps(note.tags),
            note.created.isoformat(),
            note.updated.isoformat(),
            note.pinned,
        ))
        
        # Update FTS index
        cursor.execute("DELETE FROM notes_fts WHERE id = ?", (note.id,))
        cursor.execute("""
            INSERT INTO notes_fts (id, title, content, tags)
            VALUES (?, ?, ?, ?)
        """, (
            note.id,
            note.title,
            note.content,
            " ".join(note.tags),
        ))
        
        conn.commit()
        conn.close()
    
    def get_note(self, note_id: str) -> Optional[Note]:
        """Get a specific note by ID."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT file_path FROM notes WHERE id = ?", (note_id,))
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return None
        
        file_path = self.notes_dir / row[0]
        if not file_path.exists():
            return None
        
        return Note.from_markdown(file_path.read_text(), file_path)
    
    def get_note_by_prefix(self, id_prefix: str) -> Optional[Note]:
        """Get a note by ID prefix."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT id, file_path FROM notes WHERE id LIKE ?", (f"{id_prefix}%",))
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return None
        
        note_id, file_path_str = row
        file_path = self.notes_dir / file_path_str
        if not file_path.exists():
            return None
        
        return Note.from_markdown(file_path.read_text(), file_path)
    
    def search_notes(
        self,
        query: str,
        tags: Optional[list[str]] = None,
        limit: int = 20,
    ) -> list[Note]:
        """Search notes using full-text search.
        
        Args:
            query: Search query (supports FTS5 syntax)
            tags: Optional tag filter
            limit: Maximum results
            
        Returns:
            List of matching notes
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        if query:
            # Full-text search - escape special characters for safety
            safe_query = query.replace('"', '""')
            try:
                cursor.execute("""
                    SELECT n.id, n.file_path, n.title,
                           bm25(notes_fts) as rank
                    FROM notes_fts fts
                    JOIN notes n ON n.id = fts.id
                    WHERE notes_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                """, (safe_query, limit))
            except sqlite3.OperationalError:
                # Fallback to simple LIKE search if FTS fails
                cursor.execute("""
                    SELECT id, file_path, title, 0 as rank
                    FROM notes
                    WHERE title LIKE ? OR id LIKE ?
                    ORDER BY updated DESC
                    LIMIT ?
                """, (f"%{query}%", f"{query}%", limit))
        else:
            # Recent notes
            cursor.execute("""
                SELECT id, file_path, title, 0 as rank
                FROM notes
                ORDER BY updated DESC
                LIMIT ?
            """, (limit,))
        
        results = []
        for row in cursor.fetchall():
            note_id, file_path, title, rank = row
            file_full_path = self.notes_dir / file_path
            if file_full_path.exists():
                note = Note.from_markdown(file_full_path.read_text(), file_full_path)
                results.append(note)
        
        conn.close()
        
        # Filter by tags if specified
        if tags:
            results = [n for n in results if any(t in n.tags for t in tags)]
        
        return results
    
    def list_notes(
        self,
        limit: int = 20,
        tags: Optional[list[str]] = None,
        pinned_only: bool = False,
    ) -> list[Note]:
        """List recent notes."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        query = "SELECT id, file_path FROM notes"
        params: list = []
        
        conditions = []
        if pinned_only:
            conditions.append("pinned = 1")
        if tags:
            # Filter by any of the tags
            tag_conditions = " OR ".join(["tags LIKE ?" for _ in tags])
            conditions.append(f"({tag_conditions})")
            params.extend([f'%"{t}"%' for t in tags])
        
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        
        query += " ORDER BY pinned DESC, updated DESC LIMIT ?"
        params.append(limit)
        
        cursor.execute(query, params)
        
        results = []
        for row in cursor.fetchall():
            note_id, file_path = row
            file_full_path = self.notes_dir / file_path
            if file_full_path.exists():
                note = Note.from_markdown(file_full_path.read_text(), file_full_path)
                results.append(note)
        
        conn.close()
        return results
    
    def update_note(
        self,
        note_id: str,
        content: Optional[str] = None,
        title: Optional[str] = None,
        tags: Optional[list[str]] = None,
        pinned: Optional[bool] = None,
        links: Optional[dict] = None,
    ) -> Optional[Note]:
        """Update a note's content or metadata."""
        note = self.get_note(note_id)
        if not note:
            return None
        
        # Apply updates
        if content is not None:
            note.content = content
        if title is not None:
            note.title = title
        if tags is not None:
            note.tags = tags
        if pinned is not None:
            note.pinned = pinned
        if links is not None:
            note.links = links
        
        note.updated = datetime.now()
        
        # Get current file path
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT file_path FROM notes WHERE id = ?", (note_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            file_path = self.notes_dir / row[0]
            file_path.write_text(note.to_markdown())
            self._index_note(note, file_path)
        
        return note
    
    def delete_note(self, note_id: str) -> bool:
        """Delete a note."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT file_path FROM notes WHERE id = ?", (note_id,))
        row = cursor.fetchone()
        
        if not row:
            conn.close()
            return False
        
        file_path = self.notes_dir / row[0]
        
        # Delete from database
        cursor.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        cursor.execute("DELETE FROM notes_fts WHERE id = ?", (note_id,))
        conn.commit()
        conn.close()
        
        # Delete file
        if file_path.exists():
            file_path.unlink()
        
        return True
    
    def get_all_tags(self) -> list[tuple[str, int]]:
        """Get all tags with their usage count."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT tags FROM notes")
        
        tag_counts: dict[str, int] = {}
        for row in cursor.fetchall():
            tags = json.loads(row[0]) if row[0] else []
            for tag in tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
        
        conn.close()
        
        return sorted(tag_counts.items(), key=lambda x: -x[1])
    
    def get_notes_count(self) -> int:
        """Get total number of notes."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM notes")
        count = cursor.fetchone()[0]
        conn.close()
        return count


def create_notes_tools(data_dir: Path):
    """Create LangChain tools for notes management.
    
    Args:
        data_dir: Base data directory for Atlas
        
    Returns:
        List of LangChain tools for note operations
    """
    manager = NotesManager(data_dir)
    
    @tool
    def quick_note(content: str, title: Optional[str] = None) -> str:
        """Quickly capture a note or idea.
        
        Use this tool when the user wants to save a thought, idea, 
        meeting notes, or any piece of information for later.
        
        The content can include hashtags for automatic tagging:
        "Great idea about caching #project-x #architecture"
        
        Args:
            content: The note content (can include #tags inline)
            title: Optional title (auto-generated from content if not provided)
            
        Returns:
            Confirmation with note ID and tags
        """
        note = manager.create_note(content=content, title=title)
        
        tags_str = ", ".join(f"#{t}" for t in note.tags) if note.tags else "none"
        
        return (
            f"✓ Note saved!\n"
            f"**ID:** {note.id[:8]}\n"
            f"**Title:** {note.title}\n"
            f"**Tags:** {tags_str}"
        )
    
    @tool
    def search_notes(query: str, tags: Optional[str] = None) -> str:
        """Search through your notes.
        
        Use this tool to find notes by content, title, or tags.
        
        Args:
            query: Search terms to look for in notes
            tags: Optional comma-separated tags to filter by (e.g., "meeting,project-x")
            
        Returns:
            Matching notes with snippets
        """
        tag_list = [t.strip().lstrip('#') for t in tags.split(',')] if tags else None
        
        notes = manager.search_notes(query=query, tags=tag_list, limit=10)
        
        if not notes:
            return f"No notes found matching '{query}'"
        
        results = [f"Found {len(notes)} note(s):\n"]
        
        for note in notes:
            tags_str = " ".join(f"#{t}" for t in note.tags[:3])
            preview = note.content[:100].replace('\n', ' ')
            if len(note.content) > 100:
                preview += "..."
            
            results.append(
                f"**{note.title}** ({note.id[:8]})\n"
                f"  {tags_str}\n"
                f"  _{preview}_\n"
            )
        
        return "\n".join(results)
    
    @tool
    def list_notes(
        filter_by: str = "recent",
        tag: Optional[str] = None,
        limit: int = 10
    ) -> str:
        """List your notes.
        
        Use this to see an overview of saved notes.
        
        Args:
            filter_by: Filter option - "recent", "pinned", or "all"
            tag: Optional tag to filter by (without the # prefix)
            limit: Maximum notes to show (default 10)
            
        Returns:
            List of notes with titles and dates
        """
        tags = [tag.lstrip('#')] if tag else None
        pinned_only = filter_by == "pinned"
        
        notes = manager.list_notes(limit=limit, tags=tags, pinned_only=pinned_only)
        
        if not notes:
            return "No notes found."
        
        results = [f"📝 Your Notes ({len(notes)} shown):\n"]
        
        for note in notes:
            pin = "📌 " if note.pinned else ""
            date = note.updated.strftime("%b %d")
            tags_str = " ".join(f"#{t}" for t in note.tags[:2])
            
            results.append(f"{pin}**{note.title}** ({note.id[:8]}) - {date} {tags_str}")
        
        return "\n".join(results)
    
    @tool
    def read_note(note_identifier: str) -> str:
        """Read the full content of a note.
        
        Use this to view a complete note when you have its ID or title.
        
        Args:
            note_identifier: Note ID (first 8 chars) or partial title to search for
            
        Returns:
            Full note content with metadata
        """
        # Try to find by ID prefix first
        note = manager.get_note_by_prefix(note_identifier)
        
        if not note:
            # Search by title/content
            notes = manager.search_notes(query=note_identifier, limit=1)
            if notes:
                note = notes[0]
        
        if not note:
            return f"Note not found: {note_identifier}"
        
        tags_str = " ".join(f"#{t}" for t in note.tags) if note.tags else "none"
        
        return (
            f"# {note.title}\n\n"
            f"**ID:** {note.id[:8]}\n"
            f"**Created:** {note.created.strftime('%Y-%m-%d %H:%M')}\n"
            f"**Tags:** {tags_str}\n\n"
            f"---\n\n"
            f"{note.content}"
        )
    
    @tool
    def delete_note(note_identifier: str) -> str:
        """Delete a note.
        
        Use this to permanently remove a note.
        
        Args:
            note_identifier: Note ID (first 8 chars) or partial title to match
            
        Returns:
            Confirmation of deletion
        """
        # Try to find by ID prefix first
        note = manager.get_note_by_prefix(note_identifier)
        
        if not note:
            # Search by title/content
            notes = manager.search_notes(query=note_identifier, limit=1)
            if notes:
                note = notes[0]
        
        if not note:
            return f"Note not found: {note_identifier}"
        
        if manager.delete_note(note.id):
            return f"✓ Deleted note: {note.title}"
        else:
            return f"Failed to delete note: {note.title}"
    
    @tool
    def list_tags() -> str:
        """List all tags used in your notes.
        
        Returns:
            Tags sorted by usage frequency with note counts
        """
        tags = manager.get_all_tags()
        
        if not tags:
            return "No tags found. Add tags to notes using #tagname in your note content."
        
        results = ["🏷️ Your Tags:\n"]
        
        for tag, count in tags[:20]:
            results.append(f"  #{tag} ({count} note{'s' if count > 1 else ''})")
        
        return "\n".join(results)
    
    return [quick_note, search_notes, list_notes, read_note, delete_note, list_tags]
