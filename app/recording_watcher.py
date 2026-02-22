"""
Recording folder watcher and processor.
Detects new recording files and triggers the transcription + processing pipeline.
Includes full workflow: Transcription → LLM Task Extraction → Jira Ticket Creation → Confluence → DB Storage
"""
import logging
import os
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional, Set, List, Dict, Any
from threading import Thread, Lock
import time

from app.config import settings
from app.transcriber import transcribe_file, is_transcriber_ready
from app.llm import get_llm_client
from app.jira_client import get_jira_client
from app.confluence_client import get_confluence_client, build_simple_meeting_page
from app.db import get_db_session
from app.models import Task, Member, Transcription, Meeting
from app.date_utils import parse_due_date, format_date_iso, get_default_deadline
from app.member_matching import get_member_name, match_member_name
from app.task_extractor import safe_extract_tasks, validate_and_normalize_task

logger = logging.getLogger(__name__)

# Supported file extensions (including Google Meet formats)
SUPPORTED_EXTENSIONS = {'.mp4', '.mp3', '.wav', '.m4a', '.mpeg', '.webm', '.mkv'}

# Track processed files in memory to avoid duplicates within a session
_processed_files: Set[str] = set()

# Track files currently being processed to prevent concurrent processing
_currently_processing: Set[str] = set()

# Watcher thread reference
_watcher_thread: Optional[Thread] = None
_watcher_running: bool = False

# Processing lock to prevent scheduler overlap
_processing_lock = Lock()
_is_processing: bool = False


def get_recordings_dir() -> Path:
    """
    Get the recordings directory path.
    
    Returns:
        Path to recordings directory
    """
    recordings_dir = getattr(settings, 'recordings_dir', './recordings')
    path = Path(recordings_dir)
    
    # Create directory if it doesn't exist
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
        logger.info(f"Created recordings directory: {path}")
    
    return path


def is_supported_file(filename: str) -> bool:
    """
    Check if a file has a supported extension.
    
    Args:
        filename: Name of the file
        
    Returns:
        True if file extension is supported
    """
    return Path(filename).suffix.lower() in SUPPORTED_EXTENSIONS


def get_file_id(file_path: str) -> str:
    """
    Generate a unique ID for a file based on its path and modification time.
    
    Args:
        file_path: Path to the file
        
    Returns:
        Unique identifier string
    """
    path = Path(file_path)
    return f"{path.name}_{path.stat().st_mtime}"


def is_file_processed(filename: str) -> bool:
    """
    Check if a recording file has already been processed.
    Uses in-memory cache only since the new schema doesn't track filename.
    
    Args:
        filename: Name of the recording file
        
    Returns:
        True if already processed
    """
    return filename in _processed_files


def scan_for_new_recordings() -> List[Path]:
    """
    Scan the recordings directory for new, unprocessed files.
    
    Returns:
        List of paths to new recording files
    """
    recordings_dir = get_recordings_dir()
    new_files = []
    
    try:
        for file_path in recordings_dir.iterdir():
            if not file_path.is_file():
                continue
            
            if not is_supported_file(file_path.name):
                continue
            
            if is_file_processed(file_path.name):
                logger.debug(f"File already processed: {file_path.name}")
                continue
            
            new_files.append(file_path)
            logger.debug(f"Found new recording: {file_path.name}")
        
        return new_files
        
    except Exception as e:
        logger.error(f"Error scanning recordings directory: {e}")
        return []


