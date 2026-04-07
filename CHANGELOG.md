# Changelog

All notable changes to Atlas will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
