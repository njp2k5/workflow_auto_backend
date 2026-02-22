"""
FastAPI routes for SRS (Software Requirements Specification) document processing.
Handles file upload and orchestrates the SRS processing pipeline.
"""
from typing import Optional

from fastapi import APIRouter, File, UploadFile, HTTPException, Form
from pydantic import BaseModel

from app.srs_pipeline import process_srs_document
from app.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/srs", tags=["SRS Processing"])


class SRSProcessingResponse(BaseModel):
    """Response model for SRS processing."""
    success: bool
    document_title: Optional[str] = None
    project_name: Optional[str] = None
    sections_count: int = 0
    confluence_pages: list = []
    jira_tasks: list = []
    jira_stories: list = []
    user_stories_count: int = 0
    decisions: list = []
    error: Optional[str] = None
    processing_time_ms: float = 0


@router.post("/upload", response_model=SRSProcessingResponse)
async def upload_srs_document(
    file: UploadFile = File(..., description="Word document (.docx) containing the SRS"),
    project_name: Optional[str] = Form(None, description="Optional project name override")
):
    """
    Upload and process an SRS (Software Requirements Specification) document.
    
    This endpoint:
    1. Accepts a Word document (.docx) containing the SRS
    2. Parses sections using rule-based extraction
    3. Creates Confluence pages for each section type:
       - Introduction → Product Overview
       - Scope → System Scope
       - User Types → Personas
       - Functional Requirements → Feature Pages
       - Non-functional → NFR
       - UI → UI/UX
       - API → API Docs
       - Workflows → Diagrams
    4. Generates and assigns Jira tasks to team members:
       - Nikhil J Prasad
       - S Govind Krishnan
       - Kailas S S
       - Mukundan V S
    5. Creates user stories from functional requirements
    
    Args:
        file: Word document (.docx) file
        project_name: Optional name to use for the project (extracted from document if not provided)
        
    Returns:
        SRSProcessingResponse with created pages, tickets, and processing decisions
    """
    logger.info(f"📤 Received SRS document: {file.filename}")
    
    # Validate file type
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")
    
    if not file.filename.endswith('.docx'):
        raise HTTPException(
            status_code=400,
            detail="Invalid file type. Please upload a Word document (.docx)"
        )
    
    # Read file content
    try:
        content = await file.read()
        logger.info(f"📦 File size: {len(content)} bytes")
    except Exception as e:
        logger.error(f"Failed to read file: {e}")
        raise HTTPException(status_code=400, detail=f"Failed to read file: {e}")
    
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="File is empty")
    
    # Process the document through the pipeline
    try:
        result = await process_srs_document(
            file_content=content,
            filename=file.filename,
            project_name=project_name
        )
        
        logger.info(f"✅ SRS processing complete: {result.get('success')}")
        
        return SRSProcessingResponse(**result)
        
    except Exception as e:
        logger.error(f"SRS processing failed: {e}")
        raise HTTPException(status_code=500, detail=f"Processing failed: {e}")


@router.get("/status")
async def get_srs_status():
    """
    Get the status of the SRS processing service.
    
    Returns:
        Service status and configuration info
    """
    return {
        "status": "ready",
        "supported_formats": [".docx"],
        "section_mappings": {
            "Introduction": "Product Overview",
            "Scope": "System Scope",
            "User Types": "Personas",
            "Functional Requirements": "Feature Pages",
            "Non-functional": "NFR",
            "UI": "UI/UX",
            "API": "API Docs",
            "Workflows": "Diagrams",
        },
        "team_members": [
            "Nikhil J Prasad",
            "S Govind Krishnan",
            "Kailas S S",
            "Mukundan V S",
        ],
    }
