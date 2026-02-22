"""
LangGraph workflow for processing SRS (Software Requirements Specification) documents.
Orchestrates parsing, Confluence page creation, and Jira task generation.
"""
import time
from typing import TypedDict, List, Dict, Any, Optional
from datetime import datetime, date

from langgraph.graph import StateGraph, END

from app.srs_parser import get_srs_parser, SRSSection, ParsedSRS, build_confluence_page_html
from app.jira_client import get_jira_client
from app.confluence_client import get_confluence_client
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


class SRSState(TypedDict):
    """State object passed through the SRS processing workflow."""
    # Input data
    filename: str
    file_content: bytes
    project_name: Optional[str]
    
    # Parsed data
    document_title: Optional[str]
    sections: List[Dict[str, Any]]
    raw_text: Optional[str]
    metadata: Dict[str, Any]
    
    # Generated items
    tasks: List[Dict[str, Any]]
    user_stories: List[Dict[str, Any]]
    
    # Output - Confluence
    confluence_pages: List[Dict[str, str]]  # {page_type, page_id, page_url}
    
    # Output - Jira
    jira_keys: List[str]
    story_keys: List[str]
    
    # Processing state
    decisions: List[str]
    error: Optional[str]
    current_step: str


def parse_srs_document(state: SRSState) -> SRSState:
    """
    Node: Parse the uploaded SRS Word document.
    Extracts sections and maps them to Confluence page types.
    """
    node_name = "parse_srs_document"
    start_time = time.time()
    log_node_entry(node_name, logger)
    log_step_progress(1, 5, "Parse SRS Document", logger)
    
    state["current_step"] = node_name
    state["decisions"] = state.get("decisions", [])
    
    try:
        parser = get_srs_parser()
        file_content = state.get("file_content")
        
        if not file_content:
            raise ValueError("No file content provided")
        
        # Parse the document
        parsed = parser.parse_document(file_content)
        
        state["document_title"] = parsed.document_title
        state["raw_text"] = parsed.raw_text
        state["metadata"] = parsed.metadata
        
        # Convert sections to serializable format
        sections_data = []
        for section in parsed.sections:
            sections_data.append({
                "title": section.title,
                "content": section.content,
                "confluence_page": section.confluence_page,
                "requirements": section.requirements,
            })
        state["sections"] = sections_data
        
        # Set project name from document title if not provided
        if not state.get("project_name"):
            state["project_name"] = parsed.document_title.replace("SRS", "").replace("Software Requirements Specification", "").strip()
            state["project_name"] = state["project_name"] or "New Project"
        
        state["decisions"].append(f"📄 Parsed SRS: '{parsed.document_title}' with {len(sections_data)} sections")
        
        # Log section breakdown
        page_types = {}
        for section in sections_data:
            page_type = section["confluence_page"]
            page_types[page_type] = page_types.get(page_type, 0) + 1
        
        for page_type, count in page_types.items():
            logger.info(f"  → {page_type}: {count} section(s)")
        
        state["decisions"].append(f"📊 Section mapping: {', '.join(f'{k}({v})' for k, v in page_types.items())}")
        
        duration_ms = (time.time() - start_time) * 1000
        log_node_exit(node_name, logger, success=True, duration_ms=duration_ms)
        
    except Exception as e:
        logger.error(f"Failed to parse SRS document: {e}")
        state["error"] = str(e)
        state["decisions"].append(f"❌ Parse failed: {e}")
        duration_ms = (time.time() - start_time) * 1000
        log_node_exit(node_name, logger, success=False, duration_ms=duration_ms)
    
    return state


def generate_tasks_and_stories(state: SRSState) -> SRSState:
    """
    Node: Generate tasks and user stories from parsed sections.
    """
    node_name = "generate_tasks_and_stories"
    start_time = time.time()
    log_node_entry(node_name, logger)
    log_step_progress(2, 5, "Generate Tasks & Stories", logger)
    
    state["current_step"] = node_name
    
    if state.get("error"):
        logger.warning("Skipping task generation due to previous error")
        return state
    
    try:
        parser = get_srs_parser()
        
        # Reconstruct SRSSection objects from state
        sections = []
        for s in state.get("sections", []):
            section = SRSSection(
                title=s["title"],
                content=s["content"],
                confluence_page=s["confluence_page"],
                requirements=s.get("requirements", [])
            )
            sections.append(section)
        
        # Generate tasks
        tasks = parser.generate_tasks(sections)
        state["tasks"] = tasks
        
        # Generate user stories
        user_stories = parser.generate_user_stories(sections)
        state["user_stories"] = user_stories
        
        state["decisions"].append(f"📋 Generated {len(tasks)} tasks and {len(user_stories)} user stories")
        
        # Log task assignments
        assignee_counts = {}
        for task in tasks:
            assignee = task.get("assignee", "Unassigned")
            assignee_counts[assignee] = assignee_counts.get(assignee, 0) + 1
        
        for assignee, count in assignee_counts.items():
            logger.info(f"  → {assignee}: {count} task(s)")
        
        duration_ms = (time.time() - start_time) * 1000
        log_node_exit(node_name, logger, success=True, duration_ms=duration_ms)
        
    except Exception as e:
        logger.error(f"Failed to generate tasks: {e}")
        state["error"] = str(e)
        state["decisions"].append(f"❌ Task generation failed: {e}")
        duration_ms = (time.time() - start_time) * 1000
        log_node_exit(node_name, logger, success=False, duration_ms=duration_ms)
    
    return state


