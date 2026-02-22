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
import time
from typing import TypedDict, List, Dict, Any, Optional
from datetime import datetime, date

from langgraph.graph import StateGraph, END

from app.llm import get_llm_client
from app.jira_client import get_jira_client
from app.confluence_client import get_confluence_client, build_meeting_page_html
from app.db import get_db_session
from app.models import Meeting, Transcription, Task, Member, ProcessingLog
from app.config import settings
from app.logger import (
    get_logger,
    log_node_entry,
    log_node_exit,
    log_pipeline_start,
    log_pipeline_end,
    log_step_progress,
)

logger = get_logger(__name__)


class MeetingState(TypedDict):
    """State object passed through the LangGraph workflow."""
    # Input data
    meeting_date: str  # ISO date string YYYY-MM-DD
    transcript: str
    filename: Optional[str]
    file_path: Optional[str]
    
    # Extracted metadata
    meeting_title: Optional[str]  # LLM-extracted meeting title
    project_name: Optional[str]   # Extracted project/product name
    
    # Processed data
    summary: Optional[str]
    tasks: List[Dict[str, Any]]
    jira_keys: List[str]
    skipped_tasks: List[Dict[str, Any]]  # Tasks skipped due to duplicates
    
    # Confluence data
    confluence_page_id: Optional[str]
    confluence_url: Optional[str]
    
    # Database IDs (set after store_results)
    transcription_id: Optional[int]
    meeting_id: Optional[int]
    task_ids: List[int]
    
    # Processing decisions and status
    decisions: List[str]  # Log of decisions made during processing
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
    Node: Summarize the meeting transcript and extract title/project using LLM.
    Also extracts meeting title and project name for smart page management.
    """
    node_name = "summarize_meeting"
    start_time = time.time()
    log_node_entry(node_name, logger)
    log_step_progress(1, 5, "Summarize Meeting", logger)
    
    state["current_step"] = node_name
    state["decisions"] = state.get("decisions", [])
    log_processing_step(state.get("meeting_id"), node_name, "started")
    
    try:
        llm_client = get_llm_client()
        
        if not llm_client.is_configured:
            raise RuntimeError("LLM client not configured")
        
        # Extract meeting title
        logger.info("Extracting meeting title from transcript...")
        meeting_title = llm_client.extract_meeting_title(state["transcript"])
        state["meeting_title"] = meeting_title
        state["decisions"].append(f"Meeting title: '{meeting_title}'")
        logger.info(f"📌 Meeting Title: {meeting_title}")
        
        # Generate summary
        logger.info("Generating meeting summary...")
        summary = llm_client.summarize_meeting(state["transcript"])
        state["summary"] = summary
        
        # Extract project name for smart Confluence page management
        logger.info("Identifying project/product name...")
        project_name = llm_client.extract_project_name(state["transcript"], summary)
        state["project_name"] = project_name
        
        if project_name:
            state["decisions"].append(f"Project identified: '{project_name}'")
            logger.info(f"📁 Project: {project_name}")
        else:
            state["decisions"].append("No specific project identified - will create new meeting page")
            logger.info("No specific project identified in transcript")
        
        log_processing_step(
            state.get("meeting_id"),
            node_name,
            "completed",
            message=f"Title: {meeting_title}, Summary: {len(summary)} chars"
        )
        
        logger.info(f"Generated summary ({len(summary)} chars)")
        duration_ms = (time.time() - start_time) * 1000
        log_node_exit(node_name, logger, success=True, duration_ms=duration_ms)
        
    except Exception as e:
        error_msg = f"Failed to summarize meeting: {str(e)}"
        logger.error(error_msg)
        state["error"] = error_msg
        log_processing_step(state.get("meeting_id"), node_name, "failed", message=error_msg)
        duration_ms = (time.time() - start_time) * 1000
        log_node_exit(node_name, logger, success=False, duration_ms=duration_ms)
    
    return state


def extract_tasks(state: MeetingState) -> MeetingState:
    """
    Node: Extract action items from the transcript using LLM.
    """
    node_name = "extract_tasks"
    
    if state.get("error"):
        logger.warning(f"Skipping {node_name} due to previous error")
        return state
    
    start_time = time.time()
    log_node_entry(node_name, logger)
    log_step_progress(2, 5, "Extract Tasks", logger)
    
    state["current_step"] = node_name
    log_processing_step(state.get("meeting_id"), node_name, "started")
    
    try:
        llm_client = get_llm_client()
        
        if not llm_client.is_configured:
            raise RuntimeError("LLM client not configured")
        
        logger.info("Calling LLM to extract action items...")
        summary = state.get("summary", "")
        tasks_result = llm_client.extract_tasks(state["transcript"], summary)
        tasks = tasks_result.get("tasks", [])
        state["tasks"] = tasks
        
        log_processing_step(
            state.get("meeting_id"),
            node_name,
            "completed",
            message=f"Extracted {len(tasks)} tasks"
        )
        
        logger.info(f"Extracted {len(tasks)} tasks")
        for i, task in enumerate(tasks, 1):
            logger.info(f"  Task {i}: {task.get('title', 'Untitled')[:50]}...")
            
        duration_ms = (time.time() - start_time) * 1000
        log_node_exit(node_name, logger, success=True, duration_ms=duration_ms)
        
    except Exception as e:
        error_msg = f"Failed to extract tasks: {str(e)}"
        logger.error(error_msg)
        state["error"] = error_msg
        log_processing_step(state.get("meeting_id"), node_name, "failed", message=error_msg)
        duration_ms = (time.time() - start_time) * 1000
        log_node_exit(node_name, logger, success=False, duration_ms=duration_ms)
    
    return state


def create_jira_issues(state: MeetingState) -> MeetingState:
    """
    Node: Create Jira issues for extracted tasks.
    Checks for duplicate tickets before creating new ones.
    """
    node_name = "create_jira_issues"
    
    if state.get("error"):
        logger.warning(f"Skipping {node_name} due to previous error")
        return state
    
    start_time = time.time()
    log_node_entry(node_name, logger)
    log_step_progress(3, 5, "Create Jira Issues", logger)
    
    state["decisions"] = state.get("decisions", [])
    state["skipped_tasks"] = state.get("skipped_tasks", [])
    
    tasks = state.get("tasks", [])
    if not tasks:
        logger.info("No tasks to create Jira issues for")
        state["jira_keys"] = []
        state["decisions"].append("No tasks extracted - skipping Jira issue creation")
        duration_ms = (time.time() - start_time) * 1000
        log_node_exit(node_name, logger, success=True, duration_ms=duration_ms)
        return state
    
    logger.info(f"Processing {len(tasks)} tasks for Jira issue creation")
    state["current_step"] = node_name
    log_processing_step(state.get("meeting_id"), node_name, "started")
    
    jira_keys = []
    skipped_tasks = []
    
    try:
        jira_client = get_jira_client()
        
        if not jira_client.is_configured:
            logger.warning("Jira client not configured, skipping issue creation")
            state["jira_keys"] = []
            state["decisions"].append("Jira not configured - tickets not created")
            log_processing_step(
                state.get("meeting_id"),
                node_name,
                "skipped",
                message="Jira not configured"
            )
            duration_ms = (time.time() - start_time) * 1000
            log_node_exit(node_name, logger, success=True, duration_ms=duration_ms)
            return state
        
        for i, task in enumerate(tasks, 1):
            try:
                title = task.get("title", "Untitled Task")
                description = task.get("description", "")
                assignee = task.get("assignee")
                priority = task.get("priority", "Medium")
                
                # Check for duplicate ticket
                logger.info(f"[Task {i}/{len(tasks)}] Checking for duplicates: '{title[:50]}...'")
                existing_key = jira_client.check_for_duplicate(title, assignee)
                
                if existing_key:
                    # Duplicate found - skip creation
                    decision = f"SKIPPED task '{title[:40]}...' - similar ticket exists: {existing_key}"
                    state["decisions"].append(decision)
                    logger.warning(f"⚠️  {decision}")
                    skipped_tasks.append({
                        **task,
                        "skipped_reason": f"Duplicate of {existing_key}",
                        "existing_key": existing_key
                    })
                    continue
                
                # No duplicate found - create new issue
                full_description = f"{description}\n\n---\nExtracted from meeting recording"
                if state.get("meeting_title"):
                    full_description += f"\nMeeting: {state['meeting_title']}"
                if state.get("filename"):
                    full_description += f"\nSource file: {state['filename']}"
                
                logger.info(f"[Task {i}/{len(tasks)}] Creating new Jira issue...")
                issue_key = jira_client.create_issue(
                    summary=title,
                    description=full_description,
                    issue_type="Task",
                    assignee_name=assignee,
                    priority=priority
                )
                
                if issue_key:
                    jira_keys.append(issue_key)
                    decision = f"CREATED Jira issue {issue_key}: '{title[:40]}...'"
                    state["decisions"].append(decision)
                    logger.info(f"✅ {decision}")
                    
            except Exception as e:
                logger.error(f"Failed to create Jira issue for task '{task.get('title')}': {e}")
        
        state["jira_keys"] = jira_keys
        state["skipped_tasks"] = skipped_tasks
        
        # Summary log
        logger.info(f"═══ Jira Summary: {len(jira_keys)} created, {len(skipped_tasks)} skipped (duplicates) ═══")
        
        log_processing_step(
            state.get("meeting_id"),
            node_name,
            "completed",
            message=f"Created {len(jira_keys)} issues, skipped {len(skipped_tasks)} duplicates"
        )
        
        logger.info(f"Successfully created {len(jira_keys)} Jira issues")
        duration_ms = (time.time() - start_time) * 1000
        log_node_exit(node_name, logger, success=True, duration_ms=duration_ms)
        
    except Exception as e:
        error_msg = f"Failed in Jira issue creation: {str(e)}"
        logger.error(error_msg)
        state["jira_keys"] = jira_keys
        state["skipped_tasks"] = skipped_tasks
        log_processing_step(
            state.get("meeting_id"),
            node_name,
            "failed",
            message=error_msg
        )
        duration_ms = (time.time() - start_time) * 1000
        log_node_exit(node_name, logger, success=False, duration_ms=duration_ms)
    
    return state


def update_confluence_page(state: MeetingState) -> MeetingState:
    """
    Node: Create or update Confluence page with meeting notes.
    Uses project name to find and update existing project pages.
    Runs after Jira issues are created so we can include Jira links.
    """
    node_name = "update_confluence_page"
    start_time = time.time()
    log_node_entry(node_name, logger)
    log_step_progress(4, 5, "Update Confluence Page", logger)
    
    state["current_step"] = node_name
    state["decisions"] = state.get("decisions", [])
    log_processing_step(state.get("meeting_id"), node_name, "started")
    
    try:
        confluence_client = get_confluence_client()
        
        if not confluence_client.is_configured:
            logger.warning("Confluence client not configured, skipping page creation")
            state["decisions"].append("Confluence not configured - page not created")
            log_processing_step(
                state.get("meeting_id"),
                node_name,
                "skipped",
                message="Confluence not configured"
            )
            duration_ms = (time.time() - start_time) * 1000
            log_node_exit(node_name, logger, success=True, duration_ms=duration_ms)
            return state
        
        logger.info("Building Confluence page content...")
        
        # Use extracted meeting title or build from filename
        meeting_date = state.get("meeting_date", date.today().isoformat())
        meeting_title = state.get("meeting_title")
        project_name = state.get("project_name")
        filename = state.get("filename", "Meeting")
        
        # Build page title based on whether we have a project
        if meeting_title:
            title = f"{meeting_title} - {meeting_date}"
        else:
            title = f"Meeting Notes - {filename.split('.')[0] if filename else 'Meeting'} - {meeting_date}"
        
        logger.info(f"📄 Page title: {title}")
        
        # Prepare action items with Jira links
        action_items = []
        tasks = state.get("tasks", [])
        jira_keys = state.get("jira_keys", [])
        skipped_tasks = state.get("skipped_tasks", [])
        
        # Include created tasks with Jira links
        jira_key_idx = 0
        for task in tasks:
            # Check if this task was skipped
            skipped_info = next((s for s in skipped_tasks if s.get("title") == task.get("title")), None)
            
            item = {
                "description": task.get("title") or task.get("description", "Task"),
                "assignee": task.get("assignee", "Unassigned")
            }
            
            if skipped_info:
                # Task was skipped - show existing ticket
                item["jira_key"] = skipped_info.get("existing_key")
                item["status"] = "existing"
            elif jira_key_idx < len(jira_keys):
                # New ticket created
                item["jira_key"] = jira_keys[jira_key_idx]
                item["status"] = "new"
                jira_key_idx += 1
            
            action_items.append(item)
        
        # Extract key points and decisions from summary
        summary = state.get("summary", "No summary available.")
        key_points = []
        decisions_list = []
        
        if summary:
            lines = summary.split('\n')
            for line in lines:
                line = line.strip()
                if line.startswith('- ') or line.startswith('* '):
                    key_points.append(line[2:])
        
        # Build HTML content
        html = build_meeting_page_html(
            title=title,
            meeting_date=meeting_date,
            summary=summary or "No summary available.",
            key_points=key_points if key_points else None,
            decisions=decisions_list if decisions_list else None,
            action_items=action_items if action_items else None,
            transcript=state.get("transcript"),
            jira_base_url=settings.jira_server
        )
        
        # Use project-aware page creation if project name is available
        if project_name:
            logger.info(f"📁 Looking for existing project page: {project_name}")
            result = confluence_client.create_or_update_project_page(project_name, title, html)
        else:
            logger.info("No project identified - creating standard meeting page")
            result = confluence_client.create_or_update_page(title, html)
        
        action = "processed"
        if result:
            state["confluence_page_id"] = result.get("page_id")
            state["confluence_url"] = result.get("page_url")
            action = result.get("action", "created")
            
            # Log the decision
            decision = result.get("decision", f"Page {action}")
            state["decisions"].append(decision)
            
            # Log decision details
            decision_log = result.get("decision_log", [])
            for log_item in decision_log:
                logger.info(f"  💡 {log_item}")
            
            logger.info(f"Confluence page {action}: {result.get('page_url')}")
        else:
            logger.warning("Confluence returned no result")
            state["decisions"].append("Confluence page creation failed")
        
        log_processing_step(
            state.get("meeting_id"),
            node_name,
            "completed",
            message=f"Page {action}: {state.get('confluence_url')}"
        )
        
        duration_ms = (time.time() - start_time) * 1000
        log_node_exit(node_name, logger, success=True, duration_ms=duration_ms)
        
    except Exception as e:
        error_msg = f"Failed to create/update Confluence page: {str(e)}"
        logger.error(error_msg)
        # Don't set error state - Confluence failure should not halt pipeline
        log_processing_step(
            state.get("meeting_id"),
            node_name,
            "failed",
            message=error_msg
        )
        duration_ms = (time.time() - start_time) * 1000
        log_node_exit(node_name, logger, success=False, duration_ms=duration_ms)
    
    return state


def store_results(state: MeetingState) -> MeetingState:
    """
    Node: Store processing results in the database.
    Creates Transcription, Meeting, and Task records according to schema.
    """
    node_name = "store_results"
    start_time = time.time()
    log_node_entry(node_name, logger)
    log_step_progress(5, 5, "Store Results", logger)
    
    state["current_step"] = node_name
    log_processing_step(state.get("meeting_id"), node_name, "started")
    
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
            
            # 3. Create Meeting record linked to transcription (with Confluence info)
            meeting = Meeting(
                meeting_date=meeting_date_val,
                transcription_id=transcription.transcription_id,
                confluence_page_id=state.get("confluence_page_id"),
                confluence_url=state.get("confluence_url")
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
        
        log_processing_step(state["meeting_id"], node_name, "completed")  # type: ignore[arg-type]
        duration_ms = (time.time() - start_time) * 1000
        log_node_exit(node_name, logger, success=True, duration_ms=duration_ms)
        
    except Exception as e:
        error_msg = f"Failed to store results: {str(e)}"
        logger.error(error_msg)
        state["error"] = error_msg
        log_processing_step(state.get("meeting_id"), node_name, "failed", message=error_msg)
        duration_ms = (time.time() - start_time) * 1000
        log_node_exit(node_name, logger, success=False, duration_ms=duration_ms)
    
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
    4. update_confluence_page - Create/update Confluence meeting notes
    5. store_results - Store in database (transcription, meeting, tasks, confluence)
    """
    workflow = StateGraph(MeetingState)
    
    # Add nodes
    workflow.add_node("summarize_meeting", summarize_meeting)
    workflow.add_node("extract_tasks", extract_tasks)
    workflow.add_node("create_jira_issues", create_jira_issues)
    workflow.add_node("update_confluence_page", update_confluence_page)
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
        {"continue": "update_confluence_page", "end": "store_results"}
    )
    
    # Confluence node always proceeds to store_results
    workflow.add_edge("update_confluence_page", "store_results")
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
    pipeline_start_time = time.time()
    
    log_pipeline_start("Meeting Processing", logger, context={
        "filename": filename or "meeting",
        "meeting_date": meeting_date or date.today().isoformat(),
        "transcript_length": f"{len(transcript)} chars"
    })
    
    initial_state: MeetingState = {
        "meeting_date": meeting_date or date.today().isoformat(),
        "transcript": transcript,
        "filename": filename,
        "file_path": file_path,
        "meeting_title": None,
        "project_name": None,
        "summary": None,
        "tasks": [],
        "jira_keys": [],
        "skipped_tasks": [],
        "confluence_page_id": None,
        "confluence_url": None,
        "transcription_id": None,
        "meeting_id": None,
        "task_ids": [],
        "decisions": [],
        "error": None,
        "current_step": "initialized"
    }
    
    try:
        pipeline = get_pipeline()
        result = pipeline.invoke(initial_state)
        
        duration_ms = (time.time() - pipeline_start_time) * 1000
        success = result.get("error") is None
        
        log_pipeline_end("Meeting Processing", logger, success=success, duration_ms=duration_ms, results={
            "meeting_id": result.get("meeting_id"),
            "meeting_title": result.get("meeting_title", "N/A"),
            "tasks_extracted": len(result.get("tasks", [])),
            "jira_issues": len(result.get("jira_keys", [])),
            "skipped_duplicates": len(result.get("skipped_tasks", [])),
            "confluence_url": result.get("confluence_url", "N/A")
        })
        
        # Log decisions made
        decisions = result.get("decisions", [])
        if decisions:
            logger.info("═══ Decisions Made During Processing ═══")
            for decision in decisions:
                logger.info(f"  ▸ {decision}")
        
        return dict(result)
        
    except Exception as e:
        duration_ms = (time.time() - pipeline_start_time) * 1000
        logger.error(f"Pipeline execution failed: {e}")
        
        log_pipeline_end("Meeting Processing", logger, success=False, duration_ms=duration_ms, results={
            "error": str(e)
        })
        
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
