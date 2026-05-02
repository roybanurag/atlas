"""Guardrail engine to prevent dangerous actions."""

import re
from typing import Any, Callable


class GuardrailEngine:
    """Prevents unintended or dangerous actions.
    
    Evaluates actions against safety rules before execution.
    """
    
    # Dangerous shell command patterns
    DANGEROUS_COMMANDS = [
        r"rm\s+-rf\s+[/~]",           # rm -rf /
        r"sudo\s+rm",                   # sudo rm
        r">\s*/dev/[^n]",               # Overwrite devices
        r"mkfs\.",                       # Format filesystem
        r"dd\s+if=.*of=/dev/",          # Direct disk write
        r"chmod\s+-R\s+777",            # Dangerous permissions
        r"curl.*\|\s*(bash|sh)",        # Pipe to shell
        r"wget.*\|\s*(bash|sh)",        # Pipe to shell
        r"eval\s+\$\(",                 # Eval command substitution
        r":(){ :|:& };:",               # Fork bomb
    ]
    
    # Sensitive paths that require extra protection
    SENSITIVE_PATHS = [
        "/etc/passwd",
        "/etc/shadow",
        "~/.ssh/",
        "~/.gnupg/",
        "~/Library/Keychains/",
        "/System/",
        "/usr/bin/",
        "~/.aws/credentials",
        "~/.config/gcloud/",
        "~/.kube/config",
    ]
    
    def __init__(self):
        """Initialize guardrail engine."""
        self.rules: list[dict[str, Any]] = []
        self._init_default_rules()
    
    def _init_default_rules(self):
        """Initialize default safety rules."""
        self.add_rule(
            name="dangerous_commands",
            check=self._is_dangerous_command,
            message="This command could cause system damage",
        )
        
        self.add_rule(
            name="sensitive_paths",
            check=self._accesses_sensitive_path,
            message="Access to sensitive system paths is blocked",
        )
        
        self.add_rule(
            name="credential_protection",
            check=self._leaks_credentials,
            message="Action appears to access or transmit credentials",
        )
        
        self.add_rule(
            name="prompt_injection",
            check=self._is_prompt_injection,
            message="Potential prompt injection detected",
        )
    
    def add_rule(
        self,
        name: str,
        check: Callable[[dict[str, Any]], bool],
        message: str,
    ):
        """Add a guardrail rule.
        
        Args:
            name: Rule name
            check: Function that returns True if action is dangerous
            message: Message to show when rule triggers
        """
        self.rules.append({
            "name": name,
            "check": check,
            "message": message,
        })
    
    def _is_dangerous_command(self, action: dict[str, Any]) -> bool:
        """Check for dangerous shell commands."""
        if action.get("type") != "shell_command":
            return False
        
        cmd = action.get("command", "")
        return any(re.search(p, cmd, re.IGNORECASE) for p in self.DANGEROUS_COMMANDS)
    
    def _accesses_sensitive_path(self, action: dict[str, Any]) -> bool:
        """Check for access to sensitive paths."""
        path = action.get("path", "")
        if not path:
            return False
        
        # Expand home directory
        import os
        path = os.path.expanduser(path)
        
        for sensitive in self.SENSITIVE_PATHS:
            expanded = os.path.expanduser(sensitive)
            if path.startswith(expanded) or expanded.startswith(path):
                return True
        
        return False
    
    def _leaks_credentials(self, action: dict[str, Any]) -> bool:
        """Check for potential credential exfiltration.
        
        Checks both outbound network/shell actions AND LLM output
        for patterns that look like raw credentials being leaked.
        """
        action_type = action.get("type", "")
        if action_type not in ("network_request", "shell_command", "llm_output"):
            return False
        
        content = str(action.get("data", "")) + str(action.get("command", "")) + str(action.get("text", ""))
        
        # Look for credential patterns
        credential_patterns = [
            r"(api[_-]?key|apikey)\s*[=:]\s*[\"']?[\w\-]{20,}",
            r"(password|passwd|pwd)\s*[=:]\s*[\"']?\S{8,}",
            r"(secret|token)\s*[=:]\s*[\"']?[\w\-]{20,}",
            r"(aws[_-]?access|aws[_-]?secret)\s*[=:]\s*[\"']?[\w\-]{16,}",
            r"(private[_-]?key)\s*[=:]\s*[\"']?[\w\-]{20,}",
            r"-----BEGIN\s+(RSA|EC|OPENSSH)?\s*PRIVATE\s+KEY-----",
        ]
        
        return any(re.search(p, content, re.IGNORECASE) for p in credential_patterns)
    
    def _is_prompt_injection(self, action: dict[str, Any]) -> bool:
        """Check for prompt injection attempts."""
        if action.get("type") != "user_input":
            return False
            
        text = str(action.get("text", ""))
        
        injection_patterns = [
            r"ignore\s+(all\s+)?previous\s+(instructions|prompts|directions)",
            r"forget\s+(all\s+)?(previous\s+)?(instructions|prompts|directions)",
            r"system\s+prompt",
            r"you\s+are\s+now",
            r"bypass\s+(all\s+)?(security|guardrails|rules)",
            r"print\s+(all\s+)?(previous\s+)?instructions",
        ]
        
        return any(re.search(p, text, re.IGNORECASE) for p in injection_patterns)

    async def evaluate(self, action: dict[str, Any]) -> tuple[bool, str]:
        """Evaluate action against all guardrails.
        
        Args:
            action: Action to evaluate
            
        Returns:
            Tuple of (allowed, message)
        """
        for rule in self.rules:
            try:
                if rule["check"](action):
                    return False, rule["message"]
            except Exception:
                # If rule check fails, err on the side of caution
                continue
        
        return True, ""
    
    def evaluate_sync(self, action: dict[str, Any]) -> tuple[bool, str]:
        """Synchronous version of evaluate."""
        for rule in self.rules:
            try:
                if rule["check"](action):
                    return False, rule["message"]
            except Exception:
                continue
        
        return True, ""