def create_confluence_pages(state: SRSState) -> SRSState:
    """
    Node: Create Confluence pages for each section type.
    """
    node_name = "create_confluence_pages"
    start_time = time.time()
    log_node_entry(node_name, logger)
    log_step_progress(3, 5, "Create Confluence Pages", logger)
    
    state["current_step"] = node_name
    state["confluence_pages"] = state.get("confluence_pages", [])
    
    if state.get("error"):
        logger.warning("Skipping Confluence due to previous error")
        return state
    
    confluence_client = get_confluence_client()
    
    if not confluence_client.is_configured:
        logger.warning("Confluence not configured, skipping page creation")
        state["decisions"].append("⚠️ Confluence not configured - pages not created")
        return state
    
    try:
        project_name = state.get("project_name") or "SRS Project"
        sections = state.get("sections", [])
        
        # Group sections by page type
        page_groups: Dict[str, List[Dict]] = {}
        for section in sections:
            page_type = section["confluence_page"]
            if page_type not in page_groups:
                page_groups[page_type] = []
            page_groups[page_type].append(section)
        
        created_pages = []
        
        for page_type, page_sections in page_groups.items():
            # Build combined content for all sections of this type
            html_content = _build_srs_page_html(page_type, page_sections, project_name)
            
            # Create page title
            page_title = f"{project_name} - {page_type}"
            
            logger.info(f"Creating Confluence page: {page_title}")
            
            # Use the confluence client to create/update page
            result = confluence_client.create_or_update_page(page_title, html_content)
            
            if result and not result.get("fallback"):
                page_info = {
                    "page_type": page_type,
                    "page_id": result.get("page_id"),
                    "page_url": result.get("page_url"),
                    "action": result.get("action", "created"),
                }
                created_pages.append(page_info)
                state["decisions"].append(f"📄 {page_type}: {result.get('action', 'created')} → {result.get('page_url', 'N/A')}")
            else:
                logger.warning(f"Failed to create page for {page_type}")
                state["decisions"].append(f"⚠️ {page_type}: page creation failed")
        
        state["confluence_pages"] = created_pages
        state["decisions"].append(f"📚 Created {len(created_pages)} Confluence pages")
        
        duration_ms = (time.time() - start_time) * 1000
        log_node_exit(node_name, logger, success=True, duration_ms=duration_ms)
        
    except Exception as e:
        logger.error(f"Failed to create Confluence pages: {e}")
        state["decisions"].append(f"❌ Confluence error: {e}")
        duration_ms = (time.time() - start_time) * 1000
        log_node_exit(node_name, logger, success=False, duration_ms=duration_ms)
    
    return state


