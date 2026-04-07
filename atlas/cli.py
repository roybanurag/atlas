"""CLI interface for Atlas agent."""

import asyncio
from pathlib import Path
from typing import Optional
from datetime import datetime

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from atlas.config.paths import get_config_dir, get_data_dir
from .secrets_cli import secrets_command
from .notes_cli import notes_app

# Pre-load the master encryption key to minimize keychain prompts
# This is done at import time so the password prompt happens once at startup
try:
    from atlas.security.token_encryption import preload_key
    preload_key()
except Exception:
    pass  # Ignore if preload fails (e.g., first run before keyring setup)

app = typer.Typer(
    name="atlas",
    help="Atlas - Your Privacy-Focused Personal AI Agent.\n\nInteract with your local LLMs, manage permissions, and control your data.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

# Register secrets command
app.command(name="secrets")(secrets_command)

# Register notes subcommand
app.add_typer(notes_app, name="notes")

# Memory management subcommand
memory_app = typer.Typer(
    name="memory",
    help="Manage Atlas memory system (status, compact, clear).",
    no_args_is_help=True,
)
app.add_typer(memory_app, name="memory")


@memory_app.command("status")
def memory_status():
    """Show memory system status."""
    from atlas.memory import MemoryStore, MemoryConfig
    from atlas.config.paths import get_data_dir
    from rich.table import Table
    
    data_dir = get_data_dir() / "memory"
    
    if not data_dir.exists():
        console.print("[yellow]No memory directory found.[/yellow]")
        return
    
    config = MemoryConfig()
    store = MemoryStore(data_dir=data_dir, config=config)
    status = store.get_memory_status()
    
    table = Table(title="Memory System Status", show_header=False, border_style="blue")
    table.add_column("Key", style="cyan")
    table.add_column("Value", style="white")
    
    table.add_row("Session ID", status['session_id'])
    table.add_row("Session Messages", str(status['session_messages']))
    table.add_row("Session Tokens", f"{status['session_tokens']} / {status['token_budget']}")
    table.add_row("Budget Used", f"{status['budget_used_pct']}%")
    table.add_row("", "")
    table.add_row("Total Memories", str(status['total_memories']))
    table.add_row("  Conversations", str(status['conversation_memories']))
    table.add_row("  Knowledge", str(status['knowledge_memories']))
    table.add_row("", "")
    table.add_row("Embeddings", "✅ Available" if status['embeddings_available'] else "❌ Not available")
    table.add_row("Daily Log Files", str(status['daily_log_files']))
    table.add_row("Session Files", str(status['session_files']))
    
    console.print(table)


@memory_app.command("compact")
def memory_compact():
    """Manually compact the current session (summarize old messages)."""
    from atlas.memory import MemoryStore, MemoryConfig
    from atlas.config.paths import get_data_dir
    
    data_dir = get_data_dir() / "memory"
    
    if not data_dir.exists():
        console.print("[yellow]No memory directory found.[/yellow]")
        return
    
    config = MemoryConfig()
    store = MemoryStore(data_dir=data_dir, config=config)
    
    msg_count = len(store.session_messages)
    if msg_count <= config.compaction_keep_last:
        console.print(
            f"[green]Nothing to compact ({msg_count} messages, "
            f"threshold: {config.compaction_keep_last}).[/green]"
        )
        return
    
    if not Confirm.ask(
        f"Compact {msg_count - config.compaction_keep_last} old messages "
        f"(keeping last {config.compaction_keep_last})?"
    ):
        console.print("[dim]Cancelled.[/dim]")
        return
    
    async def do_compact():
        return await store.compact()
    
    summary = asyncio.run(do_compact())
    
    if summary:
        console.print(f"\n[green]✓ Compacted to:[/green]\n{summary[:500]}")
    else:
        console.print("[yellow]Compaction produced no summary.[/yellow]")


@memory_app.command("consolidate")
def memory_consolidate(
    days: int = typer.Option(7, "--days", "-d", help="Number of days to look back"),
):
    """Extract durable knowledge from recent daily logs."""
    from atlas.memory import MemoryStore, MemoryConfig, consolidate_memories
    from atlas.config.paths import get_data_dir
    
    data_dir = get_data_dir() / "memory"
    
    if not data_dir.exists():
        console.print("[yellow]No memory directory found.[/yellow]")
        return
    
    config = MemoryConfig()
    store = MemoryStore(data_dir=data_dir, config=config)
    
    console.print(f"[dim]Scanning last {days} days of daily logs...[/dim]")
    
    async def do_consolidate():
        return await consolidate_memories(store, lookback_days=days)
    
    facts = asyncio.run(do_consolidate())
    
    if facts:
        console.print(f"\n[green]✓ Extracted {len(facts)} knowledge items:[/green]")
        for i, fact in enumerate(facts, 1):
            console.print(f"  {i}. {fact[:200]}")
    else:
        console.print("[dim]No new knowledge extracted.[/dim]")


async def permission_ui_handler(request: dict) -> dict:
    """Handle permission requests via CLI."""
    console.print()
    console.print(Panel(
        f"[bold yellow]Permission Request[/bold yellow]\n\n"
        f"[bold]{request.get('permission')}[/bold]: {request.get('description')}\n"
        f"Scope: [cyan]{request.get('scope')}[/cyan]\n"
        f"Level: [red]{request.get('level')}[/red]",
        border_style="yellow",
    ))
    
    granted = Confirm.ask("Allow this action?", default=False)
    
    if granted:
        duration = Prompt.ask(
            "Grant for how long?",
            choices=["once", "session", "hour", "day", "forever"],
            default="session",
        )
    else:
        duration = "once"
    
    return {"granted": granted, "duration": duration}


@app.command()
def briefing(
    date: Optional[str] = typer.Option(None, "--date", "-d", help="Date for briefing: 'today', 'tomorrow', or YYYY-MM-DD"),
    no_weather: bool = typer.Option(False, "--no-weather", help="Skip weather section"),
    no_notes: bool = typer.Option(False, "--no-notes", help="Skip notes section"),
):
    """Generate your daily briefing.
    
    Compiles your calendar events, unread emails, weather forecast,
    and pinned notes into a personalized summary.
    
    Examples:
        atlas briefing              # Today's briefing
        atlas briefing -d tomorrow  # Tomorrow's preview
    """
    from rich.markdown import Markdown
    from atlas.tools.briefing import BriefingGenerator
    from datetime import datetime, timedelta
    
    data_dir = get_data_dir()
    generator = BriefingGenerator(data_dir)
    
    # Parse target date
    target_date = datetime.now()
    if date:
        if date.lower() == "tomorrow":
            target_date = datetime.now() + timedelta(days=1)
        elif date.lower() == "yesterday":
            target_date = datetime.now() - timedelta(days=1)
        elif date.lower() != "today":
            try:
                target_date = datetime.fromisoformat(date)
            except ValueError:
                console.print(f"[red]Invalid date format: {date}[/red]")
                return
    
    console.print("[dim]Generating briefing...[/dim]\n")
    
    brief = generator.generate(
        target_date=target_date,
        include_weather=not no_weather,
        include_notes=not no_notes,
    )
    
    # Render as rich markdown
    console.print(Markdown(brief.to_markdown()))


@app.command()
def task(
    title: str = typer.Argument(..., help="Task title"),
    notes: str = typer.Option("", "--notes", "-n", help="Task notes/description"),
    due: str = typer.Option("", "--due", "-d", help="Due date: 'today', 'tomorrow', or YYYY-MM-DD"),
    list_id: str = typer.Option("@default", "--list", "-l", help="Task list ID"),
):
    """Create a Google Task.
    
    Examples:
        atlas task "Buy groceries"
        atlas task "Review PR" --due tomorrow
        atlas task "Submit report" -d 2026-02-20 -n "Q1 financials"
    """
    from atlas.tools.google_tasks import _get_tasks_service, _parse_due_date
    
    try:
        service = _get_tasks_service()
        
        task_body = {
            'title': title,
            'status': 'needsAction',
        }
        
        if notes:
            task_body['notes'] = notes
        
        if due:
            task_body['due'] = _parse_due_date(due)
        
        result = service.tasks().insert(
            tasklist=list_id,
            body=task_body,
        ).execute()
        
        console.print(f"\n[green]✅ Task created![/green]")
        console.print(f"  [bold]Title:[/bold] {title}")
        if notes:
            console.print(f"  [bold]Notes:[/bold] {notes}")
        if due:
            console.print(f"  [bold]Due:[/bold] {due}")
        console.print(f"  [dim]ID: {result.get('id', 'unknown')}[/dim]")
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
    except Exception as e:
        console.print(f"[red]Error creating task: {e}[/red]")


@app.command()
def tasks(
    list_id: str = typer.Option("@default", "--list", "-l", help="Task list ID"),
    show_completed: bool = typer.Option(False, "--completed", "-c", help="Show completed tasks"),
):
    """List your Google Tasks.
    
    Examples:
        atlas tasks
        atlas tasks --completed
    """
    from atlas.tools.google_tasks import _get_tasks_service
    from rich.markdown import Markdown
    
    try:
        service = _get_tasks_service()
        
        results = service.tasks().list(
            tasklist=list_id,
            maxResults=100,
            showCompleted=show_completed,
            showHidden=False,
        ).execute()
        
        task_items = results.get('items', [])
        
        if not task_items:
            console.print("[dim]No tasks found. 🎉[/dim]")
            return
        
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        
        lines = ["# Your Tasks\n"]
        
        overdue = []
        due_today = []
        upcoming = []
        no_date = []
        completed = []
        
        for t in task_items:
            title = t.get('title', '').strip()
            if not title:
                continue
            status = t.get('status', 'needsAction')
            due_val = t.get('due', '')
            
            if status == 'completed':
                completed.append(t)
                continue
            
            if due_val:
                due_date = due_val[:10]
                if due_date < today_str:
                    overdue.append(t)
                elif due_date == today_str:
                    due_today.append(t)
                else:
                    upcoming.append(t)
            else:
                no_date.append(t)
        
        if overdue:
            lines.append(f"### 🔴 Overdue ({len(overdue)})")
            for t in overdue:
                lines.append(f"- {t['title']} *(due {t.get('due', '')[:10]})*")
            lines.append("")
        
        if due_today:
            lines.append(f"### 📋 Due Today ({len(due_today)})")
            for t in due_today:
                lines.append(f"- {t['title']}")
            lines.append("")
        
        if upcoming:
            lines.append(f"### 📅 Upcoming ({len(upcoming)})")
            for t in upcoming:
                lines.append(f"- {t['title']} *(due {t.get('due', '')[:10]})*")
            lines.append("")
        
        if no_date:
            lines.append(f"### 📝 No Due Date ({len(no_date)})")
            for t in no_date:
                lines.append(f"- {t['title']}")
            lines.append("")
        
        if completed and show_completed:
            lines.append(f"### ✅ Completed ({len(completed)})")
            for t in completed:
                lines.append(f"- ~~{t['title']}~~")
            lines.append("")
        
        console.print(Markdown("\n".join(lines)))
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
    except Exception as e:
        console.print(f"[red]Error listing tasks: {e}[/red]")


@app.command()
def chat(
    message: Optional[str] = typer.Argument(None, help="The message or query to send to Atlas. If omitted, starts interactive mode."),
    model: str = typer.Option("qwen3:14b", "--model", "-m", help="Model to use. Format: 'provider:model' (e.g. 'openai:gpt-4o', 'anthropic:claude-sonnet-4-20250514'). Bare names default to Ollama."),
    privacy: str = typer.Option("local", "--privacy", "-p", help="Privacy mode: 'local' (offline tools only) or 'remote' (allows external APIs like search)."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose mode to see detailed LLM responses, tool calls, and agent thinking."),
    trust: str = typer.Option("none", "--trust", "-t", help="Trust level: 'none' (ask every time), 'low' (auto-approve low-risk), 'medium' (auto-approve low+medium)."),
):
    """Start a conversation with Atlas.
    
    You can send a single message or start an interactive chat session.
    Atlas uses local LLMs via Ollama and respects your permission settings.
    """
    from atlas.gateway.server import start_server_in_background
    start_server_in_background()
    if trust not in ("none", "low", "medium"):
        console.print(f"[red]Invalid trust level: {trust}. Use 'none', 'low', or 'medium'.[/red]")
        return
    asyncio.run(_chat(message, model, privacy, verbose, trust))



async def _chat(message: Optional[str], model: str, privacy: str, verbose: bool = False, trust: str = "none"):
    """Async chat implementation."""
    from langgraph.checkpoint.memory import MemorySaver
    
    from atlas.config.model_manager import ModelManager
    from atlas.graph import create_agent_graph, run_agent
    from atlas.memory import MemoryStore
    from atlas.security import AuditLogger, PermissionManager
    
    console.print(f"\n[dim]Initializing Atlas with model: {model}[/dim]")
    if verbose:
        console.print("[bold yellow]🔍 Verbose mode enabled[/bold yellow]")
    
    # Initialize components
    data_dir = get_data_dir()
    model_mgr = ModelManager()
    
    console.print("[dim]→ Loading LLM...[/dim]")
    llm = model_mgr.create_llm(model)
    
    console.print("[dim]→ Initializing memory store...[/dim]")
    memory_store = MemoryStore(data_dir / "memory")
    
    console.print("[dim]→ Setting up security...[/dim]")
    audit_logger = AuditLogger(data_dir / "audit")
    permission_manager = PermissionManager(
        ui_handler=permission_ui_handler,
        audit_logger=audit_logger,
        trust_level=trust,
    )
    if trust != "none":
        console.print(f"[dim]  ✓ Trust level: [yellow]{trust}[/yellow] (auto-granting {trust.upper()}-level permissions)[/dim]")
        
    # First-run experience: if no permissions are configured, prompt the user.
    if not permission_manager.grants and not permission_manager.get_active_preset():
        console.print(Panel(
            "[bold green]Welcome to Atlas![/bold green]\n\n"
            "To get started, let's configure your default permissions.\n"
            "Choosing a profile reduces how often Atlas will interrupt you to ask for access.",
            border_style="green"
        ))
        console.print("  [cyan]minimal[/cyan]  — Ask for everything (most secure)")
        console.print("  [cyan]reader[/cyan]   — Read-only access to all services")
        console.print("  [cyan]standard[/cyan] — Reader + write notes and tasks")
        console.print("  [cyan]full[/cyan]     — Standard + send email, modify calendar, upload to drive")
        console.print()
        
        valid_presets = list(PermissionManager.PERMISSION_PRESETS.keys())
        preset = Prompt.ask("Select starting profile", choices=valid_presets, default="standard")
        
        await permission_manager.apply_preset(preset)
        console.print(f"[green]✓ Applied '{preset}' profile.[/green]\n")

    
    # Initialize API Gateway (secrets isolated from tools)
    from atlas.gateway import APIGateway
    gateway = APIGateway(
        permission_manager=permission_manager,
        audit_logger=audit_logger,
    )
    console.print("[dim]  ✓ API Gateway active (secrets isolated)[/dim]")
    
    console.print("[dim]→ Loading tools...[/dim]")
    from atlas.config.loader import load_config
    config = load_config()
    from atlas.tools.tools_loader import load_all_tools
    tools = load_all_tools(data_dir, console, gateway=gateway, deny_list=config.security.tools_deny)
    
    # Re-create LLM with tools bound
    if tools:
        llm = model_mgr.create_llm(model, tools=tools)
        console.print(f"[dim]  ✓ {len(tools)} tool(s) bound to LLM[/dim]")

    
    console.print("[dim]→ Initializing conversation checkpointer...[/dim]")
    checkpointer = MemorySaver()
    
    console.print("[dim]→ Creating agent graph...[/dim]")
    # Create agent graph with tools and checkpointer for conversation state
    graph = create_agent_graph(tools=tools if tools else None, checkpointer=checkpointer)
    
    console.print("[green]✓ Atlas ready[/green]\n")
    
    if message:
        # Single message mode
        console.print("[bold cyan]Processing...[/bold cyan]")
        response = await run_agent(
            graph=graph,
            message=message,
            llm=llm,
            memory_store=memory_store,
            permission_manager=permission_manager,
            verbose=verbose,
        )
        console.print(Panel(response, title="[bold blue]Atlas[/bold blue]"))
        
        # Store conversation
        await memory_store.store_conversation([
            {"role": "user", "content": message},
            {"role": "assistant", "content": response},
        ])
    else:
        # Interactive mode
        console.print(Panel(
            "[bold blue]Atlas Agent[/bold blue]\n"
            "Type your message and press Enter. Type 'exit' to quit.\n"
            "Use '/model provider:name' to switch models.",
            border_style="blue",
        ))
        
        while True:
            try:
                user_input = console.input("[bold green]You:[/bold green] ")
            except (KeyboardInterrupt, EOFError):
                break
            
            if user_input.lower() in ("exit", "quit", "bye"):
                console.print("[dim]Goodbye![/dim]")
                break
            
            if not user_input.strip():
                continue
            
            # Handle /model command for mid-conversation switching
            if user_input.strip().startswith("/model"):
                parts = user_input.strip().split(None, 1)
                if len(parts) == 1 or parts[1].strip() == "":
                    # Show current model status
                    status = model_mgr.get_status()
                    console.print(f"[bold]Current model:[/bold] {status['current']}")
                    providers = model_mgr.list_available_providers()
                    for p in providers:
                        icon = "✓" if p["installed"] else "✗"
                        key_info = " (needs API key)" if p["needs_key"] else " (local)"
                        console.print(f"  [{('green' if p['installed'] else 'dim')}]{icon} {p['name']}[/] — {p['label']}{key_info}")
                elif parts[1].strip() == "list":
                    providers = model_mgr.list_available_providers()
                    for p in providers:
                        icon = "✓" if p["installed"] else "✗"
                        install = "" if p["installed"] else f" — pip install {p['package']}"
                        console.print(f"  [{('green' if p['installed'] else 'red')}]{icon} {p['name']}[/] ({p['label']}){install}")
                else:
                    new_model = parts[1].strip()
                    try:
                        llm = model_mgr.create_llm(new_model, tools=tools)
                        console.print(f"[green]✓ Switched to {model_mgr.current_provider}:{model_mgr.current_model}[/green]\n")
                    except (ValueError, ImportError) as e:
                        console.print(f"[red]✗ {e}[/red]")
                continue
            
            console.print("[bold cyan]Processing...[/bold cyan]")
            response = await run_agent(
                graph=graph,
                message=user_input,
                llm=llm,
                memory_store=memory_store,
                permission_manager=permission_manager,
                verbose=verbose,
            )
            
            console.print(f"[bold blue]Atlas:[/bold blue] {response}\n")
            
            # Store conversation
            await memory_store.store_conversation([
                {"role": "user", "content": user_input},
                {"role": "assistant", "content": response},
            ])




# Permission management commands
permissions_app = typer.Typer(help="Manage security permissions and access control lists.")
app.add_typer(permissions_app, name="permissions")


@app.command("slack")
def start_slack(
    start: bool = typer.Argument(True, help="Start the Slack bot"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
):
    """Start the Atlas Slack bot.
    
    Requires SLACK_BOT_TOKEN and SLACK_APP_TOKEN environment variables.
    """
    from atlas.gateway.server import start_server_in_background
    start_server_in_background()
    import asyncio
    from atlas.integrations.slack import start_bot
    
    asyncio.run(start_bot(verbose=verbose))



def get_permission_manager():
    """Get initialized permission manager."""
    from atlas.security import PermissionManager
    return PermissionManager(ui_handler=permission_ui_handler)


@permissions_app.command("status")
def permissions_status():
    """Show a dashboard of all permission statuses.
    
    Displays each permission with its grant status, level,
    expiration, and the active preset profile.
    """
    from rich.table import Table
    
    pm = get_permission_manager()
    statuses = pm.get_permission_status()
    preset = pm.get_active_preset()
    
    console.print(f"\n[bold]🛡️  Atlas Permission Status[/bold]")
    if preset:
        console.print(f"  Profile: [cyan]{preset}[/cyan]")
    console.print()
    
    # Group by service
    services = {}
    for s in statuses:
        # Infer service from permission name
        perm = s["permission"]
        if perm.startswith("email"):
            svc = "Gmail"
        elif perm.startswith("calendar"):
            svc = "Calendar"
        elif perm.startswith("drive"):
            svc = "Drive"
        elif perm.startswith("tasks"):
            svc = "Tasks"
        elif perm.startswith("notes"):
            svc = "Notes"
        elif perm.startswith("internet") or perm.startswith("web"):
            svc = "Web"
        else:
            svc = "System"
        services.setdefault(svc, []).append(s)
    
    table = Table(show_header=True, header_style="bold magenta", expand=True)
    table.add_column("Service", style="bold")
    table.add_column("Permission")
    table.add_column("Level")
    table.add_column("Status")
    table.add_column("Expires")
    table.add_column("Granted By", style="dim")
    
    status_icons = {
        "granted": "[green]✅ Granted[/green]",
        "denied": "[red]❌ Denied[/red]",
        "default": "[blue]🟢 Default[/blue]",
        "unset": "[dim]🔒 Not Set[/dim]",
    }
    
    level_colors = {
        "LOW": "[green]LOW[/green]",
        "MEDIUM": "[yellow]MEDIUM[/yellow]",
        "HIGH": "[red]HIGH[/red]",
        "CRITICAL": "[bold red]CRITICAL[/bold red]",
    }
    
    for svc_name in sorted(services.keys()):
        for i, s in enumerate(services[svc_name]):
            svc_label = svc_name if i == 0 else ""
            expires = ""
            if s["expires"]:
                remaining = s["expires"] - datetime.now()
                if remaining.total_seconds() > 0:
                    hours = int(remaining.total_seconds() / 3600)
                    if hours > 24:
                        expires = f"{hours // 24}d {hours % 24}h"
                    else:
                        expires = f"{hours}h {int((remaining.total_seconds() % 3600) / 60)}m"
                else:
                    expires = "[red]Expired[/red]"
            elif s["status"] == "granted":
                expires = "Forever"
            
            table.add_row(
                svc_label,
                s["description"],
                level_colors.get(s["level"], s["level"]),
                status_icons.get(s["status"], s["status"]),
                expires,
                s["granted_by"] or "",
            )
    
    console.print(table)
    
    # Summary line
    counts = {}
    for s in statuses:
        counts[s["status"]] = counts.get(s["status"], 0) + 1
    parts = []
    if counts.get("granted", 0):
        parts.append(f"[green]Granted: {counts['granted']}[/green]")
    if counts.get("denied", 0):
        parts.append(f"[red]Denied: {counts['denied']}[/red]")
    if counts.get("default", 0):
        parts.append(f"[blue]Default: {counts['default']}[/blue]")
    if counts.get("unset", 0):
        parts.append(f"[dim]Unset: {counts['unset']}[/dim]")
    console.print(f"\n  {' | '.join(parts)}")
    console.print()


# Keep 'list' as alias for 'status'
@permissions_app.command("list", hidden=True)
def list_permissions():
    """Alias for 'status'."""
    permissions_status()


@permissions_app.command("grant")
def grant_permission(
    permission: str = typer.Argument(..., help="Permission name (e.g. 'calendar_read', 'email_*')"),
    scope: str = typer.Option("*", "--scope", "-s", help="Scope (e.g. 'gmail.com', '*.google.com')"),
    duration: str = typer.Option("day", "--duration", "-d", help="Duration: 'once', 'session', 'hour', 'day', 'forever'"),
):
    """Grant a permission programmatically.
    
    Useful for scripting, cron jobs, or headless environments.
    
    Examples:
        atlas permissions grant calendar_read
        atlas permissions grant email_* --duration forever
        atlas permissions grant drive_write --scope drive.google.com --duration hour
    """
    pm = get_permission_manager()
    asyncio.run(pm.grant(permission, scope, duration=duration, granted_by="cli"))
    console.print(f"[green]✓ Granted '{permission}:{scope}' for {duration}[/green]")


@permissions_app.command("setup")
def setup_permissions(
    preset: str = typer.Argument(None, help="Preset name: 'minimal', 'reader', 'standard', 'full'"),
):
    """Set up permissions with a preset profile.
    
    Choose a preset to bulk-grant permissions instead of approving each one individually.
    
    Presets:
      minimal  — Ask for everything (most secure)
      reader   — Read-only access to email, calendar, tasks, drive, notes, web
      standard — Reader + write notes and tasks
      full     — Standard + send email, modify calendar, upload to drive
    """
    from atlas.security import PermissionManager
    
    valid = list(PermissionManager.PERMISSION_PRESETS.keys())
    
    if not preset:
        console.print("\n[bold]Choose a permission profile:[/bold]\n")
        console.print("  [cyan]minimal[/cyan]  — Ask for everything (most secure)")
        console.print("  [cyan]reader[/cyan]   — Read-only access to all services")
        console.print("  [cyan]standard[/cyan] — Reader + write notes and tasks")
        console.print("  [cyan]full[/cyan]     — Standard + send email, modify calendar, upload to drive")
        console.print()
        preset = Prompt.ask("Select preset", choices=valid, default="standard")
    
    if preset not in valid:
        console.print(f"[red]Invalid preset: {preset}. Valid: {valid}[/red]")
        return
    
    pm = get_permission_manager()
    asyncio.run(pm.apply_preset(preset))
    
    perms = PermissionManager.PERMISSION_PRESETS[preset]
    console.print(f"\n[green]✓ Applied '{preset}' preset ({len(perms)} permissions granted)[/green]")
    if perms:
        for p in perms:
            console.print(f"  • {p}")
    console.print()


@permissions_app.command("revoke")
def revoke_permission(
    permission_id: str = typer.Argument(..., help="Permission to revoke (format: 'permission_name:scope' or just 'permission_name')"),
):
    """Revoke a previously granted permission."""
    pm = get_permission_manager()
    
    if ":" in permission_id:
        permission, scope = permission_id.split(":", 1)
    else:
        permission, scope = permission_id, "*"
    
    key = f"{permission}:{scope}"
    if key not in pm.grants:
        console.print(f"[yellow]Permission '{key}' not found[/yellow]")
        return
        
    asyncio.run(pm.revoke(permission, scope))
    console.print(f"[green]✓ Permission '{key}' revoked[/green]")


@permissions_app.command("reset")
def reset_permissions():
    """Revoke ALL permissions and clear the active preset.
    
    This resets the permission system to a clean state.
    You will be prompted for every permission again.
    """
    if not Confirm.ask("[yellow]Reset all permissions? This cannot be undone.[/yellow]", default=False):
        console.print("[dim]Cancelled[/dim]")
        return
    
    pm = get_permission_manager()
    asyncio.run(pm.reset_all())
    console.print("[green]✓ All permissions reset[/green]")


@permissions_app.command("show")
def show_permission(
    permission_id: str = typer.Argument(..., help="The unique identifier of the permission to inspect (format: 'permission_name:scope')"),
):
    """Show detailed information for a specific permission grant."""
    pm = get_permission_manager()
    
    key = permission_id
    if key not in pm.grants:
        console.print(f"[yellow]Permission '{permission_id}' not found[/yellow]")
        return
        
    grant = pm.grants[key]
    
    console.print(Panel(
        f"[bold]Permission:[/bold] {grant.permission}\n"
        f"[bold]Scope:[/bold] {grant.scope}\n"
        f"[bold]Granted:[/bold] {grant.granted}\n"
        f"[bold]Granted At:[/bold] {grant.granted_at}\n"
        f"[bold]Expires At:[/bold] {grant.expires_at}\n"
        f"[bold]Granted By:[/bold] {grant.granted_by}",
        title=f"[bold blue]Permission Details: {permission_id}[/bold blue]",
        border_style="blue",
    ))



@app.command()
def audit(
    lines: int = typer.Option(20, "--lines", "-n", help="Number of recent log entries to display"),
):
    """View the security audit log.
    
    Displays a chronological log of security-relevant events, including
    permission requests, grants, denials, and tool executions.
    Also verifies the cryptographic integrity of the log file.
    """
    from atlas.security import AuditLogger
    
    data_dir = get_data_dir()
    logger = AuditLogger(data_dir / "audit")
    
    # Verify integrity
    if logger.verify_integrity():
        console.print("[green]✓ Audit log integrity verified[/green]\n")
    else:
        console.print("[red]⚠ Audit log integrity check failed![/red]\n")
    
    entries = logger.get_recent(lines)
    
    if not entries:
        console.print("[dim]No audit entries[/dim]")
        return
    
    for entry in entries:
        timestamp = entry.get("timestamp", "")[:19]
        event_type = entry.get("type", "unknown")
        data = entry.get("data", {})
        
        # Format based on type
        if event_type == "permission":
            status = "✅ granted" if data.get("granted") else "❌ denied"
            console.print(f"[dim]{timestamp}[/dim] [yellow]permission[/yellow] {data.get('permission')} {status}")
        elif event_type == "action":
            console.print(f"[dim]{timestamp}[/dim] [cyan]action[/cyan] {data.get('action')}")
        elif event_type == "tool_call":
            console.print(f"[dim]{timestamp}[/dim] [magenta]tool[/magenta] {data.get('tool')}")
        else:
            console.print(f"[dim]{timestamp}[/dim] {event_type}")


@app.command()
def status():
    """Check the health and status of Atlas components.
    
    Verifies:
    * Ollama connection and available models
    * Data directory presence
    * Memory store initialization
    """
    import httpx
    
    console.print("\n[bold]Atlas System Status[/bold]\n")
    
    # Check Ollama
    try:
        response = httpx.get("http://localhost:11434/api/tags", timeout=2.0)
        if response.status_code == 200:
            data = response.json()
            models = [m["name"] for m in data.get("models", [])]
            console.print(f"[green]✓[/green] Ollama: Running ({len(models)} models)")
            for model in models[:5]:
                console.print(f"    - {model}")
        else:
            console.print("[yellow]⚠[/yellow] Ollama: Not responding")
    except Exception:
        console.print("[red]✗[/red] Ollama: Not running")
        console.print("    Install: https://ollama.ai")
    
    # Check data directory
    data_dir = get_data_dir()
    console.print(f"[green]✓[/green] Data directory: {data_dir}")
    
    # Check memory
    memory_file = data_dir / "memory" / "conversations.md"
    if memory_file.exists():
        console.print(f"[green]✓[/green] Memory store: Initialized (Markdown)")
    else:
        console.print(f"[dim]○[/dim] Memory store: Not initialized")
    
    console.print()


@app.command("note")
def quick_note(
    content: str = typer.Argument(..., help="Note content with optional #tags"),
):
    """Quick shortcut to capture a note.
    
    Examples:
        atlas note "Great idea about caching #project-x"
        atlas note "Meeting summary: Discussed Q4 roadmap #meeting"
    """
    from atlas.notes_cli import add_note
    add_note(content=content, title=None)


@app.command("skill")
def create_skill(
    description: str = typer.Argument(
        ...,
        help='Natural-language description of the tool to create, e.g. "Add a currency converter using exchangerate.host"',
    ),
):
    """Generate and register a new Atlas tool using the local LLM.

    Inspired by NanoClaw's 'skills over features' philosophy. Describe what you
    want in plain English and Atlas will write, preview, and optionally register
    the Python tool module for you.

    Examples:

        atlas skill "Add a currency conversion tool"

        atlas skill "Create a Hacker News top-stories fetcher"

        atlas skill "Make a tool that checks my public IP address"
    """
    from atlas.config.paths import get_data_dir
    from atlas.llm.skill_agent import run_skill_agent

    tools_dir = Path(__file__).parent / "tools"
    loader_path = tools_dir / "tools_loader.py"

    asyncio.run(run_skill_agent(description, tools_dir=tools_dir, loader_path=loader_path))


if __name__ == "__main__":
    app()

