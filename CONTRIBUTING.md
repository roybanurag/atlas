# Contributing to Atlas

First off, thank you for considering contributing to Atlas! 

We welcome contributions of all kinds: bug fixes, new tools, documentation improvements, and architectural enhancements.

## Getting Started

1. Fork the repository on GitHub.
2. Clone your fork locally.
3. Install the development dependencies:

```bash
cd atlas
pip install -e ".[dev]"
# Or using uv: uv pip install -e ".[dev]"
```

## Development Workflow

### Code Style
Atlas adheres to PEP 8. We use `ruff` for fast linting and formatting.

```bash
# Check for issues
ruff check .

# Format the code
ruff format .
```

Please run these commands before submitting any pull requests. CI will fail if the code does not conform to the `ruff` rules.

### Running Tests

Atlas has an extensive test suite using `pytest`. Aim to include tests for all new features.

```bash
# Run the entire test suite
pytest

# Run tests with coverage
pytest --cov=atlas --cov-report=html
```

Currently, the master branch has over 175 passing tests and covers the graph layer, memory subsystem, and secure API gateway.

## Adding New Tools

If you want to add a new tool to Atlas, follow this checklist:

1. **Create the Tool File:** Place your tool in `atlas/tools/builtin/` (or create a new domain-specific file in `atlas/tools/`).
2. **Use `@tool` Decorator:** Use the `langchain_core.tools.tool` decorator.
3. **Write a Good Description:** The docstring is the prompt the LLM sees. Make it clear and tell the LLM exactly what arguments to provide.
4. **Register Permissions (Important):** If your tool touches the internet, reads files, or interacts with a sensitive API, it **must** be permissioned. Add its mapping to `PermissionManager.TOOL_PERMISSIONS` in `atlas/security/permissions.py`.
5. **Gateway Integration:** If the tool uses API keys, do not prompt the user for the key inside the tool. Route the request through the `APIGateway` (`localhost:18080`) so credentials remain isolated.
6. **Export & Load:** Export the tool in `atlas/tools/__init__.py` and add it to `tools_loader.py`.

## Pull Request Process

1. Create a descriptive feature branch (`git checkout -b feature/awesome-new-tool`).
2. Implement your changes, adding tests and updating documentation as needed.
3. Ensure all tests pass (`pytest`) and code is formatted (`ruff`).
4. Submit the PR. Describe exactly what the change does and why it's needed.

## Modifying Agent Principles

If you are proposing changes to `config/principles.md`, understand that this directly impacts the prompt and the core behavior of the LLM. 
- Please include test transcripts showing that the LLM behaves as expected with your new rules.
- Ensure the new principles do not conflict with the core directives of Privacy and Security.

## Security Disclosures

If you find a security vulnerability (e.g., a way to bypass the API Gateway or Permission Manager), please do not open a public issue. Refer to our [Security Policy](SECURITY.md) for disclosure guidelines.
