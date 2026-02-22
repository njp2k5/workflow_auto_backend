"""
LangGraph workflow for processing meeting recordings.
Defines the processing pipeline with nodes for summarization, task extraction,
Jira issue creation, and result storage.

Database Schema:
- members: member_id, member_name, designation, password
- transcription: transcription_id, transcription_summary
- meetings: meeting_id, meeting_date, transcription_id (FK)
- tasks: task_id, member_id (FK), description, deadline
"""
import logging
from typing import TypedDict, List, Dict, Any, Optional
from datetime import datetime, date

from langgraph.graph import StateGraph, END

from app.llm import get_llm_client
from app.jira_client import get_jira_client
from app.db import get_db_session
from app.models import Meeting, Transcription, Task, Member, ProcessingLog

logger = logging.getLogger(__name__)


class MeetingState(TypedDict):
    """State object passed through the LangGraph workflow."""
    # Input data
    meeting_date: str  # ISO date string YYYY-MM-DD
    transcript: str
    filename: Optional[str]
    file_path: Optional[str]
    
    # Processed data
    summary: Optional[str]
    tasks: List[Dict[str, Any]]
    jira_keys: List[str]
    
    # Database IDs (set after store_results)
    transcription_id: Optional[int]
    meeting_id: Optional[int]
    task_ids: List[int]
    
    # Processing status
    error: Optional[str]
    current_step: str


def log_processing_step(
    meeting_id: Optional[int],
    step: str,
    status: str,
    message: Optional[str] = None
) -> None:
    """Log a processing step to the database."""
    try:
        with get_db_session() as db:
            log_entry = ProcessingLog(
                meeting_id=meeting_id,
                step=step,
                status=status,
                message=message
            )
            db.add(log_entry)
            db.commit()
    except Exception as e:
        logger.error(f"Failed to log processing step: {e}")


def summarize_meeting(state: MeetingState) -> MeetingState:
    """
    Node: Summarize the meeting transcript using LLM.
    """
    logger.info("Summarizing meeting transcript")
    state["current_step"] = "summarize_meeting"
    
    log_processing_step(state.get("meeting_id"), "summarize_meeting", "started")
    
    try:
        llm_client = get_llm_client()
        
        if not llm_client.is_configured:
            raise RuntimeError("LLM client not configured")
        
        summary = llm_client.summarize_meeting(state["transcript"])
        state["summary"] = summary
        
        log_processing_step(
            state.get("meeting_id"),
            "summarize_meeting",
            "completed",
            message=f"Summary length: {len(summary)}"
        )
        
        logger.info(f"Generated summary ({len(summary)} chars)")
        
    except Exception as e:
        error_msg = f"Failed to summarize meeting: {str(e)}"
        logger.error(error_msg)
        state["error"] = error_msg
        log_processing_step(state.get("meeting_id"), "summarize_meeting", "failed", message=error_msg)
    
    return state


def extract_tasks(state: MeetingState) -> MeetingState:
    """
    Node: Extract action items from the transcript using LLM.
    """
    if state.get("error"):
        return state
    
    logger.info("Extracting tasks from transcript")
    state["current_step"] = "extract_tasks"
    
    log_processing_step(state.get("meeting_id"), "extract_tasks", "started")
    
    try:
        llm_client = get_llm_client()
        
        if not llm_client.is_configured:
            raise RuntimeError("LLM client not configured")
        
        summary = state.get("summary", "")
        tasks_result = llm_client.extract_tasks(state["transcript"], summary)
        tasks = tasks_result.get("tasks", [])
        state["tasks"] = tasks
        
        log_processing_step(
            state.get("meeting_id"),
            "extract_tasks",
            "completed",
            message=f"Extracted {len(tasks)} tasks"
        )
        
        logger.info(f"Extracted {len(tasks)} tasks")
        
    except Exception as e:
        error_msg = f"Failed to extract tasks: {str(e)}"
        logger.error(error_msg)
        state["error"] = error_msg
        log_processing_step(state.get("meeting_id"), "extract_tasks", "failed", message=error_msg)
    
    return state


