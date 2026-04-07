"""Gmail tools — all API calls route through the Atlas Gateway proxy.

No credentials, OAuth tokens, or googleapiclient objects are imported or
instantiated here. The gateway handles authentication and permission enforcement.
"""

import base64
from email.mime.text import MIMEText
from typing import Optional

from langchain_core.tools import tool

from atlas.tools.google_proxy import google_call


def create_gmail_tools(credentials_path: Optional[str] = None):
    """Create Gmail tools.

    The ``credentials_path`` argument is accepted for API compatibility only
    and is ignored — credentials are managed exclusively by the gateway.

    Returns:
        List of LangChain tools for Gmail operations.
    """

    @tool
    def search_emails(query: str, max_results: int = 10) -> str:
        """Search emails in Gmail.

        Use this tool to find emails matching specific criteria. The query uses
        Gmail's search syntax (same as the Gmail search box).

        Common query examples:
        - "from:sender@example.com" — Emails from a specific sender
        - "subject:invoice" — Emails with "invoice" in subject
        - "is:unread" — Unread emails
        - "has:attachment" — Emails with attachments
        - "after:2024/01/01" — Emails after a date
        - "label:important" — Emails with a specific label

        Args:
            query: Gmail search query.
            max_results: Maximum number of results to return (default: 10).

        Returns:
            Formatted string with email summaries.
        """
        try:
            results = google_call(
                service="gmail",
                resource_path="users.messages",
                method="list",
                params={"userId": "me", "q": query, "maxResults": max_results},
            )
            messages = results.get("messages", [])
            if not messages:
                return f"No emails found matching query: {query}"

            summaries = []
            for msg in messages:
                meta = google_call(
                    service="gmail",
                    resource_path="users.messages",
                    method="get",
                    params={
                        "userId": "me",
                        "id": msg["id"],
                        "format": "metadata",
                        "metadataHeaders": ["From", "Subject", "Date"],
                    },
                )
                headers = {
                    h["name"]: h["value"]
                    for h in meta.get("payload", {}).get("headers", [])
                }
                summaries.append(
                    f"**ID**: {msg['id']}\n"
                    f"**From**: {headers.get('From', 'Unknown')}\n"
                    f"**Subject**: {headers.get('Subject', 'No subject')}\n"
                    f"**Date**: {headers.get('Date', 'Unknown')}\n"
                )
            return f"Found {len(messages)} email(s):\n\n" + "\n---\n".join(summaries)
        except ValueError as exc:
            return f"Gmail not configured: {exc}"
        except PermissionError as exc:
            return f"Permission denied: {exc}"
        except Exception as exc:
            return f"Error searching emails: {exc}"

    @tool
    def read_email(email_id: str) -> str:
        """Read the full content of a specific email.

        Use this tool to read the complete content of an email when you have its
        ID (typically from search_emails results).

        Args:
            email_id: The Gmail message ID.

        Returns:
            Full email content including headers and body.
        """
        try:
            msg = google_call(
                service="gmail",
                resource_path="users.messages",
                method="get",
                params={"userId": "me", "id": email_id, "format": "full"},
            )
            headers = {
                h["name"]: h["value"]
                for h in msg.get("payload", {}).get("headers", [])
            }
            body = ""
            payload = msg.get("payload", {})
            if "parts" in payload:
                for part in payload["parts"]:
                    if part.get("mimeType") == "text/plain":
                        data = part.get("body", {}).get("data")
                        if data:
                            body = base64.urlsafe_b64decode(data).decode("utf-8")
                            break
            elif payload.get("body", {}).get("data"):
                body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8")

            return (
                f"**From**: {headers.get('From', 'Unknown')}\n"
                f"**To**: {headers.get('To', 'Unknown')}\n"
                f"**Subject**: {headers.get('Subject', 'No subject')}\n"
                f"**Date**: {headers.get('Date', 'Unknown')}\n\n"
                f"**Body**:\n{body or '(No text content)'}"
            )
        except ValueError as exc:
            return f"Gmail not configured: {exc}"
        except PermissionError as exc:
            return f"Permission denied: {exc}"
        except Exception as exc:
            return f"Error reading email: {exc}"

    @tool
    def send_email(to: str, subject: str, body: str) -> str:
        """Send an email via Gmail.

        Use this tool to send emails from the user's Gmail account.

        Args:
            to: Recipient email address.
            subject: Email subject line.
            body: Email body text.

        Returns:
            Confirmation message with sent email ID.
        """
        try:
            message = MIMEText(body)
            message["to"] = to
            message["subject"] = subject
            raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

            sent = google_call(
                service="gmail",
                resource_path="users.messages",
                method="send",
                params={"userId": "me"},
                body={"raw": raw},
            )
            return f"✓ Email sent successfully to {to}\nMessage ID: {sent['id']}"
        except ValueError as exc:
            return f"Gmail not configured: {exc}"
        except PermissionError as exc:
            return f"Permission denied: {exc}"
        except Exception as exc:
            return f"Error sending email: {exc}"

    @tool
    def list_recent_emails(max_results: int = 10) -> str:
        """List recent emails from Gmail inbox.

        Use this tool to get a quick overview of recent emails in the inbox.

        Args:
            max_results: Maximum number of emails to return (default: 10).

        Returns:
            Formatted list of recent emails.
        """
        try:
            results = google_call(
                service="gmail",
                resource_path="users.messages",
                method="list",
                params={"userId": "me", "labelIds": ["INBOX"], "maxResults": max_results},
            )
            messages = results.get("messages", [])
            if not messages:
                return "No emails found in inbox"

            summaries = []
            for msg in messages:
                meta = google_call(
                    service="gmail",
                    resource_path="users.messages",
                    method="get",
                    params={
                        "userId": "me",
                        "id": msg["id"],
                        "format": "metadata",
                        "metadataHeaders": ["From", "Subject", "Date"],
                    },
                )
                headers = {
                    h["name"]: h["value"]
                    for h in meta.get("payload", {}).get("headers", [])
                }
                snippet = meta.get("snippet", "")
                summaries.append(
                    f"**ID**: {msg['id']}\n"
                    f"**From**: {headers.get('From', 'Unknown')}\n"
                    f"**Subject**: {headers.get('Subject', 'No subject')}\n"
                    f"**Preview**: {snippet[:100]}...\n"
                )
            return f"Recent {len(messages)} email(s):\n\n" + "\n---\n".join(summaries)
        except ValueError as exc:
            return f"Gmail not configured: {exc}"
        except PermissionError as exc:
            return f"Permission denied: {exc}"
        except Exception as exc:
            return f"Error listing emails: {exc}"

    return [search_emails, read_email, send_email, list_recent_emails]
