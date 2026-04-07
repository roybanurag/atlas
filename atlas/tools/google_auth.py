"""Shared Google OAuth2 authentication with encrypted token storage.

Consolidates the token save/load/refresh pattern used by Gmail, Calendar,
Google Drive, and Google Tasks into a single reusable class.
"""

import json
import os
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from atlas.config.paths import get_data_dir
from atlas.security.token_encryption import get_encryption_key


class GoogleServiceAuth:
    """Manages OAuth2 tokens for a Google API service.
    
    Handles:
    - Encrypted token storage (Fernet)
    - Token refresh on expiry
    - OAuth browser flow for initial auth
    - Credential path resolution via keyring with fallbacks
    
    Usage:
        auth = GoogleServiceAuth(
            service_name="Gmail",
            scopes=["https://www.googleapis.com/auth/gmail.readonly"],
            token_filename="gmail_token.json",
            api_key_name="gmail",
        )
        service = auth.get_service("gmail", "v1")
    """
    
    def __init__(
        self,
        service_name: str,
        scopes: list[str],
        token_filename: str,
        api_key_name: str,
        fallback_api_keys: list[str] | None = None,
    ):
        """Initialize auth for a Google API service.
        
        Args:
            service_name: Human-readable name (e.g. "Gmail") for error messages.
            scopes: OAuth2 scopes required by this service.
            token_filename: Filename for the encrypted token (e.g. "gmail_token.json").
            api_key_name: Primary key name in atlas secrets for the credentials path.
            fallback_api_keys: Optional list of fallback key names to try (e.g. 
                ["calendar", "gmail"] for Tasks which can reuse those credentials).
        """
        self.service_name = service_name
        self.service_name = service_name
        self.scopes = scopes
        self.token_key = token_filename
        self.api_key_name = api_key_name
        self.fallback_api_keys = fallback_api_keys or []
    
    def _save_token(self, creds: Credentials) -> None:
        """Serialize and save OAuth2 token to unified secrets vault."""
        token_data = {
            'token': creds.token,
            'refresh_token': creds.refresh_token,
            'token_uri': creds.token_uri,
            'client_id': creds.client_id,
            'client_secret': creds.client_secret,
            'scopes': creds.scopes,
        }
        
        from atlas.security.secrets import SecretManager
        sm = SecretManager()
        sm.set_secret(self.token_key, json.dumps(token_data))
    
    def _load_token(self) -> Optional[Credentials]:
        """Load and deserialize OAuth2 token from unified secrets vault."""
        from atlas.security.secrets import SecretManager
        sm = SecretManager()
        raw_json = sm.get_secret(self.token_key)
        
        if not raw_json:
            return None
        
        try:
            token_data = json.loads(raw_json)
            return Credentials(
                token=token_data['token'],
                refresh_token=token_data['refresh_token'],
                token_uri=token_data['token_uri'],
                client_id=token_data['client_id'],
                client_secret=token_data['client_secret'],
                scopes=token_data['scopes'],
            )
        except Exception:
            return None
    
    def _resolve_credentials_path(self, credentials_path: Optional[str] = None) -> str:
        """Resolve the OAuth2 credentials JSON file path.
        
        Tries: explicit path → primary api key → fallbacks → error.
        """
        if credentials_path:
            if not os.path.exists(credentials_path):
                raise ValueError(f"{self.service_name} credentials file not found: {credentials_path}")
            return credentials_path
        
        from atlas.security import get_api_key
        
        # Try primary key
        resolved = get_api_key(self.api_key_name)
        
        # Try fallbacks
        if not resolved:
            for fallback in self.fallback_api_keys:
                resolved = get_api_key(fallback)
                if resolved:
                    break
        
        if not resolved:
            fallback_hint = ""
            if self.fallback_api_keys:
                fallback_hint = (
                    f"  2. Or reuse an existing credential (if {self.service_name} API is enabled)\n"
                )
            raise ValueError(
                f"{self.service_name} credentials not configured. Please either:\n"
                f"  1. Store credentials path: atlas secrets set {self.api_key_name}\n"
                f"{fallback_hint}"
                f"Get OAuth2 credentials at https://console.cloud.google.com/apis/credentials"
            )
        
        if not os.path.exists(resolved):
            raise ValueError(f"{self.service_name} credentials file not found: {resolved}")
        
        return resolved
    
    def get_service(
        self,
        api_name: str,
        api_version: str,
        credentials_path: Optional[str] = None,
    ):
        """Get an authenticated Google API service.
        
        Args:
            api_name: Google API name (e.g. 'gmail', 'calendar', 'drive', 'tasks').
            api_version: API version (e.g. 'v1', 'v3').
            credentials_path: Optional explicit path to credentials JSON.
        
        Returns:
            Google API service resource.
        """
        creds_path = self._resolve_credentials_path(credentials_path)
        
        # Load existing token or authenticate
        creds = self._load_token()
        
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    from google.auth.exceptions import RefreshError
                    creds.refresh(Request())
                except RefreshError:
                    from atlas.security.secrets import SecretManager
                    sm = SecretManager()
                    sm.delete_secret(self.token_key)
                    raise ValueError(
                        f"Credentials for {self.service_name} expired or revoked. "
                        f"Please run 'atlas secrets renew {self.api_key_name}' to re-authenticate."
                    )
            else:
                flow = InstalledAppFlow.from_client_secrets_file(creds_path, self.scopes)
                creds = flow.run_local_server(port=0)
            
            self._save_token(creds)
        
        return build(api_name, api_version, credentials=creds)


# Pre-configured auth instances for each Google service
GMAIL_AUTH = GoogleServiceAuth(
    service_name="Gmail",
    scopes=[
        'https://www.googleapis.com/auth/gmail.readonly',
        'https://www.googleapis.com/auth/gmail.send',
        'https://www.googleapis.com/auth/gmail.modify',
    ],
    token_filename="gmail_token.json",
    api_key_name="gmail",
)

CALENDAR_AUTH = GoogleServiceAuth(
    service_name="Calendar",
    scopes=[
        'https://www.googleapis.com/auth/calendar.readonly',
        'https://www.googleapis.com/auth/calendar.events',
    ],
    token_filename="calendar_token.json",
    api_key_name="calendar",
    fallback_api_keys=["gmail"],
)

DRIVE_AUTH = GoogleServiceAuth(
    service_name="Google Drive",
    scopes=[
        'https://www.googleapis.com/auth/drive',
        'https://www.googleapis.com/auth/drive.file',
    ],
    token_filename="drive_token.json",
    api_key_name="google_drive",
)

TASKS_AUTH = GoogleServiceAuth(
    service_name="Google Tasks",
    scopes=[
        'https://www.googleapis.com/auth/tasks',
    ],
    token_filename="tasks_token.json",
    api_key_name="google_tasks",
    fallback_api_keys=["calendar", "gmail"],
)
