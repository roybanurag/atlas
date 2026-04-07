"""Slack Bot implementation for Atlas."""

import asyncio
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from langgraph.checkpoint.memory import MemorySaver

from atlas.config.paths import get_data_dir
from atlas.graph import create_agent_graph
from atlas.security import PermissionManager
from atlas.tools.tools_loader import load_all_tools
from .handler import SlackUIHandler


class SlackBot:
    """Atlas Slack Bot."""
    
    def __init__(self, verbose: bool = False):
        """Initialize the Slack bot."""
        self.verbose = verbose
        
        # Get tokens
        from atlas.security import get_api_key
        bot_token = get_api_key("slack_bot_token") or os.environ.get("SLACK_BOT_TOKEN", "")
        app_token = get_api_key("slack_app_token") or os.environ.get("SLACK_APP_TOKEN", "")
        
        if not bot_token or not app_token:
            raise ValueError("Slack tokens not configured")
        
        self.app = AsyncApp(token=bot_token)
        self.app_token = app_token
        self.ui_handler = SlackUIHandler(self.app)
        self.permission_manager = PermissionManager(ui_handler=self.ui_handler.request_permission)
        
        self.data_dir = get_data_dir()
        
        # Initialize tools using shared loader
        print("  Loading tools...")
        from atlas.config.loader import load_config
        config = load_config()
        self.slack_allowed_users = config.security.slack_allowed_users
        self.tools = load_all_tools(self.data_dir, deny_list=config.security.tools_deny)
        print(f"  ✓ {len(self.tools)} tool(s) loaded")
            
        # Initialize LLM via ModelManager
        from atlas.config.model_manager import ModelManager
        self.model_mgr = ModelManager()
        self.llm = self.model_mgr.create_llm("qwen3:14b", tools=self.tools)
            
        # Initialize memory store
        from atlas.memory import MemoryStore
        self.memory_store = MemoryStore(self.data_dir / "memory")
        
        # Initialize checkpointer for conversation state persistence
        self.checkpointer = MemorySaver()
        
        # Initialize graph with checkpointer
        self.graph = create_agent_graph(tools=self.tools, checkpointer=self.checkpointer)
        
        # Register handlers
        self._register_handlers()
    
    def _register_handlers(self):
        """Register Slack event handlers."""
        
        @self.app.action("approve_permission")
        async def handle_approval(ack, body, action):
            await ack()
            user_id = body["user"]["id"]
            action_id = action["action_id"]
            value = action["value"]
            
            if self.ui_handler.handle_interaction(action_id, value, user_id):
                # Update message to show approval
                await self.app.client.chat_update(
                    channel=body["channel"]["id"],
                    ts=body["message"]["ts"],
                    blocks=[],
                    text=f"✅ Permission granted by <@{user_id}>"
                )

        @self.app.action("deny_permission")
        async def handle_denial(ack, body, action):
            await ack()
            user_id = body["user"]["id"]
            action_id = action["action_id"]
            value = action["value"]
            
            if self.ui_handler.handle_interaction(action_id, value, user_id):
                # Update message to show denial
                await self.app.client.chat_update(
                    channel=body["channel"]["id"],
                    ts=body["message"]["ts"],
                    blocks=[],
                    text=f"❌ Permission denied by <@{user_id}>"
                )

        @self.app.event("app_mention")
        async def handle_app_mention(event, say):
            """Handle @mentions of the bot."""
            print(f"📨 Received app mention: {event.get('text', '')}")
            await self._process_message(event, say)

        @self.app.message(re.compile(".*"))
        async def handle_message(message, say):
            """Handle direct messages to the bot."""
            subtype = message.get("subtype")
            
            # Handle message edits
            if subtype == "message_changed":
                if self.verbose:
                    print(f"📝 Received message edit (ignoring)")
                return
                
            # Ignore bot messages
            if subtype == "bot_message":
                return
            
            print(f"📨 Received DM: {message.get('text', '')}")
            await self._process_message(message, say)
        
        # ── Slash Commands ──────────────────────────────────────────────
        
        @self.app.command("/atlas-briefing")
        async def handle_briefing_command(ack, respond, command):
            """Handle /atlas-briefing slash command."""
            await ack()
            
            from atlas.tools.briefing import BriefingGenerator
            
            # Parse optional date argument
            text = command.get("text", "").strip()
            target_date = datetime.now()
            if text:
                if text.lower() == "tomorrow":
                    target_date = datetime.now() + timedelta(days=1)
                elif text.lower() == "yesterday":
                    target_date = datetime.now() - timedelta(days=1)
                elif text.lower() != "today":
                    try:
                        target_date = datetime.fromisoformat(text)
                    except ValueError:
                        await respond(f"❌ Invalid date: `{text}`. Use `today`, `tomorrow`, or `YYYY-MM-DD`.")
                        return
            
            generator = BriefingGenerator(self.data_dir)
            briefing = generator.generate(target_date=target_date)
            await respond(briefing.to_markdown())
        
        @self.app.command("/atlas-status")
        async def handle_status_command(ack, respond, command):
            """Handle /atlas-status slash command."""
            await ack()
            
            import httpx
            
            lines = ["*Atlas System Status*\n"]
            
            # Check Ollama
            try:
                response = httpx.get("http://localhost:11434/api/tags", timeout=2.0)
                if response.status_code == 200:
                    data = response.json()
                    models = [m["name"] for m in data.get("models", [])]
                    lines.append(f"✅ *Ollama:* Running ({len(models)} models)")
                    for model in models[:5]:
                        lines.append(f"    • {model}")
                else:
                    lines.append("⚠️ *Ollama:* Not responding")
            except Exception:
                lines.append("❌ *Ollama:* Not running")
            
            # Data directory
            lines.append(f"✅ *Data directory:* `{self.data_dir}`")
            
            # Memory
            memory_file = self.data_dir / "memory" / "conversations.md"
            if memory_file.exists():
                lines.append("✅ *Memory store:* Initialized")
            else:
                lines.append("○ *Memory store:* Not initialized")
            
            # Tools
            lines.append(f"✅ *Tools loaded:* {len(self.tools)}")
            
            await respond("\n".join(lines))
        
        @self.app.command("/atlas-note")
        async def handle_note_command(ack, respond, command):
            """Handle /atlas-note slash command."""
            await ack()
            
            text = command.get("text", "").strip()
            if not text:
                await respond("❌ Usage: `/atlas-note <content with optional #tags>`")
                return
            
            from atlas.tools.notes import NotesManager
            
            manager = NotesManager(self.data_dir)
            note = manager.create_note(content=text)
            
            tags_str = " ".join(f"`#{t}`" for t in note.tags) if note.tags else "none"
            await respond(
                f"📝 *Note saved!*\n"
                f"*Title:* {note.title}\n"
                f"*ID:* `{note.id[:8]}`\n"
                f"*Tags:* {tags_str}"
            )
        
        @self.app.command("/atlas-task")
        async def handle_task_command(ack, respond, command):
            """Handle /atlas-task slash command."""
            await ack()
            
            text = command.get("text", "").strip()
            if not text:
                await respond(
                    "❌ Usage: `/atlas-task <title> [--due today/tomorrow/YYYY-MM-DD] [--notes ...]`\n"
                    "Examples:\n"
                    "• `/atlas-task Buy groceries`\n"
                    "• `/atlas-task Review PR --due tomorrow`\n"
                    "• `/atlas-task Submit report --due 2026-02-20 --notes Q1 financials`"
                )
                return
            
            # Parse arguments from text
            title = text
            due = ""
            notes = ""
            
            # Extract --due
            if "--due" in text:
                parts = text.split("--due")
                title = parts[0].strip()
                rest = parts[1].strip()
                # due is the first word after --due
                due_parts = rest.split()
                if due_parts:
                    due = due_parts[0]
                    rest = " ".join(due_parts[1:])
                    # Check if rest has --notes
                    if rest.startswith("--notes"):
                        notes = rest.replace("--notes", "").strip()
            
            # Extract --notes (if not already extracted)
            if not notes and "--notes" in title:
                parts = title.split("--notes")
                title = parts[0].strip()
                notes = parts[1].strip()
            
            try:
                from atlas.tools.google_proxy import google_call
                
                # Format ISO 8601 due date if provided
                due_parsed = None
                if due:
                    from datetime import datetime, timedelta
                    if due.lower() == "today":
                        due_parsed = datetime.now()
                    elif due.lower() == "tomorrow":
                        due_parsed = datetime.now() + timedelta(days=1)
                    else:
                        due_parsed = datetime.strptime(due, "%Y-%m-%d")
                    due_parsed = due_parsed.isoformat() + "Z"

                task_body = {
                    'title': title,
                    'status': 'needsAction',
                }
                
                if notes:
                    task_body['notes'] = notes
                
                if due_parsed:
                    task_body['due'] = due_parsed
                
                # Send to Google via Gateway Proxy
                result = google_call(
                    service="tasks",
                    method="insert",
                    params={"tasklist": "@default"},
                    body=task_body
                )
                
                response = f"✅ *Task created!*\n*Title:* {title}"
                if notes:
                    response += f"\n*Notes:* {notes}"
                if due:
                    response += f"\n*Due:* {due}"
                response += f"\n*ID:* `{result.get('id', 'unknown')}`"
                
                await respond(response)
            except Exception as e:
                await respond(f"❌ Error creating task: {str(e)}")
        
        @self.app.command("/atlas-model")
        async def handle_model_command(ack, respond, command):
            """Handle /atlas-model slash command for switching LLM models."""
            await ack()
            
            text = command.get("text", "").strip()
            
            if not text:
                # Show current model
                status = self.model_mgr.get_status()
                providers = self.model_mgr.list_available_providers()
                lines = [f"🤖 *Current model:* `{status['current']}`", "", "*Available providers:*"]
                for p in providers:
                    icon = "✅" if p["installed"] else "❌"
                    lines.append(f"{icon} `{p['name']}` — {p['label']}")
                await respond("\n".join(lines))
            else:
                try:
                    self.llm = self.model_mgr.create_llm(text, tools=self.tools)
                    await respond(f"✅ Switched to `{self.model_mgr.current_provider}:{self.model_mgr.current_model}`")
                except (ValueError, ImportError) as e:
                    await respond(f"❌ {e}")

        @self.app.command("/atlas-audit")
        async def handle_audit_command(ack, respond, command):
            """Handle /atlas-audit command."""
            await ack()
            text = command.get("text", "").strip()
            lines_count = 20
            if text.isdigit():
                lines_count = int(text)
            
            from atlas.security import AuditLogger
            logger = AuditLogger(self.data_dir / "audit")
            
            blocks = []
            
            if logger.verify_integrity():
                blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "✅ *Audit log integrity verified*"}})
            else:
                blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "❌ *Audit log integrity check failed!*"}})
                
            entries = logger.get_recent(lines_count)
            if not entries:
                blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "_No audit entries found._"}})
            else:
                text_lines = []
                for entry in entries:
                    ts = entry.get("timestamp", "")[:19]
                    etype = entry.get("type", "unknown")
                    data = entry.get("data", {})
                    
                    if etype == "permission":
                        status = "✅" if data.get("granted") else "❌"
                        text_lines.append(f"`{ts}` *permission* `{data.get('permission')}` {status}")
                    elif etype == "action":
                        text_lines.append(f"`{ts}` *action* _{data.get('action')}_")
                    elif etype == "tool_call":
                        text_lines.append(f"`{ts}` *tool* `{data.get('tool')}`")
                    else:
                        text_lines.append(f"`{ts}` *{etype}*")
                blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(text_lines)}})

            await respond(blocks=blocks)

        @self.app.command("/atlas-secrets")
        async def handle_secrets_command(ack, respond, command):
            """Handle /atlas-secrets command."""
            await ack()
            from atlas.security.secrets import API_KEY_CONFIGS, get_api_key
            
            blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "🔑 *Configured Secrets*"}}]
            lines = []
            for svc_name, config in API_KEY_CONFIGS.items():
                val = get_api_key(config["keyring_name"], config["env_var"])
                status = "✅ Set" if val else "❌ Not configured"
                lines.append(f"• *{svc_name}*: {status}")
            
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}})
            blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": "Note: For security reasons, updating or deleting secrets must be done directly on the host using the `atlas secrets set` CLI."}]})
            await respond(blocks=blocks)

        @self.app.command("/atlas-permissions")
        async def handle_permissions_command(ack, respond, command):
            """Handle /atlas-permissions command."""
            await ack()
            args = command.get("text", "").strip().split()
            cmd = args[0] if args else "list"
            
            pm = self.permission_manager
            blocks = []
            
            if cmd == "list":
                permissions = pm.list_permissions()
                if not permissions:
                    await respond("ℹ️ No active permissions.")
                    return
                blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "🛡️ *Active Permissions*"}})
                for p in permissions:
                    pid = p["id"][:8]
                    status = f"✅ `{p['permission']}` (Scope: `{p['scope']}`)"
                    expiry = p.get("expires_at")
                    if p["duration"] == "forever":
                        expiry_text = "Never expires"
                    else:
                        import datetime
                        expiry_dt = datetime.datetime.fromisoformat(expiry)
                        expiry_text = expiry_dt.strftime("%b %d %H:%M")
                    
                    blocks.append({
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f"{status}\n*ID:* `{pid}` | *Expires:* {expiry_text}"},
                        "accessory": {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Revoke"},
                            "style": "danger",
                            "action_id": "revoke_permission_ui",
                            "value": p["id"]
                        }
                    })
                await respond(blocks=blocks)
            elif cmd == "revoke" and len(args) > 1:
                pid = args[1]
                matches = [p for p in pm.list_permissions() if p["id"].startswith(pid)]
                if not matches:
                    await respond(f"❌ Permission not found: `{pid}`")
                else:
                    pm.revoke_permission(matches[0]["id"])
                    await respond(f"✅ Revoked permission: `{matches[0]['permission']}`")
            else:
                await respond("❌ Usage: `/atlas-permissions list` or `/atlas-permissions revoke <id>`")

        @self.app.action("revoke_permission_ui")
        async def handle_revoke_ui(ack, body, action, respond):
            await ack()
            pid = action["value"]
            self.permission_manager.revoke_permission(pid)
            await respond(f"✅ Permission revoked.", replace_original=True)
            
        @self.app.command("/atlas-notes")
        async def handle_notes_command(ack, respond, command):
            """Handle /atlas-notes command."""
            await ack()
            args = command.get("text", "").strip().split()
            cmd = args[0] if args else "list"
            query_parts = args[1:] if len(args) > 1 else []
            
            from atlas.tools.notes import NotesManager
            manager = NotesManager(self.data_dir)
            
            if cmd == "list":
                notes = manager.list_notes(limit=10)
                if not notes:
                    await respond("ℹ️ No notes found.")
                    return
                lines = ["📝 *Recent Notes*"]
                for n in notes:
                    lines.append(f"• `{n.id[:6]}` *{n.title}*")
                await respond("\n".join(lines))
                
            elif cmd == "search":
                q = " ".join(query_parts)
                notes = manager.search_notes(query=q)
                if not notes:
                    await respond(f"ℹ️ No notes found for `{q}`.")
                    return
                lines = [f"🔍 *Search '*_'{q}'_*'"]
                for n in notes:
                    lines.append(f"• `{n.id[:6]}` *{n.title}*")
                await respond("\n".join(lines))
                
            elif cmd == "show" and query_parts:
                nid = query_parts[0]
                n = manager.get_note_by_prefix(nid)
                if not n:
                    notes = manager.search_notes(query=nid, limit=1)
                    if notes:
                        n = notes[0]
                if n:
                    tags = " ".join(f"#{t}" for t in n.tags)
                    await respond(f"📑 *{n.title}* (ID: `{n.id[:6]}`)\n*Tags:* {tags}\n\n{n.content}")
                else:
                    await respond(f"❌ Note not found: `{nid}`")
            else:
                await respond("❌ Usage: `/atlas-notes list`, `/atlas-notes search <query>`, or `/atlas-notes show <id>`")

        @self.app.command("/atlas-skill")
        async def handle_skill_command(ack, respond, command):
            """Handle /atlas-skill command."""
            await ack()
            desc = command.get("text", "").strip()
            if not desc:
                await respond("❌ Usage: `/atlas-skill <description of tool to build>`")
                return
                
            msg = await respond(f"⏳ *Atlas Skill Builder is generating...*\n> _'{desc}'_")
            
            # Run the generation entirely in the background
            async def generate_skill():
                from atlas.llm.skill_agent import _generate_with_llm, SKILL_SYSTEM_PROMPT, _extract_code, _derive_names
                from atlas.config.paths import get_data_dir
                import os
                
                module_name, factory_name, display_name = _derive_names(desc)
                tools_dir = Path(__file__).parent.parent.parent / "tools"
                loader_path = tools_dir / "tools_loader.py"
                
                try:
                    # Gen code
                    prompt=f"Create an Atlas tool for: {desc}\n\nFactory function name must be: {factory_name}"
                    raw_code = await _generate_with_llm(prompt=prompt, system=SKILL_SYSTEM_PROMPT)
                    code = _extract_code(raw_code)
                    
                    target_file = tools_dir / f"{module_name}.py"
                    target_file.write_text(code)
                    
                    # Patch loader
                    loader_src = loader_path.read_text()
                    import_line = f"    from atlas.tools.{module_name} import {factory_name}\n"
                    if import_line.strip() not in loader_src:
                        loader_src = loader_src.replace(
                            "    from atlas.tools.sandbox import create_sandbox_tools\n",
                            f"    from atlas.tools.sandbox import create_sandbox_tools\n{import_line}",
                        )
                    new_entry = f'        ("{display_name}", lambda: {factory_name}()),\n'
                    if new_entry.strip() not in loader_src:
                        loader_src = loader_src.replace(
                            '        ("Briefing",      lambda: create_briefing_tool(data_dir)),',
                            f'        ("Briefing",      lambda: create_briefing_tool(data_dir)),\n{new_entry.rstrip(",")},',
                        )
                    loader_path.write_text(loader_src)
                    
                    await self.app.client.chat_postMessage(
                        channel=command["channel_id"],
                        text=f"✅ *Skill `{display_name}` successfully created and registered!*\nRestart the Atlas agent for the new tool to take effect.\n```python\n{code[:800]}... (truncated)\n```"
                    )
                except Exception as e:
                    await self.app.client.chat_postMessage(
                        channel=command["channel_id"],
                        text=f"❌ *Failed to generate skill:* {str(e)}"
                    )
                    
            asyncio.create_task(generate_skill())

    async def _process_message(self, message, say):
        """Process a message from Slack."""
        from rich.console import Console
        from rich.panel import Panel
        from rich.syntax import Syntax
        
        console = Console()
        
        text = message.get("text", "")
        user_id = message["user"]
        channel_id = message["channel"]
        thread_ts = message.get("thread_ts", message["ts"])
        
        # Enforce allowlist
        if self.slack_allowed_users and user_id not in self.slack_allowed_users:
            await say(text=f"🚫 Unauthorized: User <@{user_id}> is not permitted to use Atlas.", thread_ts=thread_ts)
            return
        
        # Use thread_ts as thread_id for conversation context
        config = {
            "configurable": {
                "thread_id": thread_ts,
                "llm": self.llm,
                "permission_manager": self.permission_manager,
                "memory_store": self.memory_store,
                "verbose": self.verbose,
                "security_context": {
                    "user_id": user_id,
                    "channel_id": channel_id,
                    "thread_ts": thread_ts
                }
            }
        }
        
        # Send initial reaction
        await self.app.client.reactions_add(
            channel=channel_id,
            name="eyes",
            timestamp=message["ts"]
        )
        
        try:
            # Prepare input
            from langchain_core.messages import HumanMessage
            input_state = {
                "messages": [HumanMessage(content=text)],
                "needs_memory_refresh": True
            }
            
            if self.verbose:
                console.print(f"\n[bold cyan]═══ Processing Slack Message from {user_id} ═══[/bold cyan]\n")
            
            # Run agent
            final_state = None
            step_count = 0
            async for state in self.graph.astream(input_state, config, stream_mode="values"):
                step_count += 1
                final_state = state
                
                if self.verbose:
                    # Show detailed state information
                    messages = state.get("messages", [])
                    if messages:
                        last_msg = messages[-1]
                        msg_type = last_msg.__class__.__name__ if hasattr(last_msg, "__class__") else "Unknown"
                        
                        console.print(f"\n[bold yellow]Step {step_count}:[/bold yellow] {msg_type}")
                        
                        # Show AI messages with tool calls
                        if msg_type == "AIMessage":
                            if hasattr(last_msg, "content") and last_msg.content:
                                console.print(Panel(
                                    last_msg.content,
                                    title="[bold blue]🤖 LLM Response[/bold blue]",
                                    border_style="blue",
                                ))
                            
                            # Show tool calls if present
                            if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
                                for tool_call in last_msg.tool_calls:
                                    tool_name = tool_call.get("name", "unknown")
                                    tool_args = tool_call.get("args", {})
                                    console.print(f"\n[bold magenta]🔧 Tool Call:[/bold magenta] {tool_name}")
                                    if tool_args:
                                        import json
                                        args_json = json.dumps(tool_args, indent=2)
                                        syntax = Syntax(args_json, "json", theme="monokai", line_numbers=False)
                                        console.print(syntax)
                        
                        # Show tool messages (responses)
                        elif msg_type == "ToolMessage":
                            if hasattr(last_msg, "content"):
                                tool_name = getattr(last_msg, "name", "unknown")
                                console.print(Panel(
                                    last_msg.content,
                                    title=f"[bold green]✓ Tool Response: {tool_name}[/bold green]",
                                    border_style="green",
                                ))
            
            # Extract response
            response = "I encountered an issue."
            if final_state:
                messages = final_state.get("messages", [])
                if messages:
                     # Isolate messages from the current turn (from the last HumanMessage)
                    turn_messages = []
                    for msg in reversed(messages):
                        turn_messages.insert(0, msg)
                        is_human = False
                        if hasattr(msg, "__class__"):
                            is_human = msg.__class__.__name__ == "HumanMessage"
                        elif isinstance(msg, dict):
                            is_human = msg.get("type") == "human"
                        if is_human:
                            break
                    
                    # Extract final AI message
                    final_ai_content = ""
                    
                    for msg in turn_messages:
                        is_ai = False
                        
                        if hasattr(msg, "__class__"):
                            name = msg.__class__.__name__
                            is_ai = name == "AIMessage"
                        elif isinstance(msg, dict):
                            mtype = msg.get("type")
                            is_ai = mtype == "ai"
                            
                        if is_ai:
                            content = msg.content if hasattr(msg, "content") else msg.get("content", "")
                            if content:
                                final_ai_content = content
                    
                    # Construct final slack message
                    if final_ai_content:
                        response = final_ai_content
            
            # Send response
            await say(text=response, thread_ts=thread_ts)
            
            # Store conversation to give the bot long-term memory
            try:
                await self.memory_store.store_conversation([
                    {"role": "user", "content": text},
                    {"role": "assistant", "content": response},
                ])
            except Exception as store_e:
                print(f"Warning: Failed to store conversation in memory: {store_e}")
            
            # Update reaction to success
            await self.app.client.reactions_remove(
                channel=channel_id,
                name="eyes",
                timestamp=message["ts"]
            )
            await self.app.client.reactions_add(
                channel=channel_id,
                name="white_check_mark",
                timestamp=message["ts"]
            )
            
        except Exception as e:
            await say(f"Error: {str(e)}", thread_ts=thread_ts)
            await self.app.client.reactions_remove(
                channel=channel_id,
                name="eyes",
                timestamp=message["ts"]
            )
            await self.app.client.reactions_add(
                channel=channel_id,
                name="warning",
                timestamp=message["ts"]
            )

    async def _schedule_briefing(self):
        """Run scheduled morning briefing loop.
        
        Posts a daily briefing to a configured Slack channel at a set time.
        Configure via environment variables:
            ATLAS_BRIEFING_CHANNEL: Slack channel ID to post to
            ATLAS_BRIEFING_TIME: Time to post (HH:MM, 24h format, default: 08:00)
        """
        channel = os.environ.get("ATLAS_BRIEFING_CHANNEL")
        time_str = os.environ.get("ATLAS_BRIEFING_TIME", "08:00")
        
        if not channel:
            print("ℹ️  Scheduled briefing disabled (set ATLAS_BRIEFING_CHANNEL to enable)")
            return
        
        try:
            hour, minute = map(int, time_str.split(":"))
        except ValueError:
            print(f"⚠️  Invalid ATLAS_BRIEFING_TIME: {time_str}, using 08:00")
            hour, minute = 8, 0
        
        print(f"⏰ Scheduled briefing enabled: #{channel} at {hour:02d}:{minute:02d}")
        
        while True:
            now = datetime.now()
            # Calculate next target time
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            
            wait_seconds = (target - now).total_seconds()
            print(f"⏰ Next briefing in {wait_seconds / 3600:.1f} hours")
            
            await asyncio.sleep(wait_seconds)
            
            # Generate and post briefing
            try:
                from atlas.tools.briefing import BriefingGenerator
                
                generator = BriefingGenerator(self.data_dir)
                briefing = generator.generate()
                
                await self.app.client.chat_postMessage(
                    channel=channel,
                    text=briefing.to_markdown(),
                    unfurl_links=False,
                )
                print(f"✅ Posted daily briefing to #{channel}")
            except Exception as e:
                print(f"❌ Failed to post briefing: {e}")

    async def start(self):
        """Start the bot."""
        from atlas.security import get_api_key
        
        app_token = get_api_key("slack_app_token")
        if not app_token:
            raise ValueError(
                "Slack app token not configured. Please run:\n"
                "  atlas secrets set slack_app_token\n"
                "Or set environment variable: SLACK_APP_TOKEN"
            )
        
        # Start scheduled briefing as background task
        asyncio.create_task(self._schedule_briefing())
        
        handler = AsyncSocketModeHandler(self.app, app_token)
        await handler.start_async()


async def start_bot(verbose: bool = False):
    """Entry point to start the bot."""
    bot = SlackBot(verbose=verbose)
    print("⚡️ Atlas Slack Bot is running!")
    await bot.start()

if __name__ == "__main__":
    import asyncio
    asyncio.run(start_bot())
