"""Memory consolidation — extract durable knowledge from daily logs.

Runs after sessions or as a scheduled task to identify recurring
themes, user preferences, decisions, and facts worth preserving
in the knowledge base.
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


async def consolidate_memories(
    memory_store,
    llm=None,
    lookback_days: int = 7,
) -> list[str]:
    """Review recent daily logs and extract durable knowledge.
    
    Process:
    1. Load daily logs from the past N days
    2. Ask LLM (or extract heuristically) for recurring themes,
       decisions, preferences, and facts
    3. Store extracted facts in knowledge base
    4. Return list of newly stored facts
    
    Args:
        memory_store: MemoryStore instance
        llm: Optional LLM for smarter extraction (falls back to heuristic)
        lookback_days: Number of days to look back
        
    Returns:
        List of extracted knowledge strings
    """
    # Collect recent daily log content
    daily_dir = memory_store.daily_dir
    if not daily_dir.exists():
        return []
    
    log_content = []
    for days_ago in range(lookback_days):
        date = datetime.now() - timedelta(days=days_ago)
        log_file = daily_dir / f"{date.strftime('%Y-%m-%d')}.md"
        
        if log_file.exists():
            content = log_file.read_text()
            if len(content.strip()) > 50:  # Skip near-empty logs
                log_content.append((date.strftime('%Y-%m-%d'), content))
    
    if not log_content:
        logger.info("No recent daily logs to consolidate.")
        return []
    
    # Extract knowledge
    if llm is not None:
        facts = await _llm_consolidate(log_content, llm)
    else:
        facts = _heuristic_consolidate(log_content)
    
    # Store each fact as knowledge
    stored = []
    for fact in facts:
        if fact.strip():
            doc_id = await memory_store.store_knowledge(
                fact,
                source="consolidation",
                metadata={'lookback_days': lookback_days},
            )
            stored.append(fact)
    
    if stored:
        logger.info(f"Consolidated {len(stored)} facts from {len(log_content)} daily logs")
    
    return stored


async def _llm_consolidate(
    log_content: list[tuple[str, str]],
    llm,
) -> list[str]:
    """Use LLM to extract durable knowledge from daily logs."""
    from langchain_core.messages import HumanMessage
    
    # Combine logs with dates
    combined = []
    for date_str, content in log_content:
        # Truncate each day's content to keep within LLM context limits
        truncated = content[:3000]
        combined.append(f"--- {date_str} ---\n{truncated}")
    
    all_logs = "\n\n".join(combined)
    
    prompt = (
        "Review the following conversation logs from recent days. "
        "Extract durable knowledge worth remembering long-term.\n\n"
        "Focus on:\n"
        "- User preferences and habits\n"
        "- Decisions made and their rationale\n"
        "- Technical configurations or setups\n"
        "- Important facts or reference information\n"
        "- Recurring themes or patterns\n\n"
        "Return each fact as a separate line, prefixed with '- '. "
        "Be concise and specific. Skip trivial greetings or small talk.\n\n"
        f"Logs:\n{all_logs}\n\n"
        "Extracted knowledge:"
    )
    
    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        text = response.content.strip()
        
        # Parse bullet-point list
        facts = []
        for line in text.split('\n'):
            line = line.strip()
            if line.startswith('- '):
                facts.append(line[2:].strip())
            elif line.startswith('• '):
                facts.append(line[2:].strip())
        
        return facts if facts else [text]  # Fallback to whole text if no bullets
        
    except Exception as e:
        logger.warning(f"LLM consolidation failed: {e}")
        return _heuristic_consolidate(log_content)


def _heuristic_consolidate(
    log_content: list[tuple[str, str]],
) -> list[str]:
    """Extract knowledge heuristically when no LLM is available.
    
    Looks for patterns like:
    - Questions and their answers
    - Configuration/setup mentions
    - Repeated topics across days
    """
    from collections import Counter
    
    facts = []
    topic_counter: Counter = Counter()
    
    for date_str, content in log_content:
        sections = content.split("\n## ")[1:]  # Skip header
        
        for section in sections:
            lines = section.strip().split("\n")
            if not lines:
                continue
            
            header = lines[0].strip()
            body = "\n".join(lines[1:]).strip()
            
            # Count topics (words in headers)
            for word in header.lower().split():
                if len(word) > 3 and word.isalpha():
                    topic_counter[word] += 1
            
            # Extract Q&A style knowledge
            if "ASSISTANT" in header and body:
                # Take first sentence of assistant responses as potential knowledge
                first_sentence = body.split('.')[0].strip()
                if len(first_sentence) > 20 and len(first_sentence) < 200:
                    facts.append(f"[{date_str}] {first_sentence}")
    
    # Add recurring topics
    recurring = [
        word for word, count in topic_counter.most_common(5)
        if count >= 2
    ]
    if recurring:
        facts.append(f"Recurring topics: {', '.join(recurring)}")
    
    return facts[:10]  # Limit to 10 facts
