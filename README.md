# Atlas — Privacy-Focused Personal AI Agent

**Atlas** is an open-source, privacy-first personal AI agent that runs entirely on your own hardware. It uses local LLMs via [Ollama](https://ollama.ai), keeps your data local by default, and exposes a clean permission model so you always know what the agent is doing and why.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-177%20passing-brightgreen.svg)](#testing)

---

## Table of Contents

- [Features](#features)
- [Quick Start](#quick-start)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Integrations](#integrations)
- [Security & Privacy](#security--privacy)
- [Troubleshooting](#troubleshooting)
- [Advanced Usage](#advanced-usage)
- [Contributing](#contributing)
- [Technical Documentation](#technical-documentation)

---

## Features

### 🔒 Core Features

- **Privacy-First Architecture** — All data stays local by default; no telemetry, no cloud sync
- **Local LLM Support** — Runs entirely offline via Ollama (any compatible model)
- **Remote LLMs on Demand** — Switch to OpenAI, Anthropic, or Google Gemini with `/model` mid-conversation
- **Hybrid Memory System** — SQLite FTS5 + vector embeddings for semantic search, with human-readable Markdown session logs
- **Permission Management** — Least-privilege security model; Atlas asks before acting
- **Out-of-Process API Gateway** — Credentials are injected server-side; the LLM never sees raw API keys
- **Audit Logging** — SHA-256 hash-chained security event log
- **Agent Principles** — Anti-hallucination, security, and privacy guidelines loaded into every session
- **Skill Builder** — Extend Atlas autonomously: describe a new tool in plain English and the local LLM writes and registers it

### 🛠️ Available Tools

| Tool | Description |
|------|-------------|
| **Web Search** | Real-time web search via Tavily |
| **Web Reader** | Extract content from URLs (trafilatura) |
| **Gmail** | Read, search, and send emails |
| **Google Calendar** | View, create, and update events |
| **Google Tasks** | Manage to-dos and task lists |
| **Google Drive** | Upload, download, search, and share files |
| **Notes** | Local Markdown note capture with FTS5 search |
| **Daily Briefing** | Morning summary: calendar, tasks, email, weather, news, notes |
| **Slack Bot** | Respond to @mentions and DMs |
| **Code Sandbox** | Execute Python/Bash safely in Docker containers |

---

## Quick Start

### Prerequisites

| Requirement | Notes |
|-------------|-------|
| macOS, Linux, or Windows | Supported natively |
| Python 3.11+ | Required |
| [Ollama](https://ollama.ai) | For local LLM inference |
| [uv](https://astral.sh/uv) *(recommended)* | Fast Python package installer |

### Installation

```bash
# Clone the repository
git clone https://github.com/your-username/atlas.git
cd atlas

# Install Atlas (editable mode)
pip install -e .
# or with uv:
uv pip install -e .

# Verify installation
atlas --help
```

### Initial Setup

**1. Start Ollama and pull a model:**

```bash
# Start Ollama service
ollama serve

# Pull a recommended model (in a new terminal)
ollama pull qwen3:14b        # Recommended (14B parameters)
ollama pull llama3.2:8b      # Lighter alternative
```

**2. Start chatting:**

```bash
atlas chat "Hello! What can you help me with?"
```

On first launch, Atlas will walk you through setting a default permission profile.

---

## Configuration

### API Keys & Secrets

Atlas uses the OS keychain (macOS Keychain / libsecret on Linux) for secure secret storage. Environment variables are supported as a fallback.

#### Web Search (Optional)

```bash
# Get an API key from https://tavily.com (free tier available)
atlas secrets set tavily
```

#### Gmail, Calendar, Tasks & Drive (Optional)

```bash
# 1. Create OAuth2 credentials at https://console.cloud.google.com/apis/credentials
# 2. Download the credentials JSON file
# 3. Paste the file's contents into Atlas via the interactive editor:
atlas secrets set google_oauth
```

#### Remote LLM Providers (Optional)

```bash
atlas secrets set openai       # OpenAI API key
atlas secrets set anthropic    # Anthropic API key
atlas secrets set google       # Google Gemini API key
```

#### Slack Bot (Optional)

```bash
# Get tokens from https://api.slack.com/apps
atlas secrets set slack_bot_token    # Bot User OAuth Token (xoxb-…)
atlas secrets set slack_app_token    # App-Level Token (xapp-…)
```

#### Managing Secrets

```bash
atlas secrets list                    # Show stored secret names (not values)
atlas secrets delete <service_name>   # Remove a stored secret
```

---

## Usage

### Interactive Chat

```bash
atlas chat
```

Type your message and press Enter. Type `exit`, `quit`, or `bye` to end.

### Single Message

```bash
atlas chat "What's the weather in San Francisco?"
```

### Model Selection

```bash
# Local Ollama models
atlas chat --model llama3.2:8b "Explain quantum computing"

# Remote providers (format: provider:model)
atlas chat --model openai:gpt-4o "Summarize this report"
atlas chat --model anthropic:claude-sonnet-4-20250514 "Review my code"

# Switch model mid-conversation
You: /model anthropic:claude-sonnet-4-20250514
```

### Privacy Modes

```bash
# Local only — no external API calls (default)
atlas chat --privacy local "Help me organize my notes"

# Remote allowed — enables web search and external APIs
atlas chat --privacy remote "Find me the latest news on AI"
```

### Trust Levels (Reduce Permission Prompts)

```bash
# Manually approve every action (default, most secure)
atlas chat --trust none

# Auto-approve low-risk actions (e.g. read-only)
atlas chat --trust low

# Auto-approve low and medium risk actions
atlas chat --trust medium
```

### Daily Briefing

```bash
atlas briefing              # Today's briefing
atlas briefing -d tomorrow  # Tomorrow's preview
atlas briefing --no-weather # Skip weather
```

### Quick Notes

```bash
atlas note "Great idea about caching #project-x"        # Quick capture
atlas notes add "Meeting Summary" --content "Q4 plan"   # Titled note
atlas notes search "project"                             # Full-text search
atlas notes list                                         # Recent notes
atlas notes list --tag meeting                           # Filter by tag
```

### Memory Management

```bash
atlas memory status       # Token budget, memory counts, embedding info
atlas memory compact      # Manually summarize old session messages
atlas memory consolidate  # Extract facts from recent daily logs
```

---

## Integrations

### Slack Bot

```bash
atlas slack
```

The bot responds to @mentions and DMs, uses all Atlas tools, and requests permissions via interactive Slack messages.

**Slash Commands:**

| Command | Description |
|---------|-------------|
| `/atlas-briefing` | Today's daily briefing |
| `/atlas-briefing tomorrow` | Tomorrow's preview |
| `/atlas-status` | System health check |
| `/atlas-note <text>` | Quick note with #tags |
| `/atlas-task <title> [--due] [--notes]` | Create a Google Task |
| `/atlas-model [provider:model]` | View or switch LLM model |
| `/atlas-audit [lines]` | View security audit log |
| `/atlas-secrets` | View configured secret statuses |
| `/atlas-permissions list\|revoke` | Manage permissions |
| `/atlas-notes list\|search\|show` | Manage notes |
| `/atlas-skill <description>` | Preview AI-generated tool (read-only) |

**Scheduled Morning Briefing:**

```bash
export ATLAS_BRIEFING_CHANNEL="C0123456789"   # Slack channel ID
export ATLAS_BRIEFING_TIME="08:00"             # 24h format
atlas slack                                     # Starts bot + scheduler
```

### Google Workspace

Once tokens are configured, use natural language:

```bash
atlas chat "Show me my emails from today"
atlas chat "What's on my calendar this week?"
atlas chat "Add 'Review PR' to my tasks due tomorrow"
atlas chat "Upload report.pdf to my Drive"
```

### Python/Bash Code Sandbox

```bash
atlas chat "Run this Python snippet and show me the output: [code]"
```

Code runs inside an isolated Docker container — no host filesystem access. 
*Note: You must have Docker running. For convenience, you can run `docker compose build` in the repo root to pre-build the `atlas-sandbox` image.*

---

## Security & Privacy

### How Secrets Stay Secret

Atlas uses an **out-of-process API Gateway** (`localhost:18080`) to completely isolate credentials from the LLM:

1. Tools construct credential-free declarative payloads and POST them to the Gateway
2. The Gateway resolves credentials from the OS keychain *server-side*
3. Credentials are injected into the outbound HTTP request inside the Gateway process
4. The response is sanitized (any echoed secrets are redacted) before returning to the tool

**The LLM never sees raw API keys.** See [GATEWAY.md](atlas/docs/GATEWAY.md) for full details.

### Permission System

Atlas asks before acting. When a tool needs access, a permission request appears:

```
Permission Request

email_read: Read your Gmail messages
Scope: gmail.com
Level: MEDIUM

Allow this action? [y/N]: 
Grant for how long? [once/session/hour/day/forever]: session
```

**Preset profiles** (applied on first run or via `atlas permissions setup`):

| Profile | Description |
|---------|-------------|
| `minimal` | Ask for every single action (most secure) |
| `reader` | Auto-grant all read-only access |
| `standard` | Reader + write notes and tasks |
| `full` | Standard + send email, modify calendar, upload to Drive |

### Managing Permissions

```bash
atlas permissions status               # Dashboard of all permissions
atlas permissions grant calendar_read  # Grant a specific permission
atlas permissions revoke email_send    # Revoke a permission
atlas permissions setup standard       # Apply a preset profile
atlas permissions reset                # Revoke everything
```

### Prompt Injection Guardrails

The `GuardrailEngine` analyzes every user input before it reaches the LLM, blocking common jailbreak patterns ("ignore previous instructions", "reveal your system prompt", etc.).

### Audit Log

Every security-relevant event is recorded in a SHA-256 hash-chained log:

```bash
# View audit log (stored at ~/.local/share/atlas/audit/audit.jsonl)
# (use atlas audit if you've added this CLI command)
cat ~/.local/share/atlas/audit/audit.jsonl | head -20
```

### Data Directories

All Atlas data is stored in standard OS locations:

| Purpose | macOS | Linux |
|---------|-------|-------|
| Data | `~/Library/Application Support/atlas/` | `~/.local/share/atlas/` |
| Config | `~/Library/Application Support/atlas/` | `~/.config/atlas/` |
| Memory | `…/atlas/memory/` | `…/atlas/memory/` |
| Audit logs | `…/atlas/audit/` | `…/atlas/audit/` |

---

## Troubleshooting

### Ollama Not Running

```
Error: Ollama: Not running
```

```bash
ollama serve
```

### Model Not Found

```
Error: model 'qwen3:14b' not found
```

```bash
ollama pull qwen3:14b
```

### Web Search Not Working

```bash
atlas secrets set tavily    # Get key from https://tavily.com
```

### Gmail/Calendar Not Working

1. Set up OAuth2 credentials in [Google Cloud Console](https://console.cloud.google.com)
2. Enable the Gmail, Calendar, Tasks, and/or Drive APIs
3. Download the credentials JSON
4. Run `atlas secrets set gmail` (and repeat for other services)

### Multiple Keychain Password Prompts (macOS)

Atlas uses in-memory caching to minimize prompts. For persistent "Always Allow":

1. Open **Keychain Access**
2. Search for `atlas-agent`
3. Double-click each item → **Access Control** → add your terminal app

### Web Search Gateway Error

```
Web search unavailable: the Atlas credential gateway is not running
```

The gateway starts automatically with `atlas chat`. If running a tool in isolation, start the gateway first:

```python
from atlas.gateway.server import start_server_in_background
start_server_in_background()
```

---

## Advanced Usage

### Custom Models

```bash
ollama pull codellama:13b
atlas chat --model codellama:13b "Write a binary search in Python"
```

### Extending Atlas with New Tools (Skill Builder)

Use natural language to create new tools — the local LLM writes and registers the code:

```bash
atlas skill "Add a currency conversion tool using the exchangerate.host API"
atlas skill "Create a Hacker News top-stories tool"
```

### Customizing Agent Behavior

Edit `atlas/config/principles.md` to modify Atlas's guidelines. Changes take effect on the next conversation — no restart required.

### Config File

Atlas supports a YAML config file at `~/.config/atlas/config.yaml` (or the equivalent OS path):

```yaml
security:
  tools_deny:           # Block specific tools from loading
    - "Code Sandbox"
  slack_allowed_users:  # Restrict Slack bot to specific user IDs
    - "U0123456789"
```

---

## Quick Reference

```bash
# Chat
atlas chat                              # Interactive mode
atlas chat "your question"              # Single query
atlas chat --model openai:gpt-4o "…"   # Specific model

# Briefing
atlas briefing                          # Today
atlas briefing -d tomorrow              # Tomorrow

# Notes
atlas note "content #tag"              # Quick capture
atlas notes list                        # Recent notes
atlas notes search "query"              # Search

# Secrets
atlas secrets set <service>             # Store API key
atlas secrets list                      # List stored keys

# Permissions
atlas permissions status               # Permission dashboard
atlas permissions setup                # Apply a preset profile
atlas permissions grant <permission>   # Grant manually

# Memory
atlas memory status                    # Memory stats
atlas memory compact                   # Summarize old session

# Slack
atlas slack                            # Start bot
```

### Environment Variables (Alternative to Keychain)

```bash
export TAVILY_API_KEY="tvly-…"
export OPENAI_API_KEY="sk-…"
export ANTHROPIC_API_KEY="sk-ant-…"
export SLACK_BOT_TOKEN="xoxb-…"
export SLACK_APP_TOKEN="xapp-…"
```

Atlas checks the OS keychain first, then falls back to environment variables.

---

## Testing

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run full test suite (177 tests)
pytest

# Run with coverage report
pytest --cov=atlas --cov-report=html

# Run a specific test file
pytest tests/test_graph.py -v

# Lint & format
ruff check .
ruff format .
```

---

## Technical Documentation

| Document | Description |
|----------|-------------|
| [TECHNICAL.md](atlas/docs/TECHNICAL.md) | Architecture, internals, development guide |
| [GATEWAY.md](atlas/docs/GATEWAY.md) | API Gateway security deep-dive |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to contribute |
| [SECURITY.md](SECURITY.md) | Responsible disclosure policy |
| [CHANGELOG.md](CHANGELOG.md) | Release history |

---

## License

[MIT](LICENSE) — © 2026 Atlas contributors
