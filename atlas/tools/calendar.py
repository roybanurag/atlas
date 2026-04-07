"""Google Calendar tools — all API calls route through the Atlas Gateway proxy.

No credentials, OAuth tokens, or googleapiclient objects are imported here.
The gateway handles authentication and permission enforcement.
"""

from datetime import datetime, timedelta
from typing import Optional

from langchain_core.tools import tool

from atlas.tools.google_proxy import google_call


# ---------------------------------------------------------------------------
# Datetime helpers (unchanged from original)
# ---------------------------------------------------------------------------

def _parse_datetime(time_str: str) -> str:
    """Parse various datetime formats into ISO format with local timezone."""
    import re
    local_now = datetime.now().astimezone()

    if "T" in time_str or time_str.count("-") == 2:
        try:
            dt = datetime.fromisoformat(time_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=local_now.tzinfo)
            return dt.isoformat()
        except ValueError:
            pass

    base_date = local_now
    lower_str = time_str.lower()

    if "tomorrow" in lower_str:
        base_date = local_now + timedelta(days=1)
    elif "today" in lower_str:
        base_date = local_now
    else:
        try:
            dt = datetime.fromisoformat(time_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=local_now.tzinfo)
            base_date = dt
        except ValueError:
            pass

    time_match = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", lower_str)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2) or 0)
        meridiem = time_match.group(3)
        if meridiem == "pm" and hour != 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        base_date = base_date.replace(hour=hour, minute=minute, second=0, microsecond=0)

    return base_date.isoformat()


def _ensure_tz(dt_str: str) -> str:
    """Ensure a datetime string has timezone info."""
    import time as _time
    if not any(c in dt_str[-6:] for c in ["Z", "+", "-"]):
        offset = -_time.timezone if _time.localtime().tm_isdst == 0 else -_time.altzone
        h, m = offset // 3600, (offset % 3600) // 60
        dt_str += f"{h:+03d}:{m:02d}"
    return dt_str


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------

