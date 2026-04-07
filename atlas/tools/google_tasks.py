"""Google Tasks tools — all API calls route through the Atlas Gateway proxy.

No credentials, OAuth tokens, or googleapiclient objects are imported here.
The gateway handles authentication and permission enforcement.
"""

from datetime import datetime, timedelta
from typing import Optional

from langchain_core.tools import tool

from atlas.tools.google_proxy import google_call


def _parse_due_date(date_str: str) -> str:
    """Parse a due date string into RFC 3339 format for Tasks API.

    Args:
        date_str: Date string — 'today', 'tomorrow', or YYYY-MM-DD.

    Returns:
        RFC 3339 date string (e.g., '2026-02-20T00:00:00.000Z').
    """
    date_lower = date_str.lower().strip()
    if date_lower == "today":
        target = datetime.now()
    elif date_lower == "tomorrow":
        target = datetime.now() + timedelta(days=1)
    else:
        try:
            target = datetime.fromisoformat(date_str)
        except ValueError:
            raise ValueError(
                f"Invalid date format: {date_str}. Use 'today', 'tomorrow', or YYYY-MM-DD."
            )
    return target.strftime("%Y-%m-%dT00:00:00.000Z")


def create_google_tasks_tools():
    """Create Google Tasks LangChain tools.

    Returns:
        List of LangChain tools for Google Tasks.
    """

    @tool
    def list_task_lists() -> str:
        """List all Google Task lists.

        Returns:
            Formatted list of task list names and IDs.
        """
        try:
            result = google_call(
                service="tasks",
                resource_path="tasklists",
                method="list",
                params={"maxResults": 100},
            )
            task_lists = result.get("items", [])
            if not task_lists:
                return "No task lists found."
            lines = ["**Your Task Lists:**\n"]
            for tl in task_lists:
                lines.append(f"- **{tl.get('title', 'Untitled')}** (ID: `{tl['id']}`)")
            return "\n".join(lines)
        except (ValueError, PermissionError) as exc:
            return f"Tasks error: {exc}"
        except Exception as exc:
            return f"Error listing task lists: {exc}"

    @tool
    def list_tasks(
        task_list_id: str = "@default",
        show_completed: bool = False,
    ) -> str:
        """List tasks from a Google Task list.

        Use this when the user wants to see their tasks, to-dos, or things
        they need to do. Shows incomplete tasks by default.

        Args:
            task_list_id: Task list ID. Use '@default' for the default list.
                Use list_task_lists to find other list IDs.
            show_completed: If True, also show completed tasks.

        Returns:
            Formatted list of tasks with titles, due dates, and status.
        """
        try:
            result = google_call(
                service="tasks",
                resource_path="tasks",
                method="list",
                params={
                    "tasklist": task_list_id,
                    "maxResults": 100,
                    "showCompleted": show_completed,
                    "showHidden": False,
                },
            )
            tasks = result.get("items", [])
            if not tasks:
                return "No tasks found. 🎉"

            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            overdue, due_today, upcoming, no_date, completed_list = [], [], [], [], []

            for t in tasks:
                title = t.get("title", "").strip()
                if not title:
                    continue
                if t.get("status") == "completed":
                    completed_list.append(t)
                    continue
                due = t.get("due", "")
                if due:
                    due_date = due[:10]
                    if due_date < today_str:
                        overdue.append(t)
                    elif due_date == today_str:
                        due_today.append(t)
                    else:
                        upcoming.append(t)
                else:
                    no_date.append(t)

            lines = ["**Your Tasks:**\n"]
            if overdue:
                lines.append(f"🔴 **Overdue** ({len(overdue)})")
                for t in overdue:
                    lines.append(f"- {t['title']} *(due {t.get('due', '')[:10]})*")
                lines.append("")
            if due_today:
                lines.append(f"📋 **Due Today** ({len(due_today)})")
                for t in due_today:
                    lines.append(f"- {t['title']}")
                lines.append("")
            if upcoming:
                lines.append(f"📅 **Upcoming** ({len(upcoming)})")
                for t in upcoming:
                    lines.append(f"- {t['title']} *(due {t.get('due', '')[:10]})*")
                lines.append("")
            if no_date:
                lines.append(f"📝 **No Due Date** ({len(no_date)})")
                for t in no_date:
                    lines.append(f"- {t['title']}")
                lines.append("")
            if completed_list and show_completed:
                lines.append(f"✅ **Completed** ({len(completed_list)})")
                for t in completed_list:
                    lines.append(f"- ~~{t['title']}~~")
                lines.append("")
            return "\n".join(lines)
        except (ValueError, PermissionError) as exc:
            return f"Tasks error: {exc}"
        except Exception as exc:
            return f"Error listing tasks: {exc}"

    @tool
    def create_task(
        title: str,
        notes: str = "",
        due_date: str = "",
        task_list_id: str = "@default",
    ) -> str:
        """Create a new task in Google Tasks.

        Args:
            title: The task title/description.
            notes: Optional additional notes or details.
            due_date: Optional due date. Accepts 'today', 'tomorrow', or
                YYYY-MM-DD. Leave empty for no due date.
            task_list_id: Task list to add to. Use '@default' for the default list.

        Returns:
            Confirmation message with the created task details.
        """
        try:
            body: dict = {"title": title, "status": "needsAction"}
            if notes:
                body["notes"] = notes
            if due_date:
                body["due"] = _parse_due_date(due_date)

            result = google_call(
                service="tasks",
                resource_path="tasks",
                method="insert",
                params={"tasklist": task_list_id},
                body=body,
            )
            task_id = result.get("id", "unknown")
            response = "✅ **Task created!**\n\n"
            response += f"**Title:** {title}\n"
            if notes:
                response += f"**Notes:** {notes}\n"
            if due_date:
                response += f"**Due:** {due_date}\n"
            response += f"**ID:** `{task_id}`"
            return response
        except ValueError as exc:
            return f"Error: {exc}"
        except PermissionError as exc:
            return f"Permission denied: {exc}"
        except Exception as exc:
            return f"Error creating task: {exc}"

    return [list_task_lists, list_tasks, create_task]
