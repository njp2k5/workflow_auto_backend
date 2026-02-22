import logging
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

print("imported contents for main")
from api.routes import auth, health
print("imported routes")
from db.base import Base
print("imported base")
from db.session import engine
print("imported engine")
from models.user import User
print("imported user model")

# Import recording processing modules
from app.config import settings
from app.db import init_db, get_db, check_db_connection
from app.models import Meeting, Transcription, Task, Member
from app.scheduler import (
    start_scheduler,
    stop_scheduler,
    get_scheduler_status,
    trigger_immediate_poll,
    clear_processed_cache
)
from app.recording_watcher import (
    get_recordings_status,
    list_recordings,
    process_recording_file,
    start_watcher,
    stop_watcher,
    is_watcher_running,
    get_watcher_status
)
from app.transcriber import transcribe_file, is_transcriber_ready
from app.jira_client import get_jira_client
from app.llm import get_llm_client
from app.pipeline import process_meeting, process_recording

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# Pydantic models for API responses
class HealthResponse(BaseModel):
    status: str
    services: dict


class SchedulerStatusResponse(BaseModel):
    running: bool
    next_run: Optional[str]
    job_count: int
    poll_interval: int


class MeetingResponse(BaseModel):
    meeting_id: int
    meeting_date: str
    transcription_id: int
    transcription_summary: Optional[str]


class MeetingListResponse(BaseModel):
    meetings: List[MeetingResponse]
    total: int


class TaskResponse(BaseModel):
    task_id: int
    member_id: int
    member_name: Optional[str]
    description: str
    deadline: str


class TaskListResponse(BaseModel):
    tasks: List[TaskResponse]
    total: int


class ProcessRecordingRequest(BaseModel):
    path: str


class RecordingStatusResponse(BaseModel):
    recordings_dir: str
    total_files: int
    processed_files: int
    pending_files: int
    supported_extensions: List[str]
    cache_size: int


class RecordingFileInfo(BaseModel):
    filename: str
    path: str
    size_mb: float
    modified_at: str
    processed: bool


