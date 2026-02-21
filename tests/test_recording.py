"""
Test script for processing a custom recording file.
Transcribes audio, extracts tasks via LLM, and creates Jira tickets.

Usage:
    python -m tests.test_recording <path_to_audio_file>
    
Example:
    python -m tests.test_recording ./recordings/meeting.mp3
"""
import sys
import os
import argparse
import logging
from pathlib import Path
from datetime import date, timedelta

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.config import settings
from app.transcriber import transcribe_file, is_transcriber_ready
from app.llm import get_llm_client
from app.jira_client import get_jira_client, find_closest_team_member
from app.db import get_db_session
from app.models import Task, Member

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_transcription(file_path: str) -> str:
    """Transcribe the audio file and return the transcript."""
    print("\n" + "="*60)
    print("STEP 1: TRANSCRIPTION")
    print("="*60)
    
    if not is_transcriber_ready():
        print("⚠️  Transcriber not available. Using mock transcript for testing.")
        # Return a mock transcript for testing without whisper
        return """
        John said we need to finish the API documentation by Friday.
        Sarah will handle the frontend testing. 
        Mike should review the database schema before Wednesday.
        """
    
    print(f"📁 Processing file: {file_path}")
    transcript = transcribe_file(file_path)
    
    if transcript:
        print(f"✅ Transcription successful ({len(transcript)} characters)")
        print(f"\n📝 Transcript:\n{'-'*40}\n{transcript}\n{'-'*40}")
    else:
        print("❌ Transcription failed")
        
    return transcript or ""


def test_task_extraction(transcript: str) -> dict:
    """Extract tasks from transcript using LLM."""
    print("\n" + "="*60)
    print("STEP 2: TASK EXTRACTION (LLM)")
    print("="*60)
    
    llm = get_llm_client()
    
    if not llm.is_configured:
        print("❌ LLM not configured. Check GROQ_API_KEY in .env")
        return {"summary": "", "tasks": []}
    
    print("🤖 Calling LLM to analyze transcript...")
    
    # Get summary
    print("\n📋 Generating summary...")
    summary = llm.summarize_meeting(transcript)
    print(f"Summary: {summary}")
    
    # Extract tasks
    print("\n📋 Extracting tasks...")
    tasks_result = llm.extract_tasks(transcript, summary)
    tasks = tasks_result.get("tasks", [])
    
    print(f"\n✅ Found {len(tasks)} task(s):")
    for i, task in enumerate(tasks, 1):
        print(f"\n  Task {i}:")
        print(f"    📌 Title: {task.get('title')}")
        print(f"    👤 Assignee: {task.get('assignee')}")
        print(f"    📅 Due Date: {task.get('due_date', 'Not specified')}")
    
    return {"summary": summary, "tasks": tasks}