def create_jira_tickets(state: SRSState) -> SRSState:
    """
    Node: Create Jira tickets for tasks and user stories.
    """
    node_name = "create_jira_tickets"
    start_time = time.time()
    log_node_entry(node_name, logger)
    log_step_progress(4, 5, "Create Jira Tickets", logger)
    
    state["current_step"] = node_name
    state["jira_keys"] = state.get("jira_keys", [])
    state["story_keys"] = state.get("story_keys", [])
    
    if state.get("error"):
        logger.warning("Skipping Jira due to previous error")
        return state
    
    jira_client = get_jira_client()
    
    if not jira_client.is_configured:
        logger.warning("Jira not configured, skipping ticket creation")
        state["decisions"].append("⚠️ Jira not configured - tickets not created")
        return state
    
    try:
        project_name = state.get("project_name") or "SRS Project"
        tasks = state.get("tasks", [])
        user_stories = state.get("user_stories", [])
        
        created_tasks = []
        created_stories = []
        
        # Create task tickets
        for task in tasks:
            logger.info(f"Creating task: {task['title'][:50]}...")
            
            # Check for duplicates
            existing = jira_client.check_for_duplicate(task["title"], task.get("assignee"))
            
            if existing:
                logger.info(f"  → Duplicate found: {existing}")
                state["decisions"].append(f"🔄 Skipped duplicate: {task['title'][:40]}... → {existing}")
                continue
            
            # Create the issue
            result = jira_client.create_issue(
                summary=task["title"],
                description=task.get("description", ""),
                issue_type="Task",
                assignee_name=task.get("assignee"),
                labels=task.get("labels", ["srs-generated"]),
            )
            
            if result:
                issue_key = result.get("key")
                created_tasks.append(issue_key)
                state["decisions"].append(f"✅ Task created: {issue_key} → {task.get('assignee', 'Unassigned')}")
            else:
                state["decisions"].append(f"⚠️ Failed to create task: {task['title'][:40]}...")
        
        # Create story tickets
        for story in user_stories:
            logger.info(f"Creating story: {story['title'][:50]}...")
            
            # Check for duplicates
            existing = jira_client.check_for_duplicate(story["title"], None)
            
            if existing:
                logger.info(f"  → Duplicate found: {existing}")
                state["decisions"].append(f"🔄 Skipped duplicate story: {story['title'][:40]}...")
                continue
            
            # Create the story issue
            result = jira_client.create_issue(
                summary=story["title"],
                description=story.get("description", "") + f"\n\nStory Points: {story.get('story_points', 3)}",
                issue_type="Story",
                labels=["srs-generated", "user-story"],
            )
            
            if result:
                issue_key = result.get("key")
                created_stories.append(issue_key)
                state["decisions"].append(f"✅ Story created: {issue_key}")
            else:
                state["decisions"].append(f"⚠️ Failed to create story: {story['title'][:40]}...")
        
        state["jira_keys"] = created_tasks
        state["story_keys"] = created_stories
        
        state["decisions"].append(f"🎫 Created {len(created_tasks)} tasks and {len(created_stories)} stories in Jira")
        
        duration_ms = (time.time() - start_time) * 1000
        log_node_exit(node_name, logger, success=True, duration_ms=duration_ms)
        
    except Exception as e:
        logger.error(f"Failed to create Jira tickets: {e}")
        state["decisions"].append(f"❌ Jira error: {e}")
        duration_ms = (time.time() - start_time) * 1000
        log_node_exit(node_name, logger, success=False, duration_ms=duration_ms)
    
    return state


def finalize_srs_processing(state: SRSState) -> SRSState:
    """
    Node: Finalize processing and generate summary.
    """
    node_name = "finalize_srs_processing"
    start_time = time.time()
    log_node_entry(node_name, logger)
    log_step_progress(5, 5, "Finalize Processing", logger)
    
    state["current_step"] = node_name
    
    # Generate final summary
    summary_parts = []
    summary_parts.append(f"📄 Document: {state.get('document_title', 'Unknown')}")
    summary_parts.append(f"📁 Project: {state.get('project_name', 'Unknown')}")
    summary_parts.append(f"📊 Sections parsed: {len(state.get('sections', []))}")
    summary_parts.append(f"📚 Confluence pages: {len(state.get('confluence_pages', []))}")
    summary_parts.append(f"🎫 Jira tasks: {len(state.get('jira_keys', []))}")
    summary_parts.append(f"📖 User stories: {len(state.get('story_keys', []))}")
    
    logger.info("=" * 60)
    logger.info("SRS PROCESSING COMPLETE")
    logger.info("=" * 60)
    for line in summary_parts:
        logger.info(line)
    logger.info("=" * 60)
    
    # Log all decisions
    logger.info("DECISIONS MADE:")
    for decision in state.get("decisions", []):
        logger.info(f"  {decision}")
    
    duration_ms = (time.time() - start_time) * 1000
    log_node_exit(node_name, logger, success=True, duration_ms=duration_ms)
    
    return state


