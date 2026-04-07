"""Permission management system."""

import json
import os
import base64
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Callable

from atlas.config.paths import get_data_dir
from atlas.security import keyring_cache
from cryptography.fernet import Fernet


# --- Decorator-based tool permission registry ---
_DECORATOR_PERMISSIONS: dict[str, tuple[str, str]] = {}


def requires_permission(permission: str, scope: str, level: "PermissionLevel | None" = None):
    """Decorator to register a tool's required permission.
    
    Usage:
        @requires_permission("email_read", "gmail.com")
        def search_emails(query: str) -> str:
            ...
    """
    def decorator(func):
        _DECORATOR_PERMISSIONS[func.__name__] = (permission, scope)
        func._atlas_permission = (permission, scope, level)
        return func
    return decorator


class PermissionLevel(Enum):
    """Permission sensitivity levels."""
    LOW = 1       # Basic operations
    MEDIUM = 2    # File read, network
    HIGH = 3      # File write, sensitive data
    CRITICAL = 4  # System changes, installs


class GrantDuration(Enum):
    """How long a permission grant lasts."""
    ONCE = "once"
    SESSION = "session"
    HOUR = "hour"
    DAY = "day"
    FOREVER = "forever"


@dataclass
class PermissionGrant:
    """A granted permission."""
    permission: str
    scope: str
    granted: bool
    granted_at: datetime
    expires_at: datetime | None
    granted_by: str = "user"


@dataclass
class PermissionConfig:
    """Configuration for a permission type."""
    level: PermissionLevel
    scope_type: str  # "path", "domain", "global", "command"
    default: bool = False
    requires_confirmation: bool = False
    description: str = ""


