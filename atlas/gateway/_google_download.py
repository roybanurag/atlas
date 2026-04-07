"""Gateway-side helpers for Drive binary transfers.

These functions run inside the gateway server process where google_auth is
available. They are the only place binary Google Drive I/O happens, keeping
all credential access server-side.
"""

import io
from pathlib import Path
from typing import Optional


def stream_file_via_gateway(file_id: str, mime_type: str, dest: Path) -> int:
    """Download a Drive file to disk using gateway-managed credentials.

    Args:
        file_id: Google Drive file ID.
        mime_type: MIME type of the file (determines export vs download path).
        dest: Local path to write the file to.

    Returns:
        File size in bytes.
    """
    from googleapiclient.http import MediaIoBaseDownload
    from atlas.tools.google_auth import DRIVE_AUTH

    svc = DRIVE_AUTH.get_service("drive", "v3")

    GOOGLE_EXPORT_TYPES = {
        "application/vnd.google-apps.document":
            "application/pdf",
        "application/vnd.google-apps.spreadsheet":
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.google-apps.presentation":
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }

    if mime_type.startswith("application/vnd.google-apps"):
        export_mime = GOOGLE_EXPORT_TYPES.get(mime_type)
        if not export_mime:
            raise ValueError(f"Cannot download Google Workspace file type: {mime_type}")
        request = svc.files().export_media(fileId=file_id, mimeType=export_mime)
    else:
        request = svc.files().get_media(fileId=file_id)

    dest.parent.mkdir(parents=True, exist_ok=True)
    with io.FileIO(dest, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()

    return dest.stat().st_size


def upload_file_via_gateway(
    local: Path,
    display_name: str,
    parent_folder_id: Optional[str] = None,
) -> dict:
    """Upload a local file to Drive using gateway-managed credentials.

    Args:
        local: Path to the local file.
        display_name: Name to give the file in Drive.
        parent_folder_id: Optional parent folder ID.

    Returns:
        Drive file metadata dict with id, name, size, webViewLink.
    """
    from googleapiclient.http import MediaFileUpload
    from atlas.tools.google_auth import DRIVE_AUTH

    svc = DRIVE_AUTH.get_service("drive", "v3")
    meta: dict = {"name": display_name}
    if parent_folder_id:
        meta["parents"] = [parent_folder_id]

    media = MediaFileUpload(str(local), resumable=True)
    result = svc.files().create(
        body=meta,
        media_body=media,
        fields="id, name, webViewLink, size",
    ).execute()
    return result