def _build_srs_page_html(page_type: str, sections: List[Dict], project_name: str) -> str:
    """Build HTML content for a Confluence page from multiple sections."""
    html_parts = []
    # Page title: use mapped SRS section name(s) for clarity
    mapped_section_names = ', '.join([section['title'] for section in sections])
    page_title = f"{project_name} - {page_type} ({mapped_section_names})"
    html_parts.append(f"<h1>{page_title}</h1>")
    html_parts.append(f"<p><em>Auto-generated from SRS document on {date.today().isoformat()}</em></p><hr/>")

    # Info panel based on page type
    type_descriptions = {
        "Product Overview": "Product vision, goals, and high-level overview of the system.",
        "System Scope": "System boundaries, constraints, and scope definition.",
        "Personas": "User types, roles, and persona definitions.",
        "Feature Pages": "Functional requirements and feature specifications.",
        "NFR": "Non-functional requirements including performance, security, and reliability.",
        "UI/UX": "User interface and experience specifications.",
        "API Docs": "API specifications, endpoints, and integration details.",
        "Diagrams": "Workflow diagrams, process flows, and system interactions.",
    }
    description = type_descriptions.get(page_type, "Documentation from SRS.")
    html_parts.append(f"""
    <ac:structured-macro ac:name="info">
        <ac:rich-text-body>
            <p><strong>{page_type}</strong>: {description}</p>
        </ac:rich-text-body>
    </ac:structured-macro>
    """)

    # Add content from each mapped section
    for section in sections:
        html_parts.append(f"<h2>{section['title']}</h2>")
        html_parts.append(f"<div class='srs-section'>{section['content'].replace(chr(10), '<br/>')}</div>")
        # Add requirements table if present
        requirements = section.get("requirements", [])
        if requirements:
            html_parts.append("<h3>Requirements</h3><table><tr><th>ID</th><th>Requirement</th></tr>")
            for i, req in enumerate(requirements, 1):
                html_parts.append(f"<tr><td>REQ-{i:03d}</td><td>{req}</td></tr>")
            html_parts.append("</table>")

    # Footer
    html_parts.append(f"<hr/><p><em>Generated by SRS Automation on {datetime.now().strftime('%Y-%m-%d %H:%M')}</em></p>")
    return "\n".join(html_parts)


def build_srs_workflow() -> StateGraph:
    """
    Build the LangGraph workflow for SRS processing.
    
    Workflow:
    1. parse_srs_document - Extract sections from Word document
    2. generate_tasks_and_stories - Generate tasks and user stories
    3. create_confluence_pages - Create Confluence documentation
    4. create_jira_tickets - Create Jira issues
    5. finalize_srs_processing - Summary and cleanup
    """
    workflow = StateGraph(SRSState)
    
    # Add nodes
    workflow.add_node("parse_srs_document", parse_srs_document)
    workflow.add_node("generate_tasks_and_stories", generate_tasks_and_stories)
    workflow.add_node("create_confluence_pages", create_confluence_pages)
    workflow.add_node("create_jira_tickets", create_jira_tickets)
    workflow.add_node("finalize_srs_processing", finalize_srs_processing)
    
    # Define edges (linear workflow)
    workflow.set_entry_point("parse_srs_document")
    workflow.add_edge("parse_srs_document", "generate_tasks_and_stories")
    workflow.add_edge("generate_tasks_and_stories", "create_confluence_pages")
    workflow.add_edge("create_confluence_pages", "create_jira_tickets")
    workflow.add_edge("create_jira_tickets", "finalize_srs_processing")
    workflow.add_edge("finalize_srs_processing", END)
    
    return workflow


# Compile the workflow
srs_workflow = build_srs_workflow().compile()


async def process_srs_document(
    file_content: bytes,
    filename: str,
    project_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Process an SRS document through the complete workflow.
    
    Args:
        file_content: Raw bytes of the Word document
        filename: Original filename
        project_name: Optional project name override
        
    Returns:
        Processing results including created pages and tickets
    """
    log_pipeline_start("SRS Processing", logger)
    start_time = time.time()
    
    # Initialize state
    initial_state: SRSState = {
        "filename": filename,
        "file_content": file_content,
        "project_name": project_name,
        "document_title": None,
        "sections": [],
        "raw_text": None,
        "metadata": {},
        "tasks": [],
        "user_stories": [],
        "confluence_pages": [],
        "jira_keys": [],
        "story_keys": [],
        "decisions": [],
        "error": None,
        "current_step": "initializing",
    }
    
    try:
        # Run the workflow
        final_state = srs_workflow.invoke(initial_state)
        
        duration_ms = (time.time() - start_time) * 1000
        log_pipeline_end("SRS Processing", logger, success=True, duration_ms=duration_ms)
        
        # Build response
        return {
            "success": True,
            "document_title": final_state.get("document_title"),
            "project_name": final_state.get("project_name"),
            "sections_count": len(final_state.get("sections", [])),
            "confluence_pages": final_state.get("confluence_pages", []),
            "jira_tasks": final_state.get("jira_keys", []),
            "jira_stories": final_state.get("story_keys", []),
            "user_stories_count": len(final_state.get("user_stories", [])),
            "decisions": final_state.get("decisions", []),
            "error": final_state.get("error"),
            "processing_time_ms": duration_ms,
        }
        
    except Exception as e:
        logger.error(f"SRS processing failed: {e}")
        duration_ms = (time.time() - start_time) * 1000
        log_pipeline_end("SRS Processing", logger, success=False, duration_ms=duration_ms)
        
        return {
            "success": False,
            "error": str(e),
            "processing_time_ms": duration_ms,
        }
