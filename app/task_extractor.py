"""
Robust task extraction with JSON parsing improvements, retry logic, and regex fallback.
"""
import json
import re
from typing import Optional, List, Dict, Any
from datetime import date

from app.date_utils import parse_due_date, format_date_iso, get_default_deadline
from app.member_matching import get_member_name
from app.logger import get_logger

logger = get_logger(__name__)


# Task extraction schema for validation
TASK_SCHEMA = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "assignee": {"type": "string"},
                    "description": {"type": "string"},
                    "due_date": {"type": ["string", "null"]}
                },
                "required": ["description"]
            }
        }
    },
    "required": ["tasks"]
}


def clean_json_response(response_text: str) -> str:
    """
    Clean LLM response to extract valid JSON.
    
    Args:
        response_text: Raw LLM response
        
    Returns:
        Cleaned JSON string
    """
    if not response_text:
        return '{"tasks": []}'
    
    text = response_text.strip()
    
    # Remove markdown code blocks
    if '```json' in text:
        text = text.split('```json', 1)[1]
        if '```' in text:
            text = text.split('```', 1)[0]
    elif '```' in text:
        parts = text.split('```')
        if len(parts) >= 2:
            text = parts[1]
    
    text = text.strip()
    
    # Find JSON object boundaries
    start_idx = text.find('{')
    end_idx = text.rfind('}')
    
    if start_idx >= 0 and end_idx > start_idx:
        text = text[start_idx:end_idx + 1]
    
    return text


def parse_json_safely(json_text: str) -> Optional[Dict[str, Any]]:
    """
    Safely parse JSON with multiple fallback strategies.
    
    Args:
        json_text: JSON string to parse
        
    Returns:
        Parsed dictionary or None
    """
    if not json_text or not json_text.strip():
        return None
    
    text = clean_json_response(json_text)
    
    # Strategy 1: Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    
    # Strategy 2: Fix common issues
    # Replace single quotes with double quotes
    fixed = text.replace("'", '"')
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    
    # Strategy 3: Fix trailing commas
    fixed = re.sub(r',\s*}', '}', text)
    fixed = re.sub(r',\s*]', ']', fixed)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    
    # Strategy 4: Extract array if tasks property missing
    array_match = re.search(r'\[\s*\{.*?\}\s*\]', text, re.DOTALL)
    if array_match:
        try:
            tasks = json.loads(array_match.group())
            return {"tasks": tasks}
        except json.JSONDecodeError:
            pass
    
    logger.debug(f"All JSON parsing strategies failed for: {text[:100]}...")
    return None


def extract_tasks_from_text_fallback(text: str) -> List[Dict[str, Any]]:
    """
    Extract tasks using regex patterns when JSON parsing fails.
    Looks for patterns like:
    - "X will do Y by Z"
    - "X is assigned to Y"
    - "Task: Y - Assignee: X"
    
    Args:
        text: Text to extract tasks from (transcript or summary)
        
    Returns:
        List of extracted tasks
    """
    tasks = []
    
    # Pattern 1: "X will/should/needs to [do] Y [by Z]"
    pattern1 = re.compile(
        r'(?P<assignee>\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+'
        r'(?:will|should|needs?\s+to|is\s+going\s+to|must)\s+'
        r'(?:do\s+)?(?P<task>[^.!?]+?)(?:\s+by\s+(?P<date>[^.!?]+))?[.!?]',
        re.IGNORECASE
    )
    
    # Pattern 2: "Y is assigned to X"
    pattern2 = re.compile(
        r'(?P<task>[^.!?]+?)\s+is\s+assigned\s+to\s+'
        r'(?P<assignee>\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)',
        re.IGNORECASE
    )
    
    # Pattern 3: "Action item: X to do Y"
    pattern3 = re.compile(
        r'(?:action\s+item|task)[:\s]+(?P<assignee>[A-Za-z]+(?:\s+[A-Za-z]+)*)\s+'
        r'(?:to\s+)?(?P<task>[^.!?]+)',
        re.IGNORECASE
    )
    
    # Pattern 4: Simple "X should work on Y"
    pattern4 = re.compile(
        r'(?P<assignee>\b[A-Z][a-z]+)\s+'
        r'(?:should|could|can)\s+'
        r'(?:work\s+on|handle|complete|start|begin|finish)\s+'
        r'(?P<task>[^.!?,]+)',
        re.IGNORECASE
    )
    
    seen_tasks = set()
    
    for pattern in [pattern1, pattern2, pattern3, pattern4]:
        for match in pattern.finditer(text):
            task_desc = match.group('task').strip() if 'task' in match.groupdict() else ""
            assignee = match.group('assignee').strip() if 'assignee' in match.groupdict() else None
            due_date = match.group('date').strip() if 'date' in match.groupdict() and match.group('date') else None
            
            if task_desc and task_desc not in seen_tasks:
                # Avoid duplicate tasks
                seen_tasks.add(task_desc)
                
                task = {
                    "description": task_desc,
                    "assignee": assignee,
                    "due_date": due_date
                }
                tasks.append(task)
                logger.debug(f"Regex extracted task: {task}")
    
    return tasks