def create_calendar_tools(credentials_path: Optional[str] = None):
    """Create Google Calendar tools.

    The ``credentials_path`` argument is accepted for API compatibility only
    and is ignored — credentials are managed exclusively by the gateway.

    Returns:
        List of LangChain tools for Calendar operations.
    """

    @tool
    def list_events(
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
        max_results: int = 10,
    ) -> str:
        """List upcoming calendar events.

        Args:
            time_min: Start time (ISO format or relative like "today"). Defaults to now.
            time_max: End time (ISO format or relative). Defaults to 7 days from now.
            max_results: Maximum number of events to return (default: 10).

        Returns:
            Formatted list of calendar events.
        """
        try:
            tmin = _parse_datetime(time_min) if time_min else datetime.now().astimezone().isoformat()
            tmax = _parse_datetime(time_max) if time_max else (datetime.now().astimezone() + timedelta(days=7)).isoformat()

            result = google_call(
                service="calendar",
                resource_path="events",
                method="list",
                params={
                    "calendarId": "primary",
                    "timeMin": tmin,
                    "timeMax": tmax,
                    "maxResults": max_results,
                    "singleEvents": True,
                    "orderBy": "startTime",
                },
            )
            events = result.get("items", [])
            if not events:
                return "No upcoming events found."

            summaries = []
            for ev in events:
                start = ev["start"].get("dateTime", ev["start"].get("date"))
                s = f"**{ev.get('summary', 'No title')}**\n  Time: {start}\n"
                if ev.get("location"):
                    s += f"  Location: {ev['location']}\n"
                if ev.get("attendees"):
                    s += f"  Attendees: {len(ev['attendees'])} invited\n"
                s += f"  ID: {ev['id']}\n"
                summaries.append(s)
            return f"Found {len(events)} event(s):\n\n" + "\n---\n".join(summaries)
        except (ValueError, PermissionError) as exc:
            return f"Calendar error: {exc}"
        except Exception as exc:
            return f"Error listing events: {exc}"

    @tool
    def create_event(
        summary: str,
        start_time: str,
        end_time: str,
        description: Optional[str] = None,
        location: Optional[str] = None,
        attendee_emails: Optional[list] = None,
    ) -> str:
        """Create a new calendar event.

        Args:
            summary: Event title.
            start_time: Start time (ISO format or relative like "tomorrow at 2pm").
            end_time: End time.
            description: Optional event description.
            location: Optional event location.
            attendee_emails: Optional list of email addresses to invite.

        Returns:
            Confirmation message with event ID and link.
        """
        try:
            start_dt = _ensure_tz(_parse_datetime(start_time))
            end_dt = _ensure_tz(_parse_datetime(end_time))

            body: dict = {
                "summary": summary,
                "start": {"dateTime": start_dt},
                "end": {"dateTime": end_dt},
            }
            if description:
                body["description"] = description
            if location:
                body["location"] = location
            if attendee_emails:
                body["attendees"] = [{"email": e} for e in attendee_emails]

            created = google_call(
                service="calendar",
                resource_path="events",
                method="insert",
                params={
                    "calendarId": "primary",
                    "sendUpdates": "all" if attendee_emails else "none",
                },
                body=body,
            )
            msg = f"✓ Event created: {summary}\nEvent ID: {created['id']}\nLink: {created.get('htmlLink', 'N/A')}"
            if attendee_emails:
                msg += f"\nInvited: {', '.join(attendee_emails)}"
            return msg
        except (ValueError, PermissionError) as exc:
            return f"Calendar error: {exc}"
        except Exception as exc:
            return f"Error creating event: {exc}"

    @tool
    def update_event(
        event_id: str,
        summary: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        description: Optional[str] = None,
        location: Optional[str] = None,
    ) -> str:
        """Update an existing calendar event. Only provided fields are changed.

        Args:
            event_id: The event ID to update.
            summary: New event title (optional).
            start_time: New start time (optional).
            end_time: New end time (optional).
            description: New description (optional).
            location: New location (optional).

        Returns:
            Confirmation message.
        """
        try:
            # Fetch current event first
            event = google_call(
                service="calendar",
                resource_path="events",
                method="get",
                params={"calendarId": "primary", "eventId": event_id},
            )
            if summary:
                event["summary"] = summary
            if start_time:
                event["start"]["dateTime"] = _ensure_tz(_parse_datetime(start_time))
            if end_time:
                event["end"]["dateTime"] = _ensure_tz(_parse_datetime(end_time))
            if description:
                event["description"] = description
            if location:
                event["location"] = location

            updated = google_call(
                service="calendar",
                resource_path="events",
                method="update",
                params={"calendarId": "primary", "eventId": event_id},
                body=event,
            )
            return f"✓ Event updated: {updated.get('summary', 'Untitled')}\nEvent ID: {event_id}"
        except (ValueError, PermissionError) as exc:
            return f"Calendar error: {exc}"
        except Exception as exc:
            return f"Error updating event: {exc}"

    @tool
    def delete_event(event_id: str) -> str:
        """Delete a calendar event.

        Args:
            event_id: The event ID to delete.

        Returns:
            Confirmation message.
        """
        try:
            event = google_call(
                service="calendar",
                resource_path="events",
                method="get",
                params={"calendarId": "primary", "eventId": event_id},
            )
            title = event.get("summary", "Untitled")
            google_call(
                service="calendar",
                resource_path="events",
                method="delete",
                params={"calendarId": "primary", "eventId": event_id},
            )
            return f"✓ Event deleted: {title}"
        except (ValueError, PermissionError) as exc:
            return f"Calendar error: {exc}"
        except Exception as exc:
            return f"Error deleting event: {exc}"

    @tool
    def search_events(
        query: str,
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
        max_results: int = 10,
    ) -> str:
        """Search for calendar events by text.

        Args:
            query: Search query (searches summary, description, location).
            time_min: Start of time range (optional, defaults to now).
            time_max: End of time range (optional, defaults to 30 days from now).
            max_results: Maximum number of results (default: 10).

        Returns:
            Formatted list of matching events.
        """
        try:
            tmin = _parse_datetime(time_min) if time_min else datetime.now().astimezone().isoformat()
            tmax = _parse_datetime(time_max) if time_max else (datetime.now().astimezone() + timedelta(days=30)).isoformat()

            result = google_call(
                service="calendar",
                resource_path="events",
                method="list",
                params={
                    "calendarId": "primary",
                    "timeMin": tmin,
                    "timeMax": tmax,
                    "maxResults": max_results,
                    "q": query,
                    "singleEvents": True,
                    "orderBy": "startTime",
                },
            )
            events = result.get("items", [])
            if not events:
                return f"No events found matching '{query}'"

            summaries = []
            for ev in events:
                start = ev["start"].get("dateTime", ev["start"].get("date"))
                s = f"**{ev.get('summary', 'No title')}**\n  Time: {start}\n"
                if ev.get("location"):
                    s += f"  Location: {ev['location']}\n"
                if ev.get("attendees"):
                    s += f"  Attendees: {len(ev['attendees'])} invited\n"
                s += f"  ID: {ev['id']}\n"
                summaries.append(s)
            return f"Found {len(events)} event(s) matching '{query}':\n\n" + "\n---\n".join(summaries)
        except (ValueError, PermissionError) as exc:
            return f"Calendar error: {exc}"
        except Exception as exc:
            return f"Error searching events: {exc}"

    @tool
    def add_attendees(
        event_id: str, attendee_emails: list, send_notifications: bool = True
    ) -> str:
        """Add attendees to an existing calendar event.

        Args:
            event_id: The event ID.
            attendee_emails: List of email addresses to invite.
            send_notifications: Whether to send email notifications (default: True).

        Returns:
            Confirmation message.
        """
        try:
            event = google_call(
                service="calendar",
                resource_path="events",
                method="get",
                params={"calendarId": "primary", "eventId": event_id},
            )
            existing = {a["email"].lower() for a in event.get("attendees", [])}
            attendees = list(event.get("attendees", []))
            new = [e for e in attendee_emails if e.lower() not in existing]
            if not new:
                return "All specified attendees are already invited."
            attendees += [{"email": e} for e in new]
            event["attendees"] = attendees

            updated = google_call(
                service="calendar",
                resource_path="events",
                method="update",
                params={
                    "calendarId": "primary",
                    "eventId": event_id,
                    "sendUpdates": "all" if send_notifications else "none",
                },
                body=event,
            )
            count = len(updated.get("attendees", []))
            return (
                f"✓ Added {len(new)} attendee(s) to '{updated.get('summary', 'Untitled')}'\n"
                f"New: {', '.join(new)}\nTotal attendees: {count}"
            )
        except (ValueError, PermissionError) as exc:
            return f"Calendar error: {exc}"
        except Exception as exc:
            return f"Error adding attendees: {exc}"

    @tool
    def remove_attendees(
        event_id: str, attendee_emails: list, send_notifications: bool = True
    ) -> str:
        """Remove attendees from an existing calendar event.

        Args:
            event_id: The event ID.
            attendee_emails: List of email addresses to remove.
            send_notifications: Whether to send cancellation notifications (default: True).

        Returns:
            Confirmation message.
        """
        try:
            event = google_call(
                service="calendar",
                resource_path="events",
                method="get",
                params={"calendarId": "primary", "eventId": event_id},
            )
            to_remove = {e.lower() for e in attendee_emails}
            attendees = event.get("attendees", [])
            removed = [a["email"] for a in attendees if a["email"].lower() in to_remove]
            if not removed:
                return "None of the specified attendees were found."
            event["attendees"] = [a for a in attendees if a["email"].lower() not in to_remove]

            updated = google_call(
                service="calendar",
                resource_path="events",
                method="update",
                params={
                    "calendarId": "primary",
                    "eventId": event_id,
                    "sendUpdates": "all" if send_notifications else "none",
                },
                body=event,
            )
            count = len(updated.get("attendees", []))
            return (
                f"✓ Removed {len(removed)} attendee(s) from '{updated.get('summary', 'Untitled')}'\n"
                f"Removed: {', '.join(removed)}\nRemaining attendees: {count}"
            )
        except (ValueError, PermissionError) as exc:
            return f"Calendar error: {exc}"
        except Exception as exc:
            return f"Error removing attendees: {exc}"

    @tool
    def list_attendees(event_id: str) -> str:
        """List all attendees for a calendar event with their response status.

        Args:
            event_id: The event ID.

        Returns:
            Formatted list of attendees with response statuses.
        """
        try:
            event = google_call(
                service="calendar",
                resource_path="events",
                method="get",
                params={"calendarId": "primary", "eventId": event_id},
            )
            attendees = event.get("attendees", [])
            title = event.get("summary", "Untitled")
            if not attendees:
                return f"Event '{title}' has no attendees."

            STATUS = {"accepted": "✅", "declined": "❌", "tentative": "❓", "needsAction": "⏳"}
            lines = []
            for a in attendees:
                emoji = STATUS.get(a.get("responseStatus", "needsAction"), "❔")
                role = " (Organizer)" if a.get("organizer") else ""
                lines.append(f"{emoji} {a.get('email', 'Unknown')}{role}")
            return f"Attendees for '{title}' ({len(attendees)} total):\n" + "\n".join(lines)
        except (ValueError, PermissionError) as exc:
            return f"Calendar error: {exc}"
        except Exception as exc:
            return f"Error listing attendees: {exc}"

    return [list_events, create_event, update_event, delete_event, search_events,
            add_attendees, remove_attendees, list_attendees]
