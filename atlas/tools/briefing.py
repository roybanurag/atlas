"""Daily Briefing and Morning Summary for Atlas.

Compiles calendar events, emails, weather, news headlines, tasks, 
and notes into a personalized daily briefing to start your day informed.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

import httpx

from atlas.gateway.headers import get_gateway_headers
from atlas.tools.google_proxy import google_call


@dataclass
class BriefingSection:
    """A section of the daily briefing."""
    title: str
    emoji: str
    items: list[str] = field(default_factory=list)
    available: bool = True
    error: Optional[str] = None


@dataclass 
class DailyBriefing:
    """Complete daily briefing data."""
    date: datetime
    greeting: str
    sections: list[BriefingSection] = field(default_factory=list)
    
    def to_markdown(self) -> str:
        """Convert briefing to formatted markdown."""
        lines = [
            f"# {self.greeting}",
            f"**{self.date.strftime('%A, %B %d, %Y')}**",
            "",
        ]
        
        for section in self.sections:
            if not section.available:
                lines.append(f"### {section.emoji} {section.title}")
                lines.append(f"*{section.error or 'Not available'}*")
                lines.append("")
                continue
                
            if section.items:
                lines.append(f"### {section.emoji} {section.title}")
                for item in section.items:
                    lines.append(item)
                lines.append("")
        
        return "\n".join(lines)


class BriefingGenerator:
    """Generate personalized daily briefings."""
    
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self._state_file = self.data_dir / "briefing_state.json"
    
    def _get_greeting(self) -> str:
        """Get time-appropriate greeting."""
        hour = datetime.now().hour
        if hour < 12:
            return "☀️ Good Morning!"
        elif hour < 17:
            return "🌤️ Good Afternoon!"
        else:
            return "🌙 Good Evening!"
    
    def _load_last_briefing_time(self) -> Optional[datetime]:
        """Load the timestamp of the last briefing."""
        try:
            if self._state_file.exists():
                with open(self._state_file, 'r') as f:
                    data = json.load(f)
                return datetime.fromisoformat(data.get("last_briefing", ""))
        except Exception:
            pass
        return None
    
    def _save_briefing_time(self):
        """Save the current timestamp as the last briefing time."""
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            with open(self._state_file, 'w') as f:
                json.dump({"last_briefing": datetime.now().isoformat()}, f)
        except Exception:
            pass
    
    def _get_calendar_section(self, target_date: datetime) -> BriefingSection:
        """Get calendar events for the target date."""
        section = BriefingSection(title="Today's Schedule", emoji="📅")
        
        try:
            start_of_day = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_day = start_of_day + timedelta(days=1)
            
            events_result = google_call(
                service="calendar",
                resource_path="events",
                method="list",
                params={
                    "calendarId": "primary",
                    "timeMin": start_of_day.astimezone().isoformat(),
                    "timeMax": end_of_day.astimezone().isoformat(),
                    "maxResults": 20,
                    "singleEvents": True,
                    "orderBy": "startTime",
                },
            )
            
            events = events_result.get('items', [])
            
            if not events:
                section.items.append("*No events scheduled for today* 🎉")
            else:
                total_minutes = 0
                for event in events:
                    start = event['start'].get('dateTime', event['start'].get('date'))
                    end = event['end'].get('dateTime', event['end'].get('date'))
                    if 'T' in start and 'T' in end:
                        start_dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
                        end_dt = datetime.fromisoformat(end.replace('Z', '+00:00'))
                        total_minutes += (end_dt - start_dt).total_seconds() / 60
                
                if total_minutes > 0:
                    hours = int(total_minutes // 60)
                    mins = int(total_minutes % 60)
                    time_str = f"{hours}h {mins}m" if hours else f"{mins}m"
                    section.items.append(f"📊 **{len(events)} events** ({time_str} total)")
                    section.items.append("")
                
                for event in events:
                    start = event['start'].get('dateTime', event['start'].get('date'))
                    summary = event.get('summary', 'Untitled')
                    location = event.get('location', '')
                    attendees = event.get('attendees', [])
                    
                    if 'T' in start:
                        event_time = datetime.fromisoformat(start.replace('Z', '+00:00'))
                        time_str = event_time.strftime('%I:%M %p')
                    else:
                        time_str = "All day"
                    
                    item = f"- **{time_str}** - {summary}"
                    if location:
                        item += f" 📍 _{location}_"
                    if attendees:
                        item += f" 👥 {len(attendees)} attendees"
                    
                    section.items.append(item)
                    
        except (PermissionError, RuntimeError) as e:
            section.available = False
            section.error = "Calendar not accessible (gateway error)"
        except Exception as e:
            section.available = False
            section.error = f"Error: {str(e)[:50]}"
        
        return section
    
    def _categorize_email(self, headers: dict, labels: list) -> str:
        """Categorize an email based on headers and labels.
        
        Returns:
            'action', 'fyi', or 'notification'
        """
        sender = headers.get("From", "").lower()
        
        # Notifications: automated / noreply senders
        noreply_patterns = [
            "noreply", "no-reply", "notifications@", "mailer-daemon",
            "donotreply", "automated", "digest@", "updates@", "alert@",
        ]
        if any(p in sender for p in noreply_patterns):
            return "notification"
        
        # Gmail category labels
        if "CATEGORY_PROMOTIONS" in labels or "CATEGORY_UPDATES" in labels:
            return "notification"
        if "CATEGORY_FORUMS" in labels or "CATEGORY_SOCIAL" in labels:
            return "fyi"
        
        # Starred / important = action required
        if "STARRED" in labels or "IMPORTANT" in labels:
            return "action"
        
        # Default: action (direct email from a person)
        return "action"
    
    def _get_email_section(self) -> BriefingSection:
        """Get categorized email summary since last briefing."""
        section = BriefingSection(title="Email Summary", emoji="📧")
        
        try:
            last_briefing = self._load_last_briefing_time()
            if last_briefing:
                since_str = last_briefing.strftime('%Y/%m/%d')
                query = f'is:unread after:{since_str}'
                time_desc = f"since last briefing ({last_briefing.strftime('%b %d, %I:%M %p')})"
            else:
                query = 'is:unread newer_than:1d'
                time_desc = "in the last 24 hours"
            
            results = google_call(
                service="gmail",
                resource_path="users.messages",
                method="list",
                params={"userId": "me", "q": query, "maxResults": 20},
            )
            messages = results.get('messages', [])
            
            unread_result = google_call(
                service="gmail",
                resource_path="users.messages",
                method="list",
                params={"userId": "me", "q": "is:unread", "maxResults": 1},
            )
            total_unread = unread_result.get('resultSizeEstimate', 0)
            
            if not messages:
                section.items.append(f"*No new unread emails {time_desc}* ✨")
                if total_unread > 0:
                    section.items.append(f"({total_unread} total unread)")
                return section
            
            section.items.append(f"**{len(messages)} new email{'s' if len(messages) != 1 else ''}** {time_desc} ({total_unread} total unread)")
            section.items.append("")
            
            action_emails, fyi_emails, notification_count = [], [], 0
            
            for msg in messages:
                try:
                    msg_data = google_call(
                        service="gmail",
                        resource_path="users.messages",
                        method="get",
                        params={
                            "userId": "me",
                            "id": msg['id'],
                            "format": "metadata",
                            "metadataHeaders": ["From", "Subject", "Date"],
                        },
                    )
                    headers = {h['name']: h['value'] for h in msg_data.get('payload', {}).get('headers', [])}
                    labels = msg_data.get('labelIds', [])
                    category = self._categorize_email(headers, labels)
                    sender = headers.get('From', 'Unknown')
                    subject = headers.get('Subject', 'No subject')
                    if '<' in sender:
                        sender = sender.split('<')[0].strip().strip('"')
                    if len(subject) > 50:
                        subject = subject[:47] + "..."
                    entry = f"- **{sender}**: {subject}"
                    if category == "action":
                        action_emails.append(entry)
                    elif category == "fyi":
                        fyi_emails.append(entry)
                    else:
                        notification_count += 1
                except Exception:
                    continue
            
            if action_emails:
                section.items.append(f"🔴 **Action Required** ({len(action_emails)})")
                for email in action_emails[:5]:
                    section.items.append(email)
                if len(action_emails) > 5:
                    section.items.append(f"- *...and {len(action_emails) - 5} more*")
                section.items.append("")
            if fyi_emails:
                section.items.append(f"🟡 **FYI** ({len(fyi_emails)})")
                for email in fyi_emails[:3]:
                    section.items.append(email)
                if len(fyi_emails) > 3:
                    section.items.append(f"- *...and {len(fyi_emails) - 3} more*")
                section.items.append("")
            if notification_count > 0:
                section.items.append(f"⚪ **Notifications** ({notification_count})")
                section.items.append(f"- {notification_count} automated notifications")
                    
        except (PermissionError, RuntimeError) as e:
            section.available = False
            section.error = "Gmail not accessible (gateway error)"
        except Exception as e:
            section.available = False
            section.error = f"Error: {str(e)[:50]}"
        
        return section
    
    def _get_weather_section(self) -> BriefingSection:
        """Get weather forecast via the Atlas credential gateway."""
        section = BriefingSection(title="Weather", emoji="🌤️")
        
        try:
            payload = {
                "service": "openweathermap",
                "method": "GET",
                "endpoint": "/weather",
                "params": {
                    "units": "metric",
                    "q": "Sydney,AU",  # Default — make configurable via atlas config
                },
            }
            resp = httpx.post("http://127.0.0.1:18080/v1/proxy", json=payload, headers=get_gateway_headers(), timeout=8.0)
            
            if resp.status_code == 404:
                # Service or location not found
                section.available = False
                section.error = "Weather location not found"
                return section
            
            if not resp.is_success:
                section.available = False
                section.error = "Weather API not configured (optional)"
                return section
            
            data = resp.json()
            temp = data['main']['temp']
            temp_f = (temp * 9/5) + 32
            feels_like = data['main']['feels_like']
            description = data['weather'][0]['description'].title()
            humidity = data['main']['humidity']
            
            section.items.append(f"**{temp:.0f}°C / {temp_f:.0f}°F** - {description}")
            section.items.append(f"Feels like {feels_like:.0f}°C, Humidity: {humidity}%")
            
        except httpx.ConnectError:
            section.available = False
            section.error = "Weather unavailable (gateway not running)"
        except Exception as e:
            section.available = False
            section.error = "Weather unavailable"
        
        return section
    
    def _get_news_section(self) -> BriefingSection:
        """Fetch top headlines via the Atlas credential gateway (Tavily)."""
        section = BriefingSection(title="News Headlines", emoji="📰")
        
        try:
            from atlas.config.loader import get_config
            config = get_config()
            topics = config.briefing.news_topics
            max_per_topic = config.briefing.max_headlines_per_topic
            
            for topic in topics:
                try:
                    payload = {
                        "service": "tavily",
                        "method": "POST",
                        "endpoint": "/search",
                        "json_body": {
                            "query": f"latest {topic} news today",
                            "max_results": max_per_topic,
                            "search_depth": "basic",
                        },
                    }
                    resp = httpx.post(
                        "http://127.0.0.1:18080/v1/proxy",
                        json=payload,
                        headers=get_gateway_headers(),
                        timeout=15.0,
                    )
                    
                    if not resp.is_success:
                        continue
                    
                    results_data = resp.json()
                    headlines = results_data.get("results", []) if isinstance(results_data, dict) else []
                    
                    if headlines:
                        section.items.append(f"**{topic.title()}**")
                        for h in headlines:
                            title = h.get("title", "Untitled")
                            url = h.get("url", "")
                            domain = ""
                            if url:
                                try:
                                    from urllib.parse import urlparse
                                    domain = urlparse(url).netloc.replace("www.", "")
                                except Exception:
                                    pass
                            if domain:
                                section.items.append(f"- **{title}** — {domain}")
                            else:
                                section.items.append(f"- **{title}**")
                        section.items.append("")
                except Exception:
                    continue
            
            if not section.items:
                section.items.append("*No headlines available*")
                
        except httpx.ConnectError:
            section.available = False
            section.error = "News unavailable (gateway not running)"
        except Exception as e:
            section.available = False
            section.error = f"News unavailable: {str(e)[:50]}"
        
        return section
    
    def _get_tasks_section(self) -> BriefingSection:
        """Get tasks due today and overdue tasks."""
        section = BriefingSection(title="Tasks", emoji="✅")
        
        try:
            results = google_call(
                service="tasks",
                resource_path="tasks",
                method="list",
                params={
                    "tasklist": "@default",
                    "maxResults": 100,
                    "showCompleted": False,
                    "showHidden": False,
                },
            )
            tasks = results.get('items', [])
            
            if not tasks:
                section.items.append("*No pending tasks* 🎉")
                return section
            
            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            overdue, due_today, upcoming = [], [], []
            
            for t in tasks:
                title = t.get('title', '').strip()
                if not title:
                    continue
                due = t.get('due', '')
                if due:
                    due_date = due[:10]
                    if due_date < today_str:
                        overdue.append((title, due_date))
                    elif due_date == today_str:
                        due_today.append(title)
                    else:
                        upcoming.append((title, due_date))
                else:
                    upcoming.append((title, ""))
            
            if overdue:
                section.items.append(f"🔴 **Overdue** ({len(overdue)})")
                for title, due in overdue:
                    section.items.append(f"- {title} *(due {due})*")
                section.items.append("")
            if due_today:
                section.items.append(f"📋 **Due Today** ({len(due_today)})")
                for title in due_today:
                    section.items.append(f"- {title}")
                section.items.append("")
            if upcoming:
                shown = upcoming[:5]
                section.items.append(f"📅 **Upcoming** ({len(upcoming)})")
                for title, due in shown:
                    if due:
                        section.items.append(f"- {title} *(due {due})*")
                    else:
                        section.items.append(f"- {title}")
                if len(upcoming) > 5:
                    section.items.append(f"- *...and {len(upcoming) - 5} more*")
                    
        except (PermissionError, RuntimeError) as e:
            section.available = False
            section.error = "Google Tasks not accessible (gateway error)"
        except Exception as e:
            section.available = False
            section.error = f"Tasks unavailable: {str(e)[:50]}"
        
        return section
    
    def _get_notes_section(self) -> BriefingSection:
        """Get pinned notes and recent notes summary."""
        section = BriefingSection(title="Pinned Notes", emoji="📌")
        
        try:
            from atlas.tools.notes import NotesManager
            
            manager = NotesManager(self.data_dir)
            
            # Get pinned notes
            pinned = manager.list_notes(limit=5, pinned_only=True)
            
            if not pinned:
                section.items.append("*No pinned notes*")
            else:
                for note in pinned:
                    tags = " ".join(f"#{t}" for t in note.tags[:2])
                    section.items.append(f"- {note.title} {tags}")
                    
        except Exception as e:
            section.available = False
            section.error = "Notes unavailable"
        
        return section
    
    def generate(
        self,
        target_date: Optional[datetime] = None,
        include_weather: bool = True,
        include_notes: bool = True,
        include_news: bool = True,
        include_tasks: bool = True,
    ) -> DailyBriefing:
        """Generate a complete daily briefing.
        
        Args:
            target_date: Date to generate briefing for (default: today)
            include_weather: Whether to include weather section
            include_notes: Whether to include notes section
            include_news: Whether to include news headlines section
            include_tasks: Whether to include tasks section
            
        Returns:
            DailyBriefing object with all sections
        """
        if target_date is None:
            target_date = datetime.now()
        
        briefing = DailyBriefing(
            date=target_date,
            greeting=self._get_greeting(),
            sections=[]
        )
        
        # Calendar section (always included)
        briefing.sections.append(self._get_calendar_section(target_date))
        
        # Email section (always included)
        briefing.sections.append(self._get_email_section())
        
        # Tasks section (optional)
        if include_tasks:
            tasks = self._get_tasks_section()
            if tasks.available or tasks.error != "Google Tasks not configured":
                briefing.sections.append(tasks)
        
        # Weather section (optional)
        if include_weather:
            weather = self._get_weather_section()
            if weather.available or weather.error != "Weather API not configured (optional)":
                briefing.sections.append(weather)
        
        # News section (optional)
        if include_news:
            news = self._get_news_section()
            if news.available or news.error != "Tavily API not configured":
                briefing.sections.append(news)
        
        # Notes section (optional)
        if include_notes:
            briefing.sections.append(self._get_notes_section())
        
        # Save briefing time for "since last briefing" tracking
        self._save_briefing_time()
        
        return briefing


def create_briefing_command(data_dir: Path):
    """Create the briefing CLI command function.
    
    Args:
        data_dir: Base data directory for Atlas
        
    Returns:
        CLI command function
    """
    generator = BriefingGenerator(data_dir)
    
    def briefing_command(
        date: Optional[str] = None,
        no_weather: bool = False,
        no_notes: bool = False,
    ) -> str:
        """Generate daily briefing."""
        target_date = datetime.now()
        if date:
            if date.lower() == "tomorrow":
                target_date = datetime.now() + timedelta(days=1)
            elif date.lower() == "yesterday":
                target_date = datetime.now() - timedelta(days=1)
            else:
                try:
                    target_date = datetime.fromisoformat(date)
                except ValueError:
                    pass
        
        briefing = generator.generate(
            target_date=target_date,
            include_weather=not no_weather,
            include_notes=not no_notes,
        )
        
        return briefing.to_markdown()
    
    return briefing_command