class PermissionManager:
    """Least-privilege permission management with persistent storage."""
    
    SERVICE_NAME = "atlas-agent"
    KEY_NAME = "permission-store-key"
    STORAGE_DIR = get_data_dir()
    STORAGE_FILE = STORAGE_DIR / "permissions.json"
    
    # --- Permission Presets ---
    PERMISSION_PRESETS: dict[str, list[str]] = {
        "minimal": [],
        "reader": [
            "email_read", "calendar_read", "tasks_read",
            "drive_read", "notes_read", "internet_access",
        ],
        "standard": [
            "email_read", "calendar_read", "tasks_read",
            "drive_read", "notes_read", "internet_access",
            "notes_write", "tasks_write",
        ],
        "full": [
            "email_read", "calendar_read", "tasks_read",
            "drive_read", "notes_read", "internet_access",
            "notes_write", "tasks_write",
            "email_send", "calendar_write", "drive_write",
        ],
    }
    
    # Default permission configurations
    DEFAULT_PERMISSIONS: dict[str, PermissionConfig] = {
        "read_files": PermissionConfig(
            level=PermissionLevel.LOW,
            scope_type="path",
            requires_confirmation=True,
            description="Read files from your computer",
        ),
        "write_files": PermissionConfig(
            level=PermissionLevel.MEDIUM,
            scope_type="path",
            requires_confirmation=True,
            description="Create or modify files",
        ),
        "delete_files": PermissionConfig(
            level=PermissionLevel.HIGH,
            scope_type="path",
            requires_confirmation=True,
            description="Delete files permanently",
        ),
        "local_network": PermissionConfig(
            level=PermissionLevel.LOW,
            scope_type="domain",
            requires_confirmation=True,
            description="Access local services",
        ),
        "internet_access": PermissionConfig(
            level=PermissionLevel.MEDIUM,
            scope_type="domain",
            requires_confirmation=True,
            description="Access external websites and APIs",
        ),
        "run_commands": PermissionConfig(
            level=PermissionLevel.CRITICAL,
            scope_type="command",
            requires_confirmation=True,
            description="Run terminal commands",
        ),
        "install_software": PermissionConfig(
            level=PermissionLevel.CRITICAL,
            scope_type="global",
            requires_confirmation=True,
            description="Install or modify software",
        ),
        "email_read": PermissionConfig(
            level=PermissionLevel.MEDIUM,
            scope_type="domain",
            requires_confirmation=True,
            description="Read emails from your Gmail account",
        ),
        "email_send": PermissionConfig(
            level=PermissionLevel.HIGH,
            scope_type="domain",
            requires_confirmation=True,
            description="Send emails from your Gmail account",
        ),
        "calendar_read": PermissionConfig(
            level=PermissionLevel.MEDIUM,
            scope_type="domain",
            requires_confirmation=True,
            description="Read events from your Google Calendar",
        ),
        "calendar_write": PermissionConfig(
            level=PermissionLevel.HIGH,
            scope_type="domain",
            requires_confirmation=True,
            description="Create, update, or delete calendar events",
        ),
        "drive_read": PermissionConfig(
            level=PermissionLevel.MEDIUM,
            scope_type="domain",
            requires_confirmation=True,
            description="Read files from your Google Drive",
        ),
        "drive_write": PermissionConfig(
            level=PermissionLevel.HIGH,
            scope_type="domain",
            requires_confirmation=True,
            description="Upload, modify, or delete files in your Google Drive",
        ),
        "notes_read": PermissionConfig(
            level=PermissionLevel.LOW,
            scope_type="global",
            default=True,  # Allow reading local notes by default
            description="Read your local notes",
        ),
        "notes_write": PermissionConfig(
            level=PermissionLevel.MEDIUM,
            scope_type="global",
            requires_confirmation=True,
            description="Create or delete local notes",
        ),
        "tasks_read": PermissionConfig(
            level=PermissionLevel.MEDIUM,
            scope_type="domain",
            requires_confirmation=True,
            description="Read your Google Tasks",
        ),
        "tasks_write": PermissionConfig(
            level=PermissionLevel.HIGH,
            scope_type="domain",
            requires_confirmation=True,
            description="Create or modify Google Tasks",
        ),
    }

    # Map tools to required permissions
    TOOL_PERMISSIONS = {
        "web_search": ("internet_access", "tavily.com"),
        "python_sandbox": ("run_commands", "docker"),
        "bash_sandbox": ("run_commands", "docker"),
        # Gmail tools
        "search_emails": ("email_read", "gmail.com"),
        "read_email": ("email_read", "gmail.com"),
        "send_email": ("email_send", "gmail.com"),
        "list_recent_emails": ("email_read", "gmail.com"),
        # Calendar tools
        "list_events": ("calendar_read", "calendar.google.com"),
        "search_events": ("calendar_read", "calendar.google.com"),
        "create_event": ("calendar_write", "calendar.google.com"),
        "update_event": ("calendar_write", "calendar.google.com"),
        "delete_event": ("calendar_write", "calendar.google.com"),
        # Google Drive tools
        "list_files": ("drive_read", "drive.google.com"),
        "search_files": ("drive_read", "drive.google.com"),
        "get_file_metadata": ("drive_read", "drive.google.com"),
        "download_file": ("drive_read", "drive.google.com"),
        "upload_file": ("drive_write", "drive.google.com"),
        "create_folder": ("drive_write", "drive.google.com"),
        "delete_file": ("drive_write", "drive.google.com"),
        "share_file": ("drive_write", "drive.google.com"),
        # Notes tools
        "quick_note": ("notes_write", "local"),
        "search_notes": ("notes_read", "local"),
        "list_notes": ("notes_read", "local"),
        "read_note": ("notes_read", "local"),
        "delete_note": ("notes_write", "local"),
        "list_tags": ("notes_read", "local"),
        # Web Reader
        "read_url": ("internet_access", "external"),
        # Briefing
        "get_daily_briefing": ("calendar_read", "calendar.google.com"),
        # Google Tasks
        "list_task_lists": ("tasks_read", "tasks.google.com"),
        "list_tasks": ("tasks_read", "tasks.google.com"),
        "create_task": ("tasks_write", "tasks.google.com"),
    }
    
    def __init__(
        self,
        config_path: Path | None = None,
        ui_handler: Callable | None = None,
        audit_logger: Any | None = None,
        trust_level: str = "none",
    ):
        """Initialize permission manager.
        
        Args:
            config_path: Path to permission config file
            ui_handler: Function to request permissions from user
            audit_logger: Audit logger instance
            trust_level: Auto-grant level — "none", "low", or "medium".
                "low": auto-grant LOW-level permissions without prompting.
                "medium": auto-grant LOW and MEDIUM-level permissions.
        """
        self.config_path = config_path
        self.ui_handler = ui_handler
        self.audit = audit_logger
        self.trust_level = trust_level
        self.grants: list[PermissionGrant] = []
        self.permissions = dict(self.DEFAULT_PERMISSIONS)
        self._active_preset: str | None = None
        
        # Ensure storage directory exists
        self.STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        
        # Initialize encryption
        self._fernet = Fernet(self._get_key())
        
        if config_path and config_path.exists():
            self._load_config()
            
        # Load persistent grants (also loads active preset)
        self._load_grants()
    
    def _get_key(self) -> bytes:
        """Get or create encryption key from keyring."""
        key_b64 = keyring_cache.get_password(self.SERVICE_NAME, self.KEY_NAME)
        if not key_b64:
            # Generate new key
            key = Fernet.generate_key()
            key_b64 = base64.b64encode(key).decode('utf-8')
            # Store in keyring (with caching)
            try:
                keyring_cache.set_password(self.SERVICE_NAME, self.KEY_NAME, key_b64)
            except Exception as e:
                # Security: Fail securely if the encryption key cannot be saved
                raise RuntimeError(
                    f"Failed to store permission encryption key in keyring: {e}\n"
                    "Aborting to prevent data loss or DoS against permission store."
                ) from e
                
            return key
        return base64.b64decode(key_b64)

    def _load_grants(self):
        """Load and decrypt grants from storage."""
        if not self.STORAGE_FILE.exists():
            return

        try:
            with open(self.STORAGE_FILE, "rb") as f:
                encrypted_data = f.read()
            
            decrypted_data = self._fernet.decrypt(encrypted_data)
            data = json.loads(decrypted_data)
            
            # Load active preset name
            self._active_preset = data.get("active_preset", None)
            
            for grant_data in data.get("grants", []):
                # Reconstruct datetime objects
                if grant_data.get("granted_at"):
                    grant_data["granted_at"] = datetime.fromisoformat(grant_data["granted_at"])
                if grant_data.get("expires_at"):
                    grant_data["expires_at"] = datetime.fromisoformat(grant_data["expires_at"])
                
                self.grants.append(PermissionGrant(**grant_data))
        except Exception as e:
            print(f"Error loading permission grants: {e}")
            # Start fresh if corrupted
            self.grants = []

    def _save_grants(self):
        """Encrypt and save grants to storage."""
        data = {
            "grants": [],
            "active_preset": self._active_preset,
        }
        
        for grant in self.grants:
            grant_dict = asdict(grant)
            # Serialize datetimes
            if grant_dict.get("granted_at"):
                grant_dict["granted_at"] = grant.granted_at.isoformat()
            if grant_dict.get("expires_at"):
                grant_dict["expires_at"] = grant.expires_at.isoformat()
            data["grants"].append(grant_dict)
            
        json_data = json.dumps(data).encode('utf-8')
        encrypted_data = self._fernet.encrypt(json_data)
        
        with open(self.STORAGE_FILE, "wb") as f:
            f.write(encrypted_data)

    def _load_config(self):
        """Load permission config from file."""
        import yaml
        with open(self.config_path) as f:
            config = yaml.safe_load(f)
        
        for name, perm_config in config.get("permissions", {}).items():
            self.permissions[name] = PermissionConfig(
                level=PermissionLevel[perm_config.get("level", "medium").upper()],
                scope_type=perm_config.get("scope", "global"),
                default=perm_config.get("default", False),
                requires_confirmation=perm_config.get("requires_confirmation", False),
                description=perm_config.get("description", ""),
            )
    
    async def check(
        self,
        permission: str,
        scope: str = "*",
        context: dict[str, Any] | None = None,
    ) -> bool:
        """Check if permission is granted for scope.
        
        Checks in order:
        1. Exact grant match
        2. Wildcard/pattern grant match (fnmatch)
        3. Default permissions
        4. Trust-level auto-grant
        5. Interactive request
        
        Args:
            permission: Permission name
            scope: Specific scope (path, domain, etc.)
            context: Additional context for the request
            
        Returns:
            True if permission granted
        """
        now = datetime.now()
        
        # 1. & 2. Check all grants (exact and wildcard)
        active_grants = []
        has_permission = False
        
        for grant in self.grants:
            # Check expiry
            if grant.expires_at and now > grant.expires_at:
                continue
                
            active_grants.append(grant)
            
            # If we already found permission we don't need to check further, 
            # but we continue loop to clean up all expired grants
            if not has_permission:
                if grant.permission == permission and grant.scope == scope:
                    has_permission = grant.granted
                elif fnmatch(permission, grant.permission) and fnmatch(scope, grant.scope):
                    has_permission = grant.granted
                    
        # Update grants list if we cleaned up expired ones
        if len(active_grants) != len(self.grants):
            self.grants = active_grants
            self._save_grants()
            
        if has_permission:
            return True
        
        # 3. Check defaults
        perm_config = self.permissions.get(permission)
        if perm_config and perm_config.default:
            return True
        
        # 4. Trust-level auto-grant
        if perm_config and self.trust_level != "none":
            auto_grant = False
            if self.trust_level == "low" and perm_config.level == PermissionLevel.LOW:
                auto_grant = True
            elif self.trust_level == "medium" and perm_config.level.value <= PermissionLevel.MEDIUM.value:
                auto_grant = True
            
            if auto_grant:
                await self.grant(permission, scope, duration="day", granted_by="auto-trust")
                if self.audit:
                    self.audit.log("auto_grant", {
                        "permission": permission,
                        "scope": scope,
                        "trust_level": self.trust_level,
                    })
                return True
        
        # 5. Interactive request
        return await self.request(permission, scope, context)
    
    async def request(
        self,
        permission: str,
        scope: str,
        context: dict[str, Any] | None = None,
    ) -> bool:
        """Request permission from user.
        
        Args:
            permission: Permission name
            scope: Specific scope
            context: Additional context
            
        Returns:
            True if user granted permission
        """
        perm_config = self.permissions.get(permission)
        
        if not self.ui_handler:
            # No UI handler, default deny
            return False
        
        # Suggest a default duration based on level
        if perm_config and perm_config.level.value >= PermissionLevel.CRITICAL.value:
            suggested_duration = "once"
        else:
            suggested_duration = "day"
        
        # Build request
        request = {
            "permission": permission,
            "description": perm_config.description if perm_config else permission,
            "scope": scope,
            "level": perm_config.level.name if perm_config else "UNKNOWN",
            "context": context,
            "suggested_duration": suggested_duration,
        }
        
        # Request from user
        response = await self.ui_handler(request)
        
        granted = response.get("granted", False)
        duration = response.get("duration", suggested_duration)
        
        # Remove existing exact matches to avoid duplicates
        self.grants = [g for g in self.grants if not (g.permission == permission and g.scope == scope)]
        
        # Store new grant
        expires_at = self._calculate_expiry(duration)
        self.grants.append(PermissionGrant(
            permission=permission,
            scope=scope,
            granted=granted,
            granted_at=datetime.now(),
            expires_at=expires_at,
            granted_by=response.get("granted_by", "user"),
        ))
        
        # Save updates
        self._save_grants()
        
        # Audit log
        if self.audit:
            self.audit.log_permission(
                permission=permission,
                scope=scope,
                granted=granted,
                duration=duration.value if isinstance(duration, GrantDuration) else duration,
                context=context,
            )
        
        return granted
    
    def _calculate_expiry(self, duration: GrantDuration | str) -> datetime | None:
        """Calculate expiry time from duration."""
        if isinstance(duration, str):
            duration = GrantDuration(duration)
        
        if duration == GrantDuration.ONCE:
            return datetime.now()  # Expires immediately after use
        elif duration == GrantDuration.SESSION:
            return None  # Expires when process ends
        elif duration == GrantDuration.HOUR:
            return datetime.now() + timedelta(hours=1)
        elif duration == GrantDuration.DAY:
            return datetime.now() + timedelta(days=1)
        elif duration == GrantDuration.FOREVER:
            return None
        return None
    
    async def revoke(self, permission: str, scope: str = "*"):
        """Revoke a previously granted permission."""
        original_len = len(self.grants)
        self.grants = [g for g in self.grants if not (g.permission == permission and g.scope == scope)]
        
        if len(self.grants) != original_len:
            self._save_grants()
            if self.audit:
                self.audit.log("permission_revoked", {
                    "permission": permission,
                    "scope": scope,
                })
    
    async def grant(
        self,
        permission: str,
        scope: str = "*",
        duration: str = "day",
        granted_by: str = "user",
    ):
        """Programmatically grant a permission.
        
        Args:
            permission: Permission name (supports wildcards like "calendar_*")
            scope: Scope (supports wildcards like "*.google.com")
            duration: Grant duration — "once", "session", "hour", "day", "forever"
            granted_by: Who granted it ("user", "preset", "auto-trust", etc.)
        """
        # Remove existing exact matches
        self.grants = [g for g in self.grants if not (g.permission == permission and g.scope == scope)]
        
        expires_at = self._calculate_expiry(duration)
        self.grants.append(PermissionGrant(
            permission=permission,
            scope=scope,
            granted=True,
            granted_at=datetime.now(),
            expires_at=expires_at,
            granted_by=granted_by,
        ))
        self._save_grants()
        
        if self.audit:
            self.audit.log("permission_granted", {
                "permission": permission,
                "scope": scope,
                "duration": duration,
                "granted_by": granted_by,
            })
    
    async def apply_preset(self, preset_name: str, duration: str = "forever"):
        """Apply a permission preset, granting all its permissions.
        
        Args:
            preset_name: One of "minimal", "reader", "standard", "full"
            duration: How long the preset grants last
        """
        if preset_name not in self.PERMISSION_PRESETS:
            raise ValueError(
                f"Unknown preset '{preset_name}'. "
                f"Valid presets: {list(self.PERMISSION_PRESETS.keys())}"
            )
        
        permissions = self.PERMISSION_PRESETS[preset_name]
        
        # Determine appropriate scope for each permission
        for perm_name in permissions:
            perm_config = self.permissions.get(perm_name)
            scope = "*"  # Default wildcard scope
            await self.grant(perm_name, scope, duration=duration, granted_by=f"preset:{preset_name}")
        
        self._active_preset = preset_name
        self._save_grants()
    
    def get_active_preset(self) -> str | None:
        """Return the name of the currently active preset, or None."""
        return self._active_preset
    
    async def reset_all(self):
        """Revoke all grants and clear the active preset."""
        self.grants = []
        self._active_preset = None
        self._save_grants()
        if self.audit:
            self.audit.log("permissions_reset", {})
    
    def list_grants(self) -> list[PermissionGrant]:
        """List all active permission grants."""
        now = datetime.now()
        active = []
        for grant in self.grants:
            if grant.expires_at and grant.expires_at <= now:
                 continue
                 
            active.append(grant)
            
        if len(self.grants) != len(active):
             self.grants = active
             self._save_grants()
             
        return active
    
    def get_permission_status(self) -> list[dict]:
        """Get a summary of all permissions with their grant status.
        
        Returns a list of dicts with permission name, description, level,
        grant status, expiry, and granted_by.
        """
        now = datetime.now()
        result = []
        
        for perm_name, perm_config in self.permissions.items():
            # Find matching grants (exact or wildcard)
            status = "unset"
            expires = None
            granted_by = None
            
            for grant in self.grants:
                if grant.expires_at and now > grant.expires_at:
                    continue
                
                if fnmatch(perm_name, grant.permission):
                    status = "granted" if grant.granted else "denied"
                    expires = grant.expires_at
                    granted_by = grant.granted_by
                    break
            
            if status == "unset" and perm_config.default:
                status = "default"
            
            result.append({
                "permission": perm_name,
                "description": perm_config.description,
                "level": perm_config.level.name,
                "status": status,
                "expires": expires,
                "granted_by": granted_by,
            })
        
        return result