def create_jira_issues(state: MeetingState) -> MeetingState:
    """
    Node: Create Jira issues for extracted tasks.
    """
    if state.get("error"):
        return state
    
    tasks = state.get("tasks", [])
    if not tasks:
        logger.info("No tasks to create Jira issues for")
        state["jira_keys"] = []
        return state
    
    logger.info(f"Creating Jira issues for {len(tasks)} tasks")
    state["current_step"] = "create_jira_issues"
    
    log_processing_step(state.get("meeting_id"), "create_jira_issues", "started")
    
    jira_keys = []
    
    try:
        jira_client = get_jira_client()
        
        if not jira_client.is_configured:
            logger.warning("Jira client not configured, skipping issue creation")
            state["jira_keys"] = []
            log_processing_step(
                state.get("meeting_id"),
                "create_jira_issues",
                "skipped",
                message="Jira not configured"
            )
            return state
        
        for task in tasks:
            try:
                title = task.get("title", "Untitled Task")
                description = task.get("description", "")
                assignee = task.get("assignee")
                priority = task.get("priority", "Medium")
                
                full_description = f"{description}\n\n---\nExtracted from meeting recording"
                if state.get("filename"):
                    full_description += f"\nSource: {state['filename']}"
                
                issue_key = jira_client.create_issue(
                    summary=title,
                    description=full_description,
                    issue_type="Task",
                    assignee_name=assignee,
                    priority=priority
                )
                
                if issue_key:
                    jira_keys.append(issue_key)
                    logger.info(f"Created Jira issue: {issue_key}")
                    
            except Exception as e:
                logger.error(f"Failed to create Jira issue for task '{task.get('title')}': {e}")
        
        state["jira_keys"] = jira_keys
        
        log_processing_step(
            state.get("meeting_id"),
            "create_jira_issues",
            "completed",
            message=f"Created {len(jira_keys)} issues"
        )
        
    except Exception as e:
        error_msg = f"Failed in Jira issue creation: {str(e)}"
        logger.error(error_msg)
        state["jira_keys"] = jira_keys
        log_processing_step(
            state.get("meeting_id"),
            "create_jira_issues",
            "failed",
            message=error_msg
        )
    
    return state


def store_results(state: MeetingState) -> MeetingState:
    """
    Node: Store processing results in the database.
    Creates Transcription, Meeting, and Task records according to schema.
    """
    logger.info("Storing results in database")
    state["current_step"] = "store_results"
    
    log_processing_step(state.get("meeting_id"), "store_results", "started")
    
    try:
        with get_db_session() as db:
            # 1. Create Transcription record with the summary
            transcription_text = state.get("summary") or state["transcript"][:5000]
            transcription = Transcription(
                transcription_summary=transcription_text
            )
            db.add(transcription)
            db.flush()  # Get transcription_id
            
            state["transcription_id"] = transcription.transcription_id  # type: ignore[assignment]
            logger.info(f"Created transcription_id={transcription.transcription_id}")
            
            # 2. Parse meeting date
            meeting_date_str = state.get("meeting_date")
            if meeting_date_str:
                try:
                    meeting_date_val = date.fromisoformat(meeting_date_str)
                except ValueError:
                    meeting_date_val = date.today()
            else:
                meeting_date_val = date.today()
            
            # 3. Create Meeting record linked to transcription
            meeting = Meeting(
                meeting_date=meeting_date_val,
                transcription_id=transcription.transcription_id
            )
            db.add(meeting)
            db.flush()  # Get meeting_id
            
            state["meeting_id"] = meeting.meeting_id  # type: ignore[assignment]
            logger.info(f"Created meeting_id={meeting.meeting_id}")
            
            # 4. Create Task records for extracted tasks
            task_ids = []
            tasks = state.get("tasks", [])
            
            for task_data in tasks:
                assignee_name = task_data.get("assignee")
                
                # Find member by name (case-insensitive partial match)
                member_id = None
                if assignee_name:
                    member = db.query(Member).filter(
                        Member.member_name.ilike(f"%{assignee_name}%")
                    ).first()
                    if member:
                        member_id = member.member_id
                        logger.info(f"Found member '{member.member_name}' for assignee '{assignee_name}'")
                    else:
                        logger.warning(f"No member found for assignee '{assignee_name}'")
                
                # Only create task if we have a member (FK constraint)
                if member_id:  # type: ignore[truthy-bool]
                    # Parse deadline
                    deadline_str = task_data.get("deadline")
                    if deadline_str:
                        try:
                            deadline_val = date.fromisoformat(deadline_str)
                        except ValueError:
                            # Default to 7 days from now
                            from datetime import timedelta
                            deadline_val = date.today() + timedelta(days=7)
                    else:
                        from datetime import timedelta
                        deadline_val = date.today() + timedelta(days=7)
                    
                    # Create task
                    task = Task(
                        member_id=member_id,
                        description=task_data.get("description") or task_data.get("title", "Task from meeting"),
                        deadline=deadline_val
                    )
                    db.add(task)
                    db.flush()
                    task_ids.append(task.task_id)
                    logger.info(f"Created task_id={task.task_id} for member_id={member_id}")
                else:
                    logger.warning(f"Skipping task '{task_data.get('title')}' - no matching member")
            
            state["task_ids"] = task_ids
            db.commit()
            
            logger.info(f"Stored: meeting_id={meeting.meeting_id}, transcription_id={transcription.transcription_id}, tasks={len(task_ids)}")
        
        log_processing_step(state["meeting_id"], "store_results", "completed")  # type: ignore[arg-type]
        
    except Exception as e:
        error_msg = f"Failed to store results: {str(e)}"
        logger.error(error_msg)
        state["error"] = error_msg
        log_processing_step(state.get("meeting_id"), "store_results", "failed", message=error_msg)
    
    return state


