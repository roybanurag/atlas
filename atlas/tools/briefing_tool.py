"""Daily Briefing agent tool for Atlas.

Wraps BriefingGenerator as a LangChain tool so the agent can generate
briefings conversationally (e.g. "What's my day look like?").
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool


def create_briefing_tool(data_dir: Path):
    """Create the daily briefing LangChain tool.

    Args:
        data_dir: Base data directory for Atlas

    Returns:
        List of LangChain tools for briefing
    """
    from atlas.tools.briefing import BriefingGenerator

    generator = BriefingGenerator(data_dir)

    @tool
    def get_daily_briefing(date: Optional[str] = None) -> str:
        """Get a personalized daily briefing with calendar, emails, tasks, news, weather, and notes.

        Use this tool when the user asks about their day, schedule, morning summary,
        or wants an overview of what's happening. It compiles calendar events,
        categorized email summary, Google Tasks (overdue and due today),
        news headlines, weather forecast, and pinned notes.

        Args:
            date: Optional date string: 'today' (default), 'tomorrow', 'yesterday',
                  or a date in YYYY-MM-DD format.

        Returns:
            Formatted markdown briefing with all available sections.
        """
        target_date = datetime.now()
        if date:
            date_lower = date.lower().strip()
            if date_lower == "tomorrow":
                target_date = datetime.now() + timedelta(days=1)
            elif date_lower == "yesterday":
                target_date = datetime.now() - timedelta(days=1)
            elif date_lower != "today":
                try:
                    target_date = datetime.fromisoformat(date)
                except ValueError:
                    return f"Invalid date format: {date}. Use 'today', 'tomorrow', or YYYY-MM-DD."

        briefing = generator.generate(target_date=target_date)
        briefing_md = briefing.to_markdown()
        
        # Guide the LLM to prevent it from summarizing away the news section
        return (
            f"Here is the raw data for the daily briefing.\n\n"
            f"{briefing_md}\n\n"
            f"CRITICAL INSTRUCTION: When you present this briefing to the user, you MUST "
            f"include the News Headlines section and list out the actual headlines provided above. "
            f"Do not skip or overly summarize the news."
        )

    return [get_daily_briefing]
