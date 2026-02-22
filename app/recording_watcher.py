"""
Recording folder watcher and processor.
Detects new recording files and triggers the transcription + processing pipeline.
Includes full workflow: Transcription → LLM Task Extraction → Jira Ticket Creation → DB Storage
"""
import logging
import os
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional, Set, List, Dict, Any
from threading import Thread
import time

from app.config import settings
from app.transcriber import transcribe_file, is_transcriber_ready
from app.llm import get_llm_client
from app.jira_client import get_jira_client, find_closest_team_member
from app.db import get_db_session
from app.models import Task, Member, Transcription, Meeting

logger = logging.getLogger(__name__)

# Supported file extensions (including Google Meet formats)
SUPPORTED_EXTENSIONS = {'.mp4', '.mp3', '.wav', '.m4a', '.mpeg', '.webm', '.mkv'}

# Track processed files in memory to avoid duplicates within a session
_processed_files: Set[str] = set()

# Watcher thread reference
_watcher_thread: Optional[Thread] = None
_watcher_running: bool = False


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
    Transcription → LLM Task Extraction → Jira Ticket Creation → DB Storage
    
    Args:
        file_path: Path to the recording file
        
    Returns:
        Processing result dictionary
    """
    filename = file_path.name
    logger.info(f"🎬 Processing recording: {filename}")
    
    result = {
        "filename": filename,
        "transcript": None,
        "summary": None,
        "tasks": [],
        "jira_tickets": [],
        "db_tasks": [],
        "error": None,
        "processed": False
    }
    
    try:
        # Step 1: Transcribe the recording
        logger.info(f"📝 Step 1: Transcribing {filename}...")
        
        if not is_transcriber_ready():
            raise RuntimeError("Transcriber not available (faster-whisper not installed)")
        
        transcript = transcribe_file(str(file_path))
        
        if not transcript:
            raise ValueError("Transcription returned empty result")
        
        result["transcript"] = transcript
        logger.info(f"✅ Transcription complete: {len(transcript)} characters")
        
        # Step 2: Extract tasks using LLM
        logger.info(f"🤖 Step 2: Extracting tasks via LLM...")
        
        llm = get_llm_client()
        if not llm.is_configured:
            logger.warning("LLM not configured, skipping task extraction")
            result["error"] = "LLM not configured"
            return result
        
        # Get summary
        summary = llm.summarize_meeting(transcript)
        result["summary"] = summary
        logger.info(f"📋 Summary: {summary[:100]}...")
        
        # Extract tasks
        tasks_result = llm.extract_tasks(transcript, summary)
        tasks = tasks_result.get("tasks", [])
        result["tasks"] = tasks
        logger.info(f"📋 Extracted {len(tasks)} task(s)")
        
        # Step 3: Create Jira tickets and store in DB
        logger.info(f"🎫 Step 3: Creating Jira tickets and storing in DB...")
        
        jira = get_jira_client()
        
        for task in tasks:
            task_title = task.get("title", "Untitled Task")
            raw_assignee = task.get("assignee")
            due_date_str = task.get("due_date")
            
            # Map assignee to closest team member
            matched_assignee = find_closest_team_member(raw_assignee) if raw_assignee else None
            
            if matched_assignee:
                logger.info(f"👤 Mapped '{raw_assignee}' -> '{matched_assignee}'")
            
            # Create Jira ticket
            jira_key = None
            if jira.is_configured:
                try:
                    description = f"Task extracted from meeting recording: {filename}\n\nAssignee mentioned: {raw_assignee or 'Unassigned'}"
                    
                    jira_result = jira.create_issue(
                        summary=task_title,
                        description=description,
                        issue_type="Task",
                        assignee_name=matched_assignee,
                        due_date=due_date_str if due_date_str not in ['null', 'None', None] else None
                    )
                    
                    jira_key = jira_result.get("key")
                    if jira_key:
                        logger.info(f"✅ Created Jira ticket: {jira_key}")
                        result["jira_tickets"].append(jira_key)
                except Exception as jira_err:
                    logger.error(f"❌ Jira error: {jira_err}")
            else:
                logger.warning("Jira not configured, skipping ticket creation")
            
            # Store task in database
            try:
                with get_db_session() as db:
                    # Find member by name
                    member = None
                    if matched_assignee:
                        member = db.query(Member).filter(
                            Member.member_name.ilike(f"%{matched_assignee}%")
                        ).first()
                    
                    if member:
                        # Parse deadline
                        if due_date_str and due_date_str not in ['null', 'None', None]:
                            try:
                                deadline = date.fromisoformat(due_date_str)
                            except ValueError:
                                deadline = date.today() + timedelta(days=7)
                        else:
                            deadline = date.today() + timedelta(days=7)
                        
                        # Create Task record
                        task_desc = f"{task_title}"
                        if jira_key:
                            task_desc += f" [Jira: {jira_key}]"
                        
                        db_task = Task(
                            member_id=member.member_id,
                            description=task_desc,
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
                        logger.warning(f"⚠️ No matching member for '{matched_assignee}'")
            except Exception as db_err:
                logger.error(f"❌ DB error: {db_err}")
        
        # Store transcription and meeting record
        try:
            with get_db_session() as db:
                # Create transcription
                transcription = Transcription(transcription_summary=summary or transcript[:500])
                db.add(transcription)
                db.flush()
                
                # Create meeting
                meeting = Meeting(
                    meeting_date=date.today(),
                    transcription_id=transcription.transcription_id
                )
                db.add(meeting)
                db.commit()
                logger.info(f"📊 Created meeting record: meeting_id={meeting.meeting_id}")
        except Exception as db_err:
            logger.error(f"❌ Failed to store meeting record: {db_err}")
        
        # Mark as processed
        _processed_files.add(filename)
        result["processed"] = True
        logger.info(f"✅ Successfully processed: {filename}")
        
        return result
        
    except Exception as e:
        logger.error(f"❌ Error processing {filename}: {e}")
        result["error"] = str(e)
        return result


def poll_and_process_recordings() -> Dict[str, Any]:
    """
    Poll for new recordings and process them.
    This is the main polling function called by the scheduler.
    
    Returns:
        Summary of processing results
    """
    logger.info("Starting recording poll cycle...")
    
    results = {
        "processed": 0,
        "skipped": 0,
        "errors": 0,
        "files": []
    }
    
    try:
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
            f"Poll cycle complete. "
            f"Processed: {results['processed']}, "
            f"Errors: {results['errors']}"
        )
        
    except Exception as e:
        logger.error(f"Error during poll cycle: {e}")
    
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
