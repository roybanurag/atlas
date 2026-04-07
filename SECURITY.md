# Security Policy

## Supported Versions

Atlas is under active development. Currently, only the `main` branch is supported with security updates.

## Reporting a Vulnerability

If you discover a security vulnerability in Atlas, please report it privately. **Do not disclose it publicly or create a public GitHub issue.**

To report a vulnerability:
1. Contact the maintainers directly at the email provided by the repository owner, or use GitHub's private vulnerability reporting feature if enabled on the repository.
2. Include the following information in your report:
   - A detailed description of the vulnerability.
   - Steps to reproduce the issue.
   - Potential impact (e.g., credential leakage, prompt injection bypass).
   - Suggestions for mitigation or a patch if you have one.

### What to Expect
- You will receive acknowledgment of your report within 48 hours.
- We will provide updates on our progress and notify you when the fix is deployed.
- We will publicly acknowledge your contribution in the advisory (if you wish).

## Scope

Security issues in Atlas's core components are in scope, including:
- **API Gateway** (`atlas/gateway/`): Credential injection, isolation bypasses.
- **Permission Manager** (`atlas/security/permissions.py`): Authorization bypasses.
- **Guardrail Engine** (`atlas/security/guardrails.py`): Severe prompt injection bypasses that lead to immediate destructive actions or credential exfiltration.
- **Secret Manager** (`atlas/security/secrets.py`): Plaintext credential leakage.

## Out of Scope

- Vulnerabilities in third-party dependencies (e.g., `langgraph`, `ollama`). Please report these upstream.
- Default behaviors of the underlying LLM (e.g., general hallucinations or factual inaccuracies), unless they bypass the `GuardrailEngine` to execute destructive tools.
- Vulnerabilities that require physical access to the user's unlocked machine.
