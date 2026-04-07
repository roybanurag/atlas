"""Audit logging with integrity verification."""

import hashlib
import hmac
import json
from datetime import datetime
from pathlib import Path

from atlas.security.token_encryption import get_encryption_key
from typing import Any


class AuditLogger:
    """Immutable audit log for all agent actions.
    
    Uses hash chaining for tamper detection. Each log entry contains
    the hash of the previous entry, creating a verifiable chain.
    """
    
    def __init__(self, log_dir: str | Path):
        """Initialize audit logger.
        
        Args:
            log_dir: Directory for audit log files
        """
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # Current day's log file
        self.current_log = self.log_dir / f"audit_{datetime.now().strftime('%Y%m%d')}.jsonl"
        self._last_hash: str | None = None
        
        # Load last hash if log exists
        if self.current_log.exists():
            self._load_last_hash()
    
    def _load_last_hash(self):
        """Load the last hash from existing log."""
        try:
            with open(self.current_log, "rb") as f:
                # Read last line
                f.seek(0, 2)  # End of file
                if f.tell() == 0:
                    return
                
                # Find last newline
                pos = f.tell() - 1
                while pos > 0:
                    f.seek(pos)
                    if f.read(1) == b"\n":
                        break
                    pos -= 1
                
                last_line = f.readline().decode().strip()
                if last_line:
                    entry = json.loads(last_line)
                    self._last_hash = entry.get("hash")
        except Exception:
            pass
    
    def log(self, event_type: str, data: dict[str, Any]):
        """Log an event with chained integrity hash.
        
        Args:
            event_type: Type of event
            data: Event data
        """
        # Rotate log file if new day
        today = datetime.now().strftime("%Y%m%d")
        if not self.current_log.name.endswith(f"{today}.jsonl"):
            self.current_log = self.log_dir / f"audit_{today}.jsonl"
            self._last_hash = None
        
        entry = {
            "timestamp": datetime.now().isoformat(),
            "type": event_type,
            "data": self._sanitize(data),
            "prev_hash": self._last_hash,
        }
        
        # Compute HMAC for integrity chain using master key
        entry_str = json.dumps(entry, sort_keys=True)
        secret_key = get_encryption_key()
        entry["hash"] = hmac.new(secret_key, entry_str.encode(), hashlib.sha256).hexdigest()
        self._last_hash = entry["hash"]
        
        # Append to log file
        with open(self.current_log, "a") as f:
            f.write(json.dumps(entry) + "\n")
    
    def _sanitize(self, data: dict[str, Any]) -> dict[str, Any]:
        """Sanitize data to remove sensitive information."""
        sanitized = {}
        sensitive_keys = {"password", "secret", "token", "key", "credential"}
        
        for k, v in data.items():
            if any(s in k.lower() for s in sensitive_keys):
                sanitized[k] = "***REDACTED***"
            elif isinstance(v, dict):
                sanitized[k] = self._sanitize(v)
            else:
                sanitized[k] = v
        
        return sanitized
    
    def log_permission(
        self,
        permission: str,
        scope: str,
        granted: bool,
        **kwargs,
    ):
        """Log permission decision."""
        self.log("permission", {
            "permission": permission,
            "scope": scope,
            "granted": granted,
            **kwargs,
        })
    
    def log_action(
        self,
        action: str,
        params: dict[str, Any],
        result: str,
    ):
        """Log executed action."""
        self.log("action", {
            "action": action,
            "params": self._sanitize(params),
            "result": result,
        })
    
    def log_tool_call(
        self,
        tool: str,
        input_data: dict[str, Any],
        output: str | None = None,
        error: str | None = None,
    ):
        """Log tool invocation."""
        self.log("tool_call", {
            "tool": tool,
            "input": self._sanitize(input_data),
            "output": output[:500] if output else None,
            "error": error,
        })
    
    def verify_integrity(self) -> bool:
        """Verify audit log hasn't been tampered with.
        
        Returns:
            True if log integrity is intact
        """
        if not self.current_log.exists():
            return True
        
        prev_hash = None
        try:
            with open(self.current_log) as f:
                for line in f:
                    if not line.strip():
                        continue
                    
                    entry = json.loads(line)
                    
                    # Check chain
                    if entry.get("prev_hash") != prev_hash:
                        return False
                    
                    # Recompute HMAC
                    check_entry = {k: v for k, v in entry.items() if k != "hash"}
                    check_str = json.dumps(check_entry, sort_keys=True).encode()
                    secret_key = get_encryption_key()
                    expected = hmac.new(secret_key, check_str, hashlib.sha256).hexdigest()
                    
                    if entry.get("hash") != expected:
                        return False
                    
                    prev_hash = entry["hash"]
            
            return True
        except Exception:
            return False
    
    def get_recent(self, n: int = 50) -> list[dict[str, Any]]:
        """Get recent log entries.
        
        Args:
            n: Number of entries to retrieve
            
        Returns:
            List of log entries
        """
        if not self.current_log.exists():
            return []
        
        entries = []
        with open(self.current_log) as f:
            for line in f:
                if line.strip():
                    entries.append(json.loads(line))
        
        return entries[-n:]
