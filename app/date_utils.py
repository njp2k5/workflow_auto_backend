"""
Date parsing utilities for handling natural language dates.
Converts expressions like "tomorrow", "next Friday", "in 3 days" to ISO format.
"""
import logging
import re
from datetime import date, datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Try to import dateparser for natural language parsing
try:
    import dateparser
    DATEPARSER_AVAILABLE = True
except ImportError:
    dateparser = None  # type: ignore
    DATEPARSER_AVAILABLE = False
    logger.warning("dateparser not installed - natural language dates will use fallback parsing")


# Common date patterns for fallback parsing
RELATIVE_DATE_PATTERNS = {
    r'^today$': lambda: date.today(),
    r'^tomorrow$': lambda: date.today() + timedelta(days=1),
    r'^yesterday$': lambda: date.today() - timedelta(days=1),
    r'^next\s+week$': lambda: date.today() + timedelta(weeks=1),
    r'^in\s+(\d+)\s+days?$': lambda m: date.today() + timedelta(days=int(m.group(1))),
    r'^in\s+(\d+)\s+weeks?$': lambda m: date.today() + timedelta(weeks=int(m.group(1))),
    r'^(\d+)\s+days?\s+from\s+now$': lambda m: date.today() + timedelta(days=int(m.group(1))),
    r'^end\s+of\s+week$': lambda: _get_end_of_week(),
    r'^end\s+of\s+month$': lambda: _get_end_of_month(),
}

# Day of week patterns
WEEKDAY_NAMES = {
    'monday': 0, 'mon': 0,
    'tuesday': 1, 'tue': 1, 'tues': 1,
    'wednesday': 2, 'wed': 2,
    'thursday': 3, 'thu': 3, 'thur': 3, 'thurs': 3,
    'friday': 4, 'fri': 4,
    'saturday': 5, 'sat': 5,
    'sunday': 6, 'sun': 6
}


def _get_end_of_week() -> date:
    """Get the date of the coming Sunday."""
    today = date.today()
    days_until_sunday = (6 - today.weekday()) % 7
    if days_until_sunday == 0:
        days_until_sunday = 7  # If today is Sunday, get next Sunday
    return today + timedelta(days=days_until_sunday)


def _get_end_of_month() -> date:
    """Get the last day of the current month."""
    today = date.today()
    if today.month == 12:
        next_month = date(today.year + 1, 1, 1)
    else:
        next_month = date(today.year, today.month + 1, 1)
    return next_month - timedelta(days=1)


def _get_next_weekday(weekday: int) -> date:
    """Get the next occurrence of a specific weekday (0=Monday, 6=Sunday)."""
    today = date.today()
    days_ahead = weekday - today.weekday()
    if days_ahead <= 0:  # Target day already happened this week
        days_ahead += 7
    return today + timedelta(days=days_ahead)


def _fallback_parse_date(text: str) -> Optional[date]:
    """
    Fallback date parsing without dateparser library.
    Handles common natural language patterns.
    
    Args:
        text: Natural language date string
        
    Returns:
        Parsed date or None
    """
    text_lower = text.lower().strip()
    
    # Try relative patterns first
    for pattern, date_func in RELATIVE_DATE_PATTERNS.items():
        match = re.match(pattern, text_lower, re.IGNORECASE)
        if match:
            try:
                if match.groups():
                    return date_func(match)
                else:
                    return date_func()
            except Exception:
                continue
    
    # Try weekday names (e.g., "Friday", "next Monday")
    for day_name, weekday in WEEKDAY_NAMES.items():
        if day_name in text_lower:
            return _get_next_weekday(weekday)
    
    return None


def parse_due_date(text: str) -> Optional[date]:
    """
    Parse a due date from natural language or ISO format.
    
    Supports:
    - ISO format: 2026-02-22
    - Natural language: tomorrow, Friday, next week, in 3 days, etc.
    - Null/None values
    
    Args:
        text: Date string to parse
        
    Returns:
        Parsed date object or None if parsing fails
    """
    if not text or text.lower() in ['null', 'none', 'n/a', '', 'unspecified']:
        return None
    
    text = str(text).strip()
    
    # Try ISO format first (YYYY-MM-DD)
    iso_pattern = r'^\d{4}-\d{2}-\d{2}$'
    if re.match(iso_pattern, text):
        try:
            return date.fromisoformat(text)
        except ValueError:
            pass
    
    # Try other common date formats
    date_formats = [
        '%Y-%m-%d',
        '%d/%m/%Y',
        '%m/%d/%Y',
        '%d-%m-%Y',
        '%B %d, %Y',
        '%b %d, %Y',
        '%d %B %Y',
        '%d %b %Y',
    ]
    
    for fmt in date_formats:
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.date()
        except ValueError:
            continue
    
    # Try natural language parsing with dateparser (if available)
    if DATEPARSER_AVAILABLE and dateparser:
        try:
            parsed = dateparser.parse(
                text,
                settings={
                    'PREFER_DATES_FROM': 'future',
                    'RELATIVE_BASE': datetime.now()
                }
            )
            if parsed:
                result = parsed.date()
                logger.debug(f"Parsed '{text}' -> {result.isoformat()}")
                return result
        except Exception as e:
            logger.debug(f"dateparser failed for '{text}': {e}")
    
    # Fallback to manual parsing
    fallback_result = _fallback_parse_date(text)
    if fallback_result:
        logger.debug(f"Fallback parsed '{text}' -> {fallback_result.isoformat()}")
        return fallback_result
    
    logger.warning(f"Could not parse date: '{text}'")
    return None


def format_date_iso(d: Optional[date]) -> Optional[str]:
    """
    Format a date to ISO format string.
    
    Args:
        d: Date object or None
        
    Returns:
        ISO format string (YYYY-MM-DD) or None
    """
    if d is None:
        return None
    return d.isoformat()


def get_default_deadline(days_from_now: int = 7) -> date:
    """
    Get a default deadline date.
    
    Args:
        days_from_now: Number of days in the future
        
    Returns:
        Date object for the default deadline
    """
    return date.today() + timedelta(days=days_from_now)
