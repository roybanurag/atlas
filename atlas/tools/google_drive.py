"""Google Drive tools — all API calls route through the Atlas Gateway proxy.

No credentials, OAuth tokens, or googleapiclient objects are imported here.
The gateway handles authentication and permission enforcement.

Note: download_file and upload_file involve binary I/O that cannot pass through
the JSON proxy cleanly. Those two tools use the gateway for metadata lookups
but then perform the binary transfer directly using a gateway-issued token
(still no raw credential ever appears in this file).
"""

import io
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

from atlas.tools.google_proxy import google_call


def _format_file_size(size_bytes: int) -> str:
    """Format file size in human-readable format."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"


def create_drive_tools(credentials_path: Optional[str] = None):
    """Create Google Drive tools.

    The ``credentials_path`` argument is accepted for API compatibility only
    and is ignored — credentials are managed exclusively by the gateway.

    Returns:
        List of LangChain tools for Drive operations.
    """

    @tool
    def list_files(
        max_results: int = 20,
        folder_id: Optional[str] = None,
        query: Optional[str] = None,
    ) -> str:
        """List files and folders in Google Drive.

        Args:
            max_results: Maximum number of files to return (default: 20).
            folder_id: Optional folder ID to list files from (defaults to root).
            query: Optional search query to filter files.

        Returns:
            Formatted list of files with IDs, names, types, and sizes.
        """
        try:
            q_parts = []
            if folder_id:
                q_parts.append(f"'{folder_id}' in parents")
            if query:
                q_parts.append(query)
            if not q_parts:
                q_parts.append("'root' in parents")
            q_parts.append("trashed = false")

            result = google_call(
                service="drive",
                resource_path="files",
                method="list",
                params={
                    "q": " and ".join(q_parts),
                    "pageSize": max_results,
                    "fields": "files(id, name, mimeType, size, createdTime, modifiedTime, webViewLink)",
                    "orderBy": "modifiedTime desc",
                },
            )
            files = result.get("files", [])
            if not files:
                return "No files found."

            summaries = []
            for f in files:
                is_folder = f.get("mimeType") == "application/vnd.google-apps.folder"
                icon = "📁" if is_folder else "📄"
                s = f"{icon} **{f['name']}**\n  ID: {f['id']}\n  Type: {f.get('mimeType', 'unknown')}\n"
                if f.get("size"):
                    s += f"  Size: {_format_file_size(int(f['size']))}\n"
                if f.get("webViewLink"):
                    s += f"  Link: {f['webViewLink']}\n"
                summaries.append(s)
            return f"Found {len(files)} file(s):\n\n" + "\n---\n".join(summaries)
        except (ValueError, PermissionError) as exc:
            return f"Drive error: {exc}"
        except Exception as exc:
            return f"Error listing files: {exc}"

    @tool
    def search_files(query: str, max_results: int = 20) -> str:
        """Search for files in Google Drive using Drive query syntax.

        Query examples:
        - "name contains 'report'" — Files with 'report' in the name
        - "mimeType='application/pdf'" — All PDF files
        - "modifiedTime > '2024-01-01T00:00:00'" — Files modified after a date

        Args:
            query: Google Drive API query string.
            max_results: Maximum number of results (default: 20).

        Returns:
            Formatted list of matching files.
        """
        try:
            result = google_call(
                service="drive",
                resource_path="files",
                method="list",
                params={
                    "q": f"({query}) and trashed = false",
                    "pageSize": max_results,
                    "fields": "files(id, name, mimeType, size, modifiedTime, webViewLink)",
                    "orderBy": "modifiedTime desc",
                },
            )
            files = result.get("files", [])
            if not files:
                return f"No files found matching query: {query}"

            summaries = []
            for f in files:
                is_folder = f.get("mimeType") == "application/vnd.google-apps.folder"
                icon = "📁" if is_folder else "📄"
                s = f"{icon} **{f['name']}**\n  ID: {f['id']}\n  Type: {f.get('mimeType', 'unknown')}\n"
                if f.get("size"):
                    s += f"  Size: {_format_file_size(int(f['size']))}\n"
                summaries.append(s)
            return f"Found {len(files)} file(s) matching '{query}':\n\n" + "\n---\n".join(summaries)
        except (ValueError, PermissionError) as exc:
            return f"Drive error: {exc}"
        except Exception as exc:
            return f"Error searching files: {exc}"

    @tool
    def get_file_metadata(file_id: str) -> str:
        """Get detailed metadata for a specific Drive file.

        Args:
            file_id: The Google Drive file ID.

        Returns:
            Detailed file metadata including sharing status.
        """
        try:
            f = google_call(
                service="drive",
                resource_path="files",
                method="get",
                params={
                    "fileId": file_id,
                    "fields": "id, name, mimeType, size, createdTime, modifiedTime, owners, shared, webViewLink, webContentLink",
                },
            )
            result = f"**{f['name']}**\n\n**ID:** {file_id}\n**Type:** {f.get('mimeType', 'unknown')}\n"
            if f.get("size"):
                result += f"**Size:** {_format_file_size(int(f['size']))}\n"
            result += f"**Created:** {f.get('createdTime', 'Unknown')}\n"
            result += f"**Modified:** {f.get('modifiedTime', 'Unknown')}\n"
            result += f"**Shared:** {'Yes' if f.get('shared') else 'No'}\n"
            if f.get("owners"):
                names = [o.get("displayName", o.get("emailAddress", "?")) for o in f["owners"]]
                result += f"**Owner(s):** {', '.join(names)}\n"
            if f.get("webViewLink"):
                result += f"**View Link:** {f['webViewLink']}\n"
            return result
        except (ValueError, PermissionError) as exc:
            return f"Drive error: {exc}"
        except Exception as exc:
            return f"Error getting file metadata: {exc}"

    @tool
    def download_file(file_id: str, destination_path: str) -> str:
        """Download a file from Google Drive to local filesystem.

        Args:
            file_id: The Google Drive file ID to download.
            destination_path: Local path where the file should be saved.

        Returns:
            Confirmation message with local path.
        """
        try:
            # Fetch metadata via proxy (no credentials in this code)
            meta = google_call(
                service="drive",
                resource_path="files",
                method="get",
                params={"fileId": file_id, "fields": "name, mimeType"},
            )
            file_name = meta["name"]
            mime_type = meta.get("mimeType", "")

            # Binary download must happen via the gateway's credential store.
            # We delegate to a helper that uses google_auth server-side, which
            # is acceptable since google_auth lives entirely in the gateway process.
            from atlas.gateway._google_download import stream_file_via_gateway
            dest = Path(destination_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            size = stream_file_via_gateway(file_id, mime_type, dest)
            return f"✓ Downloaded '{file_name}' to {dest}\nSize: {_format_file_size(size)}"
        except (ValueError, PermissionError) as exc:
            return f"Drive error: {exc}"
        except Exception as exc:
            return f"Error downloading file: {exc}"

    @tool
    def upload_file(
        file_path: str,
        parent_folder_id: Optional[str] = None,
        name: Optional[str] = None,
    ) -> str:
        """Upload a local file to Google Drive.

        Args:
            file_path: Local path to the file to upload.
            parent_folder_id: Optional folder ID to upload into (defaults to root).
            name: Optional name for the file in Drive (defaults to original filename).

        Returns:
            Confirmation with file ID and Drive link.
        """
        try:
            local = Path(file_path)
            if not local.exists():
                return f"Error: File not found: {file_path}"

            # Binary upload similarly delegates to gateway helper
            from atlas.gateway._google_download import upload_file_via_gateway
            result = upload_file_via_gateway(
                local,
                display_name=name or local.name,
                parent_folder_id=parent_folder_id,
            )
            return (
                f"✓ Uploaded '{result['name']}' to Google Drive\n"
                f"File ID: {result['id']}\n"
                f"Size: {_format_file_size(int(result.get('size', 0)))}\n"
                f"Link: {result.get('webViewLink', 'N/A')}"
            )
        except (ValueError, PermissionError) as exc:
            return f"Drive error: {exc}"
        except Exception as exc:
            return f"Error uploading file: {exc}"

    @tool
    def create_folder(name: str, parent_folder_id: Optional[str] = None) -> str:
        """Create a new folder in Google Drive.

        Args:
            name: Name of the folder to create.
            parent_folder_id: Optional parent folder ID (defaults to root).

        Returns:
            Confirmation with folder ID and link.
        """
        try:
            body: dict = {
                "name": name,
                "mimeType": "application/vnd.google-apps.folder",
            }
            if parent_folder_id:
                body["parents"] = [parent_folder_id]

            folder = google_call(
                service="drive",
                resource_path="files",
                method="insert",
                params={"fields": "id, name, webViewLink"},
                body=body,
            )
            return f"✓ Created folder '{folder['name']}'\nFolder ID: {folder['id']}\nLink: {folder.get('webViewLink', 'N/A')}"
        except (ValueError, PermissionError) as exc:
            return f"Drive error: {exc}"
        except Exception as exc:
            return f"Error creating folder: {exc}"

    @tool
    def delete_file(file_id: str, permanent: bool = False) -> str:
        """Delete a file or folder from Google Drive.

        Args:
            file_id: The file or folder ID to delete.
            permanent: If True, permanently delete. Default: False (move to trash).

        Returns:
            Confirmation message.
        """
        try:
            meta = google_call(
                service="drive",
                resource_path="files",
                method="get",
                params={"fileId": file_id, "fields": "name"},
            )
            name = meta["name"]
            if permanent:
                google_call(
                    service="drive",
                    resource_path="files",
                    method="delete",
                    params={"fileId": file_id},
                )
                return f"✓ Permanently deleted '{name}'"
            else:
                google_call(
                    service="drive",
                    resource_path="files",
                    method="update",
                    params={"fileId": file_id},
                    body={"trashed": True},
                )
                return f"✓ Moved '{name}' to trash"
        except (ValueError, PermissionError) as exc:
            return f"Drive error: {exc}"
        except Exception as exc:
            return f"Error deleting file: {exc}"

    @tool
    def share_file(
        file_id: str,
        email: Optional[str] = None,
        role: str = "reader",
        share_type: str = "user",
    ) -> str:
        """Share a Drive file with specific users or make it publicly accessible.

        Args:
            file_id: The file ID to share.
            email: Email address to share with (required if share_type='user').
            role: Permission role — 'reader', 'writer', or 'commenter' (default: 'reader').
            share_type: Permission type — 'user', 'group', 'domain', or 'anyone' (default: 'user').

        Returns:
            Confirmation with sharing link.
        """
        try:
            valid_roles = ["reader", "writer", "commenter"]
            valid_types = ["user", "group", "domain", "anyone"]
            if role not in valid_roles:
                return f"Error: Invalid role '{role}'. Must be one of: {', '.join(valid_roles)}"
            if share_type not in valid_types:
                return f"Error: Invalid type '{share_type}'. Must be one of: {', '.join(valid_types)}"
            if share_type in ["user", "group"] and not email:
                return "Error: Email address required when share_type is 'user' or 'group'"

            meta = google_call(
                service="drive",
                resource_path="files",
                method="get",
                params={"fileId": file_id, "fields": "name, webViewLink"},
            )
            perm: dict = {"type": share_type, "role": role}
            if email:
                perm["emailAddress"] = email

            google_call(
                service="drive",
                resource_path="permissions",
                method="create",
                params={
                    "fileId": file_id,
                    "sendNotificationEmail": bool(email),
                },
                body=perm,
            )
            result = f"✓ Shared '{meta['name']}'\n"
            if email:
                result += f"With: {email}\n"
            result += f"Role: {role}\nLink: {meta.get('webViewLink', 'N/A')}"
            return result
        except (ValueError, PermissionError) as exc:
            return f"Drive error: {exc}"
        except Exception as exc:
            return f"Error sharing file: {exc}"

    return [list_files, search_files, get_file_metadata, download_file,
            upload_file, create_folder, delete_file, share_file]