def process_recording_file(file_path: Path) -> Dict[str, Any]:
    """
    Process a single recording file through the full pipeline:
    Transcription → LLM Task Extraction → Jira Ticket Creation → Confluence → DB Storage
    
    Args:
        file_path: Path to the recording file
        
    Returns:
        Processing result dictionary
    """
    filename = file_path.name
    
    # Prevent duplicate concurrent processing
    if filename in _currently_processing:
        logger.info(f"⏭️ Skipping already-processing file: {filename}")
        return {
            "filename": filename,
            "error": "Already being processed",
            "processed": False
        }
    
    # Add to processing set
    _currently_processing.add(filename)
    
    logger.info(f"🎬 Processing recording: {filename}")
    
    result = {
        "filename": filename,
        "transcript": None,
        "summary": None,
        "tasks": [],
        "jira_tickets": [],
        "db_tasks": [],
        "confluence_page_id": None,
        "confluence_url": None,
        "meeting_id": None,
        "error": None,
        "processed": False
    }
    
    try:
        meeting_date_str = date.today().isoformat()
        summary = None
        tasks = []
        transcript = None
        
        # Step 1: Transcribe the recording
        try:
            logger.info(f"📝 Step 1: Transcribing {filename}...")
            
            if not is_transcriber_ready():
                raise RuntimeError("Transcriber not available (faster-whisper not installed)")
            
            transcript = transcribe_file(str(file_path))
            
            if not transcript:
                raise ValueError("Transcription returned empty result")
            
            result["transcript"] = transcript
            logger.info(f"✅ Transcription complete: {len(transcript)} characters")
            
        except Exception as e:
            logger.error(f"❌ Transcription error: {e}")
            result["error"] = f"Transcription failed: {e}"
            _processed_files.add(filename)  # Mark as processed to avoid retry loop
            return result
        
        # Step 2: Extract tasks using LLM
        try:
            logger.info(f"🤖 Step 2: Extracting tasks via LLM...")
            
            llm = get_llm_client()
            if not llm.is_configured:
                logger.warning("LLM not configured, skipping task extraction")
            else:
                # Get summary
                try:
                    summary = llm.summarize_meeting(transcript)
                    result["summary"] = summary
                    logger.info(f"📋 Summary: {summary[:100]}...")
                except Exception as sum_err:
                    logger.error(f"❌ Summary error: {sum_err}")
                    summary = transcript[:500]
                    result["summary"] = summary
                
                # Extract tasks with safe extraction
                try:
                    llm_response = None
                    try:
                        tasks_result = llm.extract_tasks(transcript, summary)
                        # Get raw response for safe extraction
                        llm_response = str(tasks_result) if tasks_result else None
                    except Exception:
                        llm_response = None
                    
                    # Use safe extraction with fallbacks
                    extraction_result = safe_extract_tasks(
                        transcript=transcript,
                        summary=summary,
                        llm_response=llm_response
                    )
                    tasks = extraction_result.get("tasks", [])
                    result["tasks"] = tasks
                    logger.info(f"📋 Extracted {len(tasks)} task(s) via {extraction_result.get('extraction_method', 'unknown')}")
                    
                except Exception as task_err:
                    logger.error(f"❌ Task extraction error: {task_err}")
                    tasks = []
        
        except Exception as e:
            logger.error(f"❌ LLM step error: {e}")
            # Continue with empty tasks
        
        # Step 3: Create Jira tickets
        action_items = []  # For Confluence
        try:
            logger.info(f"🎫 Step 3: Creating Jira tickets...")
            
            jira = get_jira_client()
            
            for task in tasks:
                task_desc = task.get("description") or task.get("title", "Untitled Task")
                raw_assignee = task.get("assignee")
                due_date_str = task.get("due_date")
                
                # Map assignee using improved matching
                matched_assignee = None
                match_result = match_member_name(raw_assignee) if raw_assignee else None
                if match_result:
                    matched_assignee = match_result[0]
                    logger.info(f"👤 Mapped '{raw_assignee}' -> '{matched_assignee}' (score: {match_result[1]:.2f})")
                
                # Parse due date with natural language support
                parsed_due_date = parse_due_date(due_date_str) if due_date_str else None
                due_date_iso = format_date_iso(parsed_due_date) if parsed_due_date else None
                
                if due_date_str and parsed_due_date:
                    logger.info(f"📅 Parsed date '{due_date_str}' -> '{due_date_iso}'")
                
                # Create Jira ticket
                jira_key = None
                if jira.is_configured:
                    try:
                        description = f"Task extracted from meeting recording: {filename}\n\nAssignee mentioned: {raw_assignee or 'Unassigned'}"
                        
                        jira_result = jira.create_issue(
                            summary=task_desc[:255],
                            description=description,
                            issue_type="Task",
                            assignee_name=matched_assignee,
                            due_date=due_date_iso
                        )
                        
                        jira_key = jira_result.get("key") if isinstance(jira_result, dict) else jira_result
                        if jira_key:
                            logger.info(f"✅ Created Jira ticket: {jira_key}")
                            result["jira_tickets"].append(jira_key)
                    except Exception as jira_err:
                        logger.error(f"❌ Jira error for task '{task_desc[:50]}': {jira_err}")
                else:
                    logger.warning("Jira not configured, skipping ticket creation")
                
                # Collect for Confluence
                action_items.append({
                    "jira_key": jira_key,
                    "description": task_desc,
                    "assignee": matched_assignee or raw_assignee or "Unassigned"
                })
                
                # Store task in database
                try:
                    with get_db_session() as db:
                        member = None
                        if matched_assignee:
                            member = db.query(Member).filter(
                                Member.member_name.ilike(f"%{matched_assignee}%")
                            ).first()
                        
                        if member:
                            deadline = parsed_due_date or get_default_deadline(7)
                            
                            task_desc_with_jira = task_desc
                            if jira_key:
                                task_desc_with_jira += f" [Jira: {jira_key}]"
                            
                            db_task = Task(
                                member_id=member.member_id,
                                description=task_desc_with_jira,
                                deadline=deadline
                            )
                            db.add(db_task)
                            db.commit()
                            
                            result["db_tasks"].append({
                                "task_id": db_task.task_id,
                                "member": member.member_name
                            })
                            logger.info(f"📊 Stored in DB: task_id={db_task.task_id}")
                        else:
                            if matched_assignee:
                                logger.warning(f"⚠️ No DB member found for '{matched_assignee}'")
                except Exception as db_err:
                    logger.error(f"❌ DB error storing task: {db_err}")
            
        except Exception as e:
            logger.error(f"❌ Jira step error: {e}")
            # Continue to Confluence and DB
        
        # Step 4: Create/Update Confluence page
        try:
            logger.info(f"📄 Step 4: Creating Confluence page...")
            
            confluence = get_confluence_client()
            
            if confluence.is_configured:
                # Build page HTML
                page_html = build_simple_meeting_page(
                    meeting_date=meeting_date_str,
                    summary=summary or "No summary available",
                    action_items=action_items if action_items else None,
                    transcript=transcript,
                    jira_base_url=settings.jira_server
                )
                
                # Create/update page
                page_title = f"Meeting {meeting_date_str}"
                try:
                    confluence_result = confluence.create_or_update_page(page_title, page_html)
                    if confluence_result:
                        result["confluence_page_id"] = confluence_result.get("page_id")
                        result["confluence_url"] = confluence_result.get("page_url")
                        action = confluence_result.get("action", "created")
                        logger.info(f"✅ Confluence page {action}: {result['confluence_url']}")
                    else:
                        logger.warning("⚠️ Confluence page creation returned None")
                except Exception as conf_err:
                    logger.error(f"❌ Confluence error: {conf_err}")
            else:
                logger.warning("Confluence not configured, skipping page creation")
                
        except Exception as e:
            logger.error(f"❌ Confluence step error: {e}")
        
        # Step 5: Store transcription and meeting record
        try:
            logger.info(f"💾 Step 5: Storing meeting record...")
            
            with get_db_session() as db:
                # Create transcription
                transcription_record = Transcription(transcription_summary=summary or (transcript[:500] if transcript else ""))
                db.add(transcription_record)
                db.flush()
                
                # Create meeting with Confluence info
                meeting = Meeting(
                    meeting_date=date.today(),
                    transcription_id=transcription_record.transcription_id,
                    confluence_page_id=result.get("confluence_page_id"),
                    confluence_url=result.get("confluence_url")
                )
                db.add(meeting)
                db.commit()
                
                result["meeting_id"] = meeting.meeting_id
                logger.info(f"📊 Created meeting record: meeting_id={meeting.meeting_id}")
                
        except Exception as db_err:
            logger.error(f"❌ Failed to store meeting record: {db_err}")
        
        # Mark as processed
        _processed_files.add(filename)
        result["processed"] = True
        logger.info(f"✅ Successfully processed: {filename}")
        
        return result
        
    finally:
        # Always remove from processing set
        _currently_processing.discard(filename)