def validate_and_normalize_task(task: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate and normalize a single task.
    
    Args:
        task: Raw task dictionary
        
    Returns:
        Normalized task dictionary
    """
    # Get description (support both 'title' and 'description' keys)
    description = task.get("description") or task.get("title") or "Untitled Task"
    
    # Get and normalize assignee
    raw_assignee = task.get("assignee")
    if raw_assignee and raw_assignee.lower() not in ['unassigned', 'none', 'null', 'n/a', '']:
        # Try to match to a team member
        matched = get_member_name(raw_assignee)
        assignee = matched or raw_assignee  # Keep original if no match
    else:
        assignee = None
    
    # Parse and normalize due date
    raw_date = task.get("due_date") or task.get("deadline")
    if raw_date:
        parsed_date = parse_due_date(str(raw_date))
        due_date = format_date_iso(parsed_date)
    else:
        due_date = None
    
    return {
        "description": description.strip(),
        "assignee": assignee,
        "due_date": due_date
    }


def safe_extract_tasks(
    transcript: str,
    summary: Optional[str] = None,
    llm_response: Optional[str] = None
) -> Dict[str, Any]:
    """
    Safely extract tasks with multiple fallback strategies.
    
    Strategies (in order):
    1. Parse LLM JSON response
    2. Extract tasks from text using regex patterns
    3. Return empty tasks list
    
    Args:
        transcript: Meeting transcript
        summary: Optional meeting summary
        llm_response: Optional raw LLM response to parse
        
    Returns:
        Dictionary with 'tasks' list
    """
    tasks: List[Dict[str, Any]] = []
    extraction_method = "none"
    
    # Strategy 1: Parse LLM JSON response
    if llm_response:
        parsed = parse_json_safely(llm_response)
        if parsed and "tasks" in parsed:
            raw_tasks = parsed.get("tasks", [])
            if isinstance(raw_tasks, list) and raw_tasks:
                tasks = [validate_and_normalize_task(t) for t in raw_tasks if isinstance(t, dict)]
                extraction_method = "json"
                logger.info(f"Extracted {len(tasks)} tasks via JSON parsing")
    
    # Strategy 2: Regex fallback on transcript/summary
    if not tasks:
        # Try summary first (more likely to have structured information)
        if summary:
            tasks = extract_tasks_from_text_fallback(summary)
            if tasks:
                extraction_method = "regex_summary"
        
        # Then try transcript
        if not tasks and transcript:
            tasks = extract_tasks_from_text_fallback(transcript)
            if tasks:
                extraction_method = "regex_transcript"
        
        if tasks:
            tasks = [validate_and_normalize_task(t) for t in tasks]
            logger.info(f"Extracted {len(tasks)} tasks via regex ({extraction_method})")
    
    # Filter out invalid tasks
    valid_tasks = [t for t in tasks if t.get("description") and len(t["description"]) > 3]
    
    if len(valid_tasks) < len(tasks):
        logger.debug(f"Filtered out {len(tasks) - len(valid_tasks)} invalid tasks")
    
    return {
        "tasks": valid_tasks,
        "extraction_method": extraction_method,
        "raw_count": len(tasks)
    }


def format_tasks_for_jira(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Format tasks for Jira issue creation.
    
    Args:
        tasks: List of normalized tasks
        
    Returns:
        List of tasks formatted for Jira API
    """
    jira_tasks = []
    
    for task in tasks:
        jira_task = {
            "summary": task.get("description", "Untitled Task")[:255],  # Jira limit
            "description": task.get("description", ""),
            "assignee": task.get("assignee"),
            "due_date": task.get("due_date")
        }
        jira_tasks.append(jira_task)
    
    return jira_tasks
