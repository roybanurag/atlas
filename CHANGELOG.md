# Changelog

All notable changes to Atlas will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-05-02

### Added
- **Docker Code Sandbox**: OS-level isolated `python_sandbox` and `bash_sandbox` tools with `--network none`, `--read-only`, and `--cap-drop ALL`.
- **Permission Presets**: Bulk-grant profiles (`minimal`, `reader`, `standard`, `full`) with interactive first-run onboarding.
- **Trust Levels**: `--trust none|low|medium` flag auto-approves lower-risk permission requests.
- **Tools Deny List**: `security.tools_deny` config key to block specific tools from loading.
- **Skill Builder** (`atlas skill`): AI-powered tool generation with AST safety validation.
- **Gateway Token Authentication**: Bearer-token auth between tools and the out-of-process gateway.

### Security
- **Gateway Token Cleanup**: Ephemeral `.gateway_token` is deleted on shutdown, preventing stale credential files.
- **Slack `/atlas-skill` Hardened**: Refactored to use the shared `run_skill_agent` path with AST validation and `interactive=False`, blocking unauthorized host writes via Slack.
- **Response Sanitization**: Gateway now recursively redacts echoed API keys from all response bodies.
- **Audit Log Integrity**: HMAC-chained log entries with `verify_all()` for multi-day verification.
- **Guardrail Engine**: Regex-based detection for dangerous commands, sensitive paths, credential leaks, and prompt injection attempts.
- **`list_grants` Fix**: Fixed crash when `expires_at` is the `"ONCE"` sentinel string instead of `datetime`.
- **CLI Permission Commands Fix**: Fixed `show` and `revoke` commands that were incorrectly treating `grants` list as a dict.
- **Google Tasks Permission Fix**: Corrected gateway registry to use `tasks_read` instead of `calendar_read`.

### Changed
- **Secret Manager**: Vault decryption now fails loudly with a clear error instead of silently wiping stored secrets.
- **Environment Variable Scrub**: `get_secret()` removes env vars from `os.environ` after reading to prevent accidental leakage.

---

## [0.1.0] - 2026-04-07

### Added
- **LangGraph Agent Engine**: Core conversational flow using LangGraph, handling multi-step reasoning.
- **Secure API Gateway**: Isolated out-of-process server (`localhost:18080`) that resolves and injects API credentials server-side.
- **Permission Manager**: Granular, interactive CLI/Slack consent prompts to authorize tool actions.
- **Hybrid Memory System**: SQLite-backed FTS5 search combined with semantic vector embeddings for robust recall. Includes session summarization and knowledge extraction.
- **Google Workspace Isolation**: Native zero-credential handling for Gmail, Calendar, Tasks, and Drive.
- **Local Model Routing**: Dynamic privacy-aware routing prioritizing local Ollama models.
- **Skill Builder**: AI-native capability to self-author and register new python tools.
- **CLI Suite**: Deep typer integration for terminal-first interaction (`chat`, `notes`, `memory`, `status`).

### Security
- Cryptographic hash-chained audit logging for all permission and tool usage.
- `GuardrailEngine` to intercept prompt injection attempts.