def poll_and_process_recordings() -> Dict[str, Any]:
    """
    Poll for new recordings and process them.
    This is the main polling function called by the scheduler.
    Uses a lock to prevent overlapping processing when jobs run longer than interval.
    
    Returns:
        Summary of processing results
    """
    global _is_processing
    
    results = {
        "processed": 0,
        "skipped": 0,
        "errors": 0,
        "files": []
    }
    
    # Try to acquire lock (non-blocking)
    if not _processing_lock.acquire(blocking=False):
        logger.warning("⏭️ Skipping poll cycle - previous processing still in progress")
        results["skipped"] = 1
        return results
    
    try:
        _is_processing = True
        logger.info("🔄 Starting recording poll cycle...")
        
        # Scan for new files
        new_files = scan_for_new_recordings()
        
        if not new_files:
            logger.debug("No new recordings found")
            return results
        
        logger.info(f"Found {len(new_files)} new recording(s)")
        
        # Process each new file
        for file_path in new_files:
            try:
                result = process_recording_file(file_path)
                
                if result.get('error'):
                    results["errors"] += 1
                else:
                    results["processed"] += 1
                
                results["files"].append({
                    "filename": file_path.name,
                    "success": result.get('error') is None,
                    "jira_tickets": result.get('jira_tickets', []),
                    "confluence_url": result.get('confluence_url'),
                    "error": result.get('error')
                })
                
            except Exception as e:
                logger.error(f"Failed to process {file_path.name}: {e}")
                results["errors"] += 1
                results["files"].append({
                    "filename": file_path.name,
                    "success": False,
                    "error": str(e)
                })
        
        logger.info(
            f"✅ Poll cycle complete. "
            f"Processed: {results['processed']}, "
            f"Errors: {results['errors']}"
        )
        
    except Exception as e:
        logger.error(f"Error during poll cycle: {e}")
    finally:
        _is_processing = False
        _processing_lock.release()
    
    return results


