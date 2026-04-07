"""CLI commands for notes management."""

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Confirm

from atlas.config.paths import get_data_dir
from atlas.tools.notes import NotesManager


notes_app = typer.Typer(help="Capture and search your notes.")
console = Console()


def get_notes_manager() -> NotesManager:
    """Get initialized NotesManager."""
    data_dir = get_data_dir()
    return NotesManager(data_dir)


@notes_app.command("add")
def add_note(
    content: str = typer.Argument(..., help="Note content (include #tags inline)"),
    title: Optional[str] = typer.Option(None, "--title", "-t", help="Note title"),
):
    """Quickly capture a note.
    
    Examples:
        atlas notes add "Great meeting! #meeting #project-x"
        atlas notes add "TODO: Review PR" --title "Code Review"
    """
    manager = get_notes_manager()
    
    note = manager.create_note(content=content, title=title)
    
    tags_str = ", ".join(f"#{t}" for t in note.tags) if note.tags else "none"
    
    console.print(Panel(
        f"[bold green]✓ Note saved![/bold green]\n\n"
        f"[bold]ID:[/bold] {note.id[:8]}\n"
        f"[bold]Title:[/bold] {note.title}\n"
        f"[bold]Tags:[/bold] {tags_str}",
        title="📝 Note Created",
        border_style="green",
    ))


@notes_app.command("search")
def search_notes(
    query: str = typer.Argument(..., help="Search query"),
    tag: Optional[str] = typer.Option(None, "--tag", "-t", help="Filter by tag"),
    limit: int = typer.Option(10, "--limit", "-n", help="Max results"),
):
    """Search your notes."""
    manager = get_notes_manager()
    
    tags = [tag.lstrip('#')] if tag else None
    notes = manager.search_notes(query=query, tags=tags, limit=limit)
    
    if not notes:
        console.print(f"[yellow]No notes found matching '{query}'[/yellow]")
        return
    
    table = Table(title=f"🔍 Search Results for '{query}'")
    table.add_column("ID", style="dim", width=8)
    table.add_column("Title", style="bold")
    table.add_column("Tags")
    table.add_column("Updated", style="dim")
    
    for note in notes:
        tags_str = " ".join(f"#{t}" for t in note.tags[:3])
        date = note.updated.strftime("%b %d")
        table.add_row(note.id[:8], note.title[:40], tags_str, date)
    
    console.print(table)