@asynccontextmanager
async def app_lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown."""
    # Startup
    logger.info("Starting application...")
    
    try:
        # Initialize database tables (existing)
        Base.metadata.create_all(bind=engine)
        print("Database tables created successfully.")
        
        # Initialize recording processing tables
        init_db()
        logger.info("Recording processing database tables initialized")
        
        # Start the scheduler for recording folder polling
        if settings.app_env != "test":
            start_scheduler()
            logger.info("Recording polling scheduler started")
    except Exception as e:
        logger.error(f"Error during startup: {e}")
    
    yield
    
    # Shutdown
    logger.info("Shutting down application...")
    stop_scheduler()
    logger.info("Application shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Workflow Automation Backend",
        description="Backend service for meeting recording transcription and task automation",
        version="1.0.0",
        lifespan=app_lifespan
    )

    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Configure appropriately for production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include existing routers
    app.include_router(health.router)
    app.include_router(auth.router)
    
    # ==========================================================
    # Recording Processing Endpoints
    # ==========================================================
    
    @app.get("/api/recordings/health", response_model=HealthResponse, tags=["Recording Processing"])
    async def recordings_health_check():
        """Check health of all recording processing services."""
        jira_client = get_jira_client()
        llm_client = get_llm_client()
        
        # Only check configuration status - avoid blocking calls
        services = {
            "database": check_db_connection(),
            "transcriber": is_transcriber_ready(),
            "jira": jira_client.is_configured,
            "llm": llm_client.is_configured
        }
        
        overall_status = "healthy" if all(services.values()) else "degraded"
        
        return HealthResponse(
            status=overall_status,
            services=services
        )
    
    @app.get("/api/recordings/status", response_model=RecordingStatusResponse, tags=["Recording Processing"])
    async def get_recordings_dir_status():
        """Get status of the recordings directory."""
        status = get_recordings_status()
        return RecordingStatusResponse(**status)
    
    @app.get("/api/recordings/files", tags=["Recording Processing"])
    async def list_recording_files(include_processed: bool = True):
        """List all recording files in the watched directory."""
        files = list_recordings(include_processed=include_processed)
        return {"recordings": files, "total": len(files)}
    
    @app.get("/api/recordings/scheduler/status", response_model=SchedulerStatusResponse, tags=["Recording Processing"])
    async def get_recording_scheduler_status():
        """Get the current status of the recording polling scheduler."""
        status = get_scheduler_status()
        return SchedulerStatusResponse(**status)
    
    @app.post("/api/recordings/scheduler/trigger", tags=["Recording Processing"])
    async def trigger_recording_poll(background_tasks: BackgroundTasks):
        """Trigger an immediate poll of the recordings folder."""
        background_tasks.add_task(trigger_immediate_poll)
        return {"message": "Poll triggered", "status": "running"}
    
    @app.post("/api/recordings/scheduler/start", tags=["Recording Processing"])
    async def start_recording_scheduler():
        """Start the recording polling scheduler."""
        start_scheduler()
        return {"message": "Scheduler started"}
    
    @app.post("/api/recordings/scheduler/stop", tags=["Recording Processing"])
    async def stop_recording_scheduler():
        """Stop the recording polling scheduler."""
        stop_scheduler()
        return {"message": "Scheduler stopped"}
    
    @app.post("/api/recordings/cache/clear", tags=["Recording Processing"])
    async def clear_recordings_cache():
        """Clear the processed recordings cache."""
        clear_processed_cache()
        return {"message": "Cache cleared"}
    
    # ==========================================================
    # File Watcher Endpoints
    # ==========================================================
    
    @app.get("/api/watcher/status", tags=["File Watcher"])
    async def get_file_watcher_status():
        """Get the current status of the real-time file watcher."""
        return get_watcher_status()
    
    @app.post("/api/watcher/start", tags=["File Watcher"])
    async def start_file_watcher(poll_interval: int = 5):
        """Start the real-time file watcher."""
        success = start_watcher(poll_interval=poll_interval)
        if success:
            return {"message": "File watcher started", "poll_interval": poll_interval}
        return {"message": "File watcher already running", "poll_interval": poll_interval}
    
    @app.post("/api/watcher/stop", tags=["File Watcher"])
    async def stop_file_watcher():
        """Stop the real-time file watcher."""
        success = stop_watcher()
        if success:
            return {"message": "File watcher stopped"}
        return {"message": "File watcher was not running"}
    
    @app.get("/api/recordings/meetings", response_model=MeetingListResponse, tags=["Recording Processing"])
    async def list_meetings(
        skip: int = 0,
        limit: int = 50,
        db: Session = Depends(get_db)
    ):
        """List all meeting recordings with their transcriptions."""
        query = db.query(Meeting)
        
        total = query.count()
        meetings = query.order_by(Meeting.meeting_date.desc()).offset(skip).limit(limit).all()
        
        meeting_responses = []
        for m in meetings:
            # Get transcription summary
            transcription = db.query(Transcription).filter(
                Transcription.transcription_id == m.transcription_id
            ).first()
            
            meeting_responses.append(MeetingResponse(
                meeting_id=m.meeting_id,
                meeting_date=m.meeting_date.isoformat() if m.meeting_date else None,
                transcription_id=m.transcription_id,
                transcription_summary=transcription.transcription_summary if transcription else None
            ))
        
        return MeetingListResponse(meetings=meeting_responses, total=total)
    
    @app.get("/api/recordings/meetings/{meeting_id}", response_model=MeetingResponse, tags=["Recording Processing"])
    async def get_meeting(meeting_id: int, db: Session = Depends(get_db)):
        """Get details of a specific meeting by ID."""
        meeting = db.query(Meeting).filter(Meeting.meeting_id == meeting_id).first()
        
        if not meeting:
            raise HTTPException(status_code=404, detail="Meeting not found")
        
        # Get transcription
        transcription = db.query(Transcription).filter(
            Transcription.transcription_id == meeting.transcription_id
        ).first()
        
        return MeetingResponse(
            meeting_id=meeting.meeting_id,
            meeting_date=meeting.meeting_date.isoformat() if meeting.meeting_date else None,
            transcription_id=meeting.transcription_id,
            transcription_summary=transcription.transcription_summary if transcription else None
        )
    
    @app.get("/api/recordings/tasks", response_model=TaskListResponse, tags=["Recording Processing"])
    async def list_tasks(
        skip: int = 0,
        limit: int = 50,
        member_id: Optional[int] = None,
        db: Session = Depends(get_db)
    ):
        """List all tasks, optionally filtered by member."""
        query = db.query(Task)
        
        if member_id:
            query = query.filter(Task.member_id == member_id)
        
        total = query.count()
        tasks = query.order_by(Task.deadline.asc()).offset(skip).limit(limit).all()
        
        task_responses = []
        for t in tasks:
            # Get member name
            member = db.query(Member).filter(Member.member_id == t.member_id).first()
            
            task_responses.append(TaskResponse(
                task_id=t.task_id,
                member_id=t.member_id,
                member_name=member.member_name if member else None,
                description=t.description,
                deadline=t.deadline.isoformat() if t.deadline else None
            ))
        
        return TaskListResponse(tasks=task_responses, total=total)
    
    @app.post("/api/recordings/process", tags=["Recording Processing"])
    async def process_recording_endpoint(request: ProcessRecordingRequest):
        """
        Manually process a specific recording file.
        Transcribes the file with Faster-Whisper and runs it through the LangGraph pipeline.
        """
        import os
        from pathlib import Path
        
        file_path = Path(request.path)
        
        # Validate file exists
        if not file_path.exists():
            raise HTTPException(status_code=404, detail=f"File not found: {request.path}")
        
        # Validate extension
        supported = {'.mp4', '.mp3', '.wav', '.m4a'}
        if file_path.suffix.lower() not in supported:
            raise HTTPException(
                status_code=400, 
                detail=f"Unsupported file format. Supported: {', '.join(supported)}"
            )
        
        try:
            # Process the recording
            result = process_recording_file(str(file_path))
            
            if result.get("error"):
                raise HTTPException(status_code=500, detail=result["error"])
            
            return {
                "success": True,
                "filename": file_path.name,
                "meeting_id": result.get("meeting_id"),
                "transcription_id": result.get("transcription_id"),
                "summary": result.get("summary"),
                "tasks": result.get("tasks"),
                "task_ids": result.get("task_ids"),
                "jira_keys": result.get("jira_keys")
            }
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error processing recording: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.delete("/api/recordings/meetings/{meeting_id}", tags=["Recording Processing"])
    async def delete_meeting(meeting_id: int, db: Session = Depends(get_db)):
        """Delete a meeting record and its associated transcription."""
        meeting = db.query(Meeting).filter(Meeting.meeting_id == meeting_id).first()
        
        if not meeting:
            raise HTTPException(status_code=404, detail="Meeting not found")
        
        # Also delete the associated transcription
        transcription_id = meeting.transcription_id
        
        db.delete(meeting)
        
        if transcription_id:
            transcription = db.query(Transcription).filter(
                Transcription.transcription_id == transcription_id
            ).first()
            if transcription:
                db.delete(transcription)
        
        db.commit()
        
        return {"message": f"Meeting {meeting_id} deleted"}

    return app


app = create_app()