def clear_processed_cache() -> int:
    """
    Clear the in-memory processed files cache.
    
    Returns:
        Number of entries cleared
    """
    global _processed_files
    count = len(_processed_files)
    _processed_files = set()
    logger.info(f"Cleared processed files cache ({count} entries)")
    return count


def get_recordings_status() -> Dict[str, Any]:
    """
    Get status information about the recordings directory.
    
    Returns:
        Dictionary with status information
    """
    recordings_dir = get_recordings_dir()
    
    total_files = 0
    processed_files = 0
    pending_files = 0
    
    try:
        for file_path in recordings_dir.iterdir():
            if file_path.is_file() and is_supported_file(file_path.name):
                total_files += 1
                if is_file_processed(file_path.name):
                    processed_files += 1
                else:
                    pending_files += 1
    except Exception as e:
        logger.error(f"Error getting recordings status: {e}")
    
    return {
        "recordings_dir": str(recordings_dir),
        "total_files": total_files,
        "processed_files": processed_files,
        "pending_files": pending_files,
        "supported_extensions": list(SUPPORTED_EXTENSIONS),
        "cache_size": len(_processed_files)
    }


def list_recordings(include_processed: bool = True) -> List[Dict[str, Any]]:
    """
    List all recording files in the directory.
    
    Args:
        include_processed: Whether to include already processed files
        
    Returns:
        List of recording file information
    """
    recordings_dir = get_recordings_dir()
    recordings = []
    
    try:
        for file_path in sorted(recordings_dir.iterdir()):
            if not file_path.is_file():
                continue
            
            if not is_supported_file(file_path.name):
                continue
            
            is_processed = is_file_processed(file_path.name)
            
            if not include_processed and is_processed:
                continue
            
            stat = file_path.stat()
            recordings.append({
                "filename": file_path.name,
                "path": str(file_path),
                "size_bytes": stat.st_size,
                "size_mb": round(stat.st_size / (1024 * 1024), 2),
                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "processed": is_processed
            })
        
        return recordings
        
    except Exception as e:
        logger.error(f"Error listing recordings: {e}")
        return []