@notes_app.command("list")
def list_notes(
    tag: Optional[str] = typer.Option(None, "--tag", "-t", help="Filter by tag"),
    pinned: bool = typer.Option(False, "--pinned", "-p", help="Show pinned only"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max results"),
):
    """List your notes."""
    manager = get_notes_manager()
    
    tags = [tag.lstrip('#')] if tag else None
    notes = manager.list_notes(limit=limit, tags=tags, pinned_only=pinned)
    
    if not notes:
        console.print("[yellow]No notes found.[/yellow]")
        return
    
    table = Table(title="📝 Your Notes")
    table.add_column("", width=2)  # Pin indicator
    table.add_column("ID", style="dim", width=8)
    table.add_column("Title", style="bold")
    table.add_column("Tags")
    table.add_column("Updated", style="dim")
    
    for note in notes:
        pin = "📌" if note.pinned else ""
        tags_str = " ".join(f"#{t}" for t in note.tags[:3])
        date = note.updated.strftime("%b %d %H:%M")
        table.add_row(pin, note.id[:8], note.title[:40], tags_str, date)
    
    console.print(table)
    
    total = manager.get_notes_count()
    if total > len(notes):
        console.print(f"[dim]Showing {len(notes)} of {total} notes[/dim]")


@notes_app.command("show")
def show_note(
    note_id: str = typer.Argument(..., help="Note ID (first 8 chars) or search term"),
):
    """Show full note content."""
    manager = get_notes_manager()
    
    # Try by ID prefix first
    note = manager.get_note_by_prefix(note_id)
    
    if not note:
        # Search by title/content
        notes = manager.search_notes(query=note_id, limit=1)
        if notes:
            note = notes[0]
    
    if not note:
        console.print(f"[red]Note not found: {note_id}[/red]")
        return
    
    tags_str = " ".join(f"#{t}" for t in note.tags) if note.tags else "(no tags)"
    
    console.print(Panel(
        f"[bold]Created:[/bold] {note.created.strftime('%Y-%m-%d %H:%M')}\n"
        f"[bold]Updated:[/bold] {note.updated.strftime('%Y-%m-%d %H:%M')}\n"
        f"[bold]Tags:[/bold] {tags_str}\n"
        f"[bold]ID:[/bold] {note.id}",
        title=f"📝 {note.title}",
        border_style="blue",
    ))
    
    console.print()
    console.print(Markdown(note.content))


@notes_app.command("delete")
def delete_note(
    note_id: str = typer.Argument(..., help="Note ID to delete"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Delete a note."""
    manager = get_notes_manager()
    
    # Try by ID prefix first
    note = manager.get_note_by_prefix(note_id)
    
    if not note:
        # Search by title
        notes = manager.search_notes(query=note_id, limit=1)
        if notes:
            note = notes[0]
    
    if not note:
        console.print(f"[red]Note not found: {note_id}[/red]")
        return
    
    if not force:
        if not Confirm.ask(f"Delete note '[bold]{note.title}[/bold]'?"):
            console.print("[yellow]Cancelled.[/yellow]")
            return
    
    if manager.delete_note(note.id):
        console.print(f"[green]✓ Deleted: {note.title}[/green]")
    else:
        console.print(f"[red]Failed to delete note.[/red]")


@notes_app.command("tags")
def list_tags():
    """Show all tags with usage counts."""
    manager = get_notes_manager()
    
    tags = manager.get_all_tags()
    
    if not tags:
        console.print("[yellow]No tags found. Add tags to notes using #tagname.[/yellow]")
        return
    
    table = Table(title="🏷️ Your Tags")
    table.add_column("Tag", style="bold cyan")
    table.add_column("Notes", justify="right")
    
    for tag, count in tags:
        table.add_row(f"#{tag}", str(count))
    
    console.print(table)


@notes_app.command("export")
def export_notes(
    output: Path = typer.Argument(..., help="Output directory or file"),
    format: str = typer.Option("markdown", "--format", "-f", help="Export format: markdown, json"),
    tag: Optional[str] = typer.Option(None, "--tag", "-t", help="Filter by tag"),
):
    """Export notes to a file or directory."""
    manager = get_notes_manager()
    
    tags = [tag.lstrip('#')] if tag else None
    notes = manager.list_notes(limit=1000, tags=tags)
    
    if not notes:
        console.print("[yellow]No notes to export.[/yellow]")
        return
    
    if format == "markdown":
        output.mkdir(parents=True, exist_ok=True)
        for note in notes:
            file_path = output / f"{note.slug}-{note.id[:8]}.md"
            file_path.write_text(note.to_markdown())
        console.print(f"[green]✓ Exported {len(notes)} notes to {output}[/green]")
    
    elif format == "json":
        import json
        export_data = [
            {
                "id": n.id,
                "title": n.title,
                "content": n.content,
                "tags": n.tags,
                "created": n.created.isoformat(),
                "updated": n.updated.isoformat(),
                "pinned": n.pinned,
            }
            for n in notes
        ]
        output.write_text(json.dumps(export_data, indent=2))
        console.print(f"[green]✓ Exported {len(notes)} notes to {output}[/green]")
    else:
        console.print(f"[red]Unknown format: {format}. Use 'markdown' or 'json'.[/red]")


@notes_app.command("pin")
def pin_note(
    note_id: str = typer.Argument(..., help="Note ID to pin/unpin"),
    unpin: bool = typer.Option(False, "--unpin", "-u", help="Unpin instead of pin"),
):
    """Pin or unpin a note."""
    manager = get_notes_manager()
    
    # Try by ID prefix first
    note = manager.get_note_by_prefix(note_id)
    
    if not note:
        notes = manager.search_notes(query=note_id, limit=1)
        if notes:
            note = notes[0]
    
    if not note:
        console.print(f"[red]Note not found: {note_id}[/red]")
        return
    
    new_pinned = not unpin
    manager.update_note(note.id, pinned=new_pinned)
    
    if new_pinned:
        console.print(f"[green]📌 Pinned: {note.title}[/green]")
    else:
        console.print(f"[yellow]Unpinned: {note.title}[/yellow]")