def should_continue(state: MeetingState) -> str:
    """Decide whether to continue processing or end due to error."""
    if state.get("error"):
        return "end"
    return "continue"


def create_pipeline():
    """
    Create the LangGraph workflow for meeting processing.
    
    Pipeline:
    1. summarize_meeting - Generate meeting summary
    2. extract_tasks - Extract action items
    3. create_jira_issues - Create Jira issues for tasks
    4. store_results - Store in database (transcription, meeting, tasks)
    """
    workflow = StateGraph(MeetingState)
    
    # Add nodes
    workflow.add_node("summarize_meeting", summarize_meeting)
    workflow.add_node("extract_tasks", extract_tasks)
    workflow.add_node("create_jira_issues", create_jira_issues)
    workflow.add_node("store_results", store_results)
    
    # Define flow
    workflow.set_entry_point("summarize_meeting")
    
    workflow.add_conditional_edges(
        "summarize_meeting",
        should_continue,
        {"continue": "extract_tasks", "end": "store_results"}
    )
    
    workflow.add_conditional_edges(
        "extract_tasks",
        should_continue,
        {"continue": "create_jira_issues", "end": "store_results"}
    )
    
    workflow.add_conditional_edges(
        "create_jira_issues",
        should_continue,
        {"continue": "store_results", "end": "store_results"}
    )
    
    workflow.add_edge("store_results", END)
    
    return workflow.compile()


# Cache the compiled pipeline
_pipeline = None


def get_pipeline():
    """Get or create the cached pipeline instance."""
    global _pipeline
    if _pipeline is None:
        _pipeline = create_pipeline()
    return _pipeline


def process_meeting(
    transcript: str,
    meeting_date: Optional[str] = None,
    filename: Optional[str] = None,
    file_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Process a meeting recording through the full pipeline.
    
    Args:
        transcript: Full meeting transcript text
        meeting_date: ISO date string (YYYY-MM-DD), defaults to today
        filename: Original recording filename
        file_path: Full path to the recording file
        
    Returns:
        Dictionary with processing results including:
        - summary: Generated meeting summary
        - tasks: List of extracted tasks
        - jira_keys: List of created Jira issue keys
        - meeting_id: Database meeting ID
        - transcription_id: Database transcription ID
        - task_ids: List of created task IDs
        - error: Error message if any
    """
    logger.info(f"Starting pipeline processing for: {filename or 'meeting'}")
    
    initial_state: MeetingState = {
        "meeting_date": meeting_date or date.today().isoformat(),
        "transcript": transcript,
        "filename": filename,
        "file_path": file_path,
        "summary": None,
        "tasks": [],
        "jira_keys": [],
        "transcription_id": None,
        "meeting_id": None,
        "task_ids": [],
        "error": None,
        "current_step": "initialized"
    }
    
    try:
        pipeline = get_pipeline()
        result = pipeline.invoke(initial_state)
        
        return dict(result)
        
    except Exception as e:
        logger.error(f"Pipeline execution failed: {e}")
        return {
            **initial_state,
            "error": str(e)
        }


def process_recording(file_path: str, transcript: str) -> Dict[str, Any]:
    """
    Convenience function to process a recording file.
    
    Args:
        file_path: Path to the recording file
        transcript: Transcribed text from the recording
        
    Returns:
        Dictionary with processing results
    """
    from pathlib import Path
    
    path = Path(file_path)
    filename = path.name
    
    return process_meeting(
        transcript=transcript,
        meeting_date=date.today().isoformat(),
        filename=filename,
        file_path=str(path.absolute())
    )