# =============================================================================
# Real-time File Watcher
# =============================================================================

def _watcher_loop(poll_interval: int = 5):
    """
    Internal watcher loop that continuously polls for new recordings.
    
    Args:
        poll_interval: Seconds between polls
    """
    global _watcher_running
    
    logger.info(f"🔍 Watcher started. Polling every {poll_interval} seconds...")
    logger.info(f"📁 Watching directory: {get_recordings_dir()}")
    
    while _watcher_running:
        try:
            # Scan for new files
            new_files = scan_for_new_recordings()
            
            if new_files:
                logger.info(f"🆕 Found {len(new_files)} new recording(s)")
                
                for file_path in new_files:
                    logger.info(f"\n{'='*60}")
                    logger.info(f"🎬 NEW RECORDING DETECTED: {file_path.name}")
                    logger.info(f"{'='*60}")
                    
                    # Process the file
                    result = process_recording_file(file_path)
                    
                    if result.get("processed"):
                        logger.info(f"✅ Processing complete for {file_path.name}")
                        if result.get("jira_tickets"):
                            logger.info(f"🎫 Created Jira tickets: {', '.join(result['jira_tickets'])}")
                    else:
                        logger.error(f"❌ Processing failed: {result.get('error', 'Unknown error')}")
            
            # Wait before next poll
            time.sleep(poll_interval)
            
        except Exception as e:
            logger.error(f"❌ Watcher error: {e}")
            time.sleep(poll_interval)
    
    logger.info("🛑 Watcher stopped")


def start_watcher(poll_interval: int = 5) -> bool:
    """
    Start the real-time file watcher in a background thread.
    
    Args:
        poll_interval: Seconds between directory polls
        
    Returns:
        True if watcher started successfully
    """
    global _watcher_thread, _watcher_running
    
    if _watcher_running:
        logger.warning("Watcher is already running")
        return False
    
    _watcher_running = True
    _watcher_thread = Thread(target=_watcher_loop, args=(poll_interval,), daemon=True)
    _watcher_thread.start()
    
    logger.info(f"✅ File watcher started (poll interval: {poll_interval}s)")
    return True


def stop_watcher() -> bool:
    """
    Stop the real-time file watcher.
    
    Returns:
        True if watcher stopped successfully
    """
    global _watcher_running, _watcher_thread
    
    if not _watcher_running:
        logger.warning("Watcher is not running")
        return False
    
    _watcher_running = False
    
    if _watcher_thread:
        _watcher_thread.join(timeout=10)
        _watcher_thread = None
    
    logger.info("✅ File watcher stopped")
    return True


def is_watcher_running() -> bool:
    """Check if the watcher is currently running."""
    return _watcher_running


def get_watcher_status() -> Dict[str, Any]:
    """
    Get the current status of the file watcher.
    
    Returns:
        Dictionary with watcher status information
    """
    return {
        "running": _watcher_running,
        "recordings_dir": str(get_recordings_dir()),
        "supported_extensions": list(SUPPORTED_EXTENSIONS),
        "processed_files_count": len(_processed_files),
        "pending_files": len(scan_for_new_recordings())
    }


# =============================================================================
# CLI Entry Point
# =============================================================================

def run_watcher_cli():
    """
    Run the file watcher from command line.
    This is a blocking call that runs until interrupted.
    """
    import signal
    
    print("\n" + "🚀"*30)
    print("  WORKFLOW AUTOMATION - FILE WATCHER")
    print("🚀"*30)
    print(f"\n📁 Watching directory: {get_recordings_dir()}")
    print(f"📋 Supported formats: {', '.join(SUPPORTED_EXTENSIONS)}")
    print("\n⏳ Waiting for new recordings... (Press Ctrl+C to stop)\n")
    
    # Handle graceful shutdown
    def signal_handler(sig, frame):
        print("\n\n🛑 Shutting down watcher...")
        stop_watcher()
        print("👋 Goodbye!")
        exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    
    # Start watcher in main thread (blocking)
    global _watcher_running
    _watcher_running = True
    _watcher_loop(poll_interval=5)


if __name__ == "__main__":
    run_watcher_cli()