def test_jira_creation(tasks: list, dry_run: bool = False) -> list:
    """Create Jira tickets for extracted tasks."""
    print("\n" + "="*60)
    print("STEP 3: JIRA TICKET CREATION")
    print("="*60)
    
    jira = get_jira_client()
    
    if not jira.is_configured:
        print("❌ Jira not configured. Check JIRA_* settings in .env")
        return []
    
    print(f"🎫 Jira Project: {settings.jira_project_key}")
    print(f"🏠 Jira Server: {settings.jira_server}")
    
    if dry_run:
        print("\n⚠️  DRY RUN MODE - No tickets will be created")
        for task in tasks:
            print(f"\n  Would create ticket:")
            print(f"    Summary: {task.get('title')}")
            print(f"    Assignee: {task.get('assignee')}")
        return []
    
    created_tickets = []
    
    for task in tasks:
        print(f"\n📝 Creating ticket: {task.get('title')}")
        
        try:
            # Map assignee to closest team member
            raw_assignee = task.get('assignee')
            matched_assignee = find_closest_team_member(raw_assignee) if raw_assignee else None
            
            if matched_assignee:
                print(f"  👤 Mapped '{raw_assignee}' -> '{matched_assignee}'")
            elif raw_assignee and raw_assignee != 'Unassigned':
                print(f"  ⚠️  Could not match '{raw_assignee}' to any team member")
            
            # Build description
            description = f"Task extracted from meeting transcript.\\n\\nAssignee mentioned: {raw_assignee or 'Unassigned'}"
            if task.get('due_date'):
                description += f"\\nDue Date: {task.get('due_date')}"
            
            # Create the issue
            result = jira.create_issue(
                summary=task.get('title', 'Untitled Task'),
                description=description,
                issue_type="Task",
                assignee_name=matched_assignee,
                due_date=task.get('due_date')
            )
            
            if result.get('key'):
                print(f"  ✅ Created: {result['key']}")
                print(f"     URL: {result.get('url', 'N/A')}")
                
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
                            due_date_str = task.get('due_date')
                            if due_date_str and due_date_str not in ['null', 'None', None]:
                                try:
                                    deadline = date.fromisoformat(due_date_str)
                                except ValueError:
                                    deadline = date.today() + timedelta(days=7)
                            else:
                                deadline = date.today() + timedelta(days=7)
                            
                            # Create Task record
                            db_task = Task(
                                member_id=member.member_id,
                                description=f"{task.get('title', 'Task')} [Jira: {result['key']}]",
                                deadline=deadline
                            )
                            db.add(db_task)
                            db.commit()
                            print(f"     📊 Stored in DB: task_id={db_task.task_id}, member={member.member_name}")
                        else:
                            print(f"     ⚠️  Not stored in DB (no matching member)")
                except Exception as db_err:
                    print(f"     ⚠️  DB storage failed: {db_err}")
                
                created_tickets.append(result)
            else:
                print(f"  ❌ Failed to create ticket")
                if result.get('error'):
                    print(f"     Error: {result['error']}")
                    
        except Exception as e:
            print(f"  ❌ Error: {e}")
    
    return created_tickets


def run_full_test(file_path: str, dry_run: bool = False, use_mock: bool = False):
    """Run the complete test pipeline."""
    print("\n" + "🚀"*30)
    print("  WORKFLOW AUTOMATION - TEST SCRIPT")
    print("🚀"*30)
    
    # Validate file exists
    if not use_mock and not os.path.exists(file_path):
        print(f"❌ File not found: {file_path}")
        return
    
    # Step 1: Transcription
    if use_mock:
        print("\n⚠️  Using mock transcript (--mock flag)")
        transcript = """
        Okay team, let's wrap up. John, you need to complete the user authentication module by Friday.
        Sarah, please review the pull request for the payment integration today.
        Mike will fix the database connection issue by tomorrow morning.
        """
    else:
        transcript = test_transcription(file_path)
    
    if not transcript:
        print("❌ No transcript available. Exiting.")
        return
    
    # Step 2: Task Extraction
    result = test_task_extraction(transcript)
    tasks = result.get("tasks", [])
    
    if not tasks:
        print("\n⚠️  No tasks extracted from transcript.")
        return
    
    # Step 3: Jira Creation
    created = test_jira_creation(tasks, dry_run=dry_run)
    
    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(f"📝 Transcript length: {len(transcript)} characters")
    print(f"📋 Tasks extracted: {len(tasks)}")
    print(f"🎫 Jira tickets created: {len(created)}")
    
    if created:
        print("\nCreated tickets:")
        for ticket in created:
            print(f"  - {ticket.get('key')}: {ticket.get('url', 'N/A')}")


def main():
    # Default recording file in project root
    project_root = Path(__file__).parent.parent
    default_recording = project_root / "recordings" / "recording-1.mp4"
    
    parser = argparse.ArgumentParser(
        description="Test the workflow automation pipeline with a custom recording."
    )
    parser.add_argument(
        "file_path",
        nargs="?",
        default=str(default_recording),
        help="Path to the audio/video recording file (default: recordings/recording-1.mpeg)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't create Jira tickets, just show what would be created"
    )
    parser.add_argument(
        "--mock",
        action="store_true", 
        help="Use a mock transcript instead of transcribing a file"
    )
    
    args = parser.parse_args()
    
    run_full_test(
        file_path=args.file_path,
        dry_run=args.dry_run,
        use_mock=args.mock
    )


if __name__ == "__main__":
    main()
