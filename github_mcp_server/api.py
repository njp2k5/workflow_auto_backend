"""
GitHub MCP Server - Combined MCP (SSE transport) + REST API

MCP Protocol endpoints (for any MCP client):
    GET  /mcp/sse        - SSE connection endpoint
    POST /mcp/messages   - MCP message handler

REST API endpoints (for React frontend):
    GET /health
    GET /api/commits
    GET /api/commits/{sha}
    GET /api/commits/{sha}/summary
    GET /api/commits-summary
    GET /api/progress-report
    GET /api/contributors
    GET /api/repo-info
    GET /api/commit-activity
    GET /api/pull-requests
    GET /api/branches

Live SSE stream (for React EventSource):
    GET /api/stream/dashboard
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.routing import Route, Mount
from starlette.responses import StreamingResponse
from mcp.server.sse import SseServerTransport

from . import github_client as gh
from . import summarizer as llm_summary
from .config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MCP SSE Transport
# ---------------------------------------------------------------------------
_sse_transport = SseServerTransport("/mcp/messages")


async def _handle_mcp_sse(request: Request):
    """Accept an MCP client connection over SSE."""
    from .server import mcp as mcp_instance

    async with _sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as (read_stream, write_stream):
        await mcp_instance._mcp_server.run(
            read_stream,
            write_stream,
            mcp_instance._mcp_server.create_initialization_options(),
        )


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="GitHub MCP Server",
    description="MCP server (SSE transport) + REST API for GitHub commit tracking dashboard",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount MCP protocol endpoints inside the same app
app.router.routes.insert(0, Route("/mcp/sse", endpoint=_handle_mcp_sse))
app.mount("/mcp/messages", app=_sse_transport.handle_post_message)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "github-mcp-server",
        "mcp_sse_endpoint": "/mcp/sse",
        "github_configured": bool(settings.github_token),
        "llm_configured": bool(settings.groq_api_key),
    }


# ---------------------------------------------------------------------------
# SSE Dashboard Stream (React connects via EventSource)
# ---------------------------------------------------------------------------
@app.get("/api/stream/dashboard")
async def stream_dashboard():
    """
    Server-Sent Events stream for the React dashboard.
    Pushes fresh GitHub stats every 30 seconds automatically.

    React usage:
        const es = new EventSource('http://localhost:3003/api/stream/dashboard');
        es.onmessage = (e) => setDashboard(JSON.parse(e.data));
    """
    async def _generate():
        while True:
            try:
                data = await asyncio.to_thread(_build_dashboard)
                yield f"data: {json.dumps(data)}\n\n"
            except Exception as exc:
                yield f"data: {json.dumps({'error': str(exc)})}\n\n"
            await asyncio.sleep(30)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _build_dashboard() -> dict:
    """Collect all dashboard data (runs in a thread)."""
    return {
        "commits": gh.get_recent_commits(since_days=7, per_page=10),
        "contributors": gh.get_contributors(),
        "repo_info": gh.get_repo_info(),
        "branches": gh.get_branches(),
        "pull_requests": gh.get_recent_pull_requests(state="all", per_page=5),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# REST Endpoints (for React)
# ---------------------------------------------------------------------------
@app.get("/api/commits")
def api_commits(
    branch: Optional[str] = Query(None, description="Branch name"),
    since_days: int = Query(7, ge=1, le=365),
    per_page: int = Query(30, ge=1, le=100),
):
    """Fetch recent commits."""
    try:
        return gh.get_recent_commits(branch=branch, since_days=since_days, per_page=per_page)
    except Exception as exc:
        logger.exception("Error fetching commits")
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/commits/{sha}")
def api_commit_detail(sha: str):
    """Fetch detailed info for a single commit."""
    try:
        return gh.get_commit_detail(sha)
    except Exception as exc:
        logger.exception("Error fetching commit %s", sha)
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/commits/{sha}/summary")
def api_commit_summary(sha: str):
    """Get an LLM-generated summary of a single commit."""
    try:
        detail = gh.get_commit_detail(sha)
        summary = llm_summary.summarize_commit_detail(detail)
        return {"sha": sha, "summary": summary}
    except Exception as exc:
        logger.exception("Error summarizing commit %s", sha)
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/commits-summary")
def api_commits_summary(
    branch: Optional[str] = Query(None),
    since_days: int = Query(7, ge=1, le=365),
):
    """LLM-generated summary of recent commits."""
    try:
        commits = gh.get_recent_commits(branch=branch, since_days=since_days)
        summary = llm_summary.summarize_commits(commits)
        return {
            "total_commits": len(commits),
            "since_days": since_days,
            "summary": summary,
        }
    except Exception as exc:
        logger.exception("Error generating commit summary")
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/progress-report")
def api_progress_report(since_days: int = Query(7, ge=1, le=365)):
    """Full LLM-powered progress report for the frontend dashboard."""
    try:
        commits = gh.get_recent_commits(since_days=since_days)
        contributors = gh.get_contributors()
        repo_info = gh.get_repo_info()
        prs = gh.get_recent_pull_requests(state="all", per_page=10)
        report = llm_summary.generate_progress_report(commits, contributors, repo_info, prs)
        # Add frontend-expected top-level fields
        report["period"] = f"Last {since_days} days"
        report["total_commits"] = len(commits)
        report["contributors"] = len(contributors)
        return report
    except Exception as exc:
        logger.exception("Error generating progress report")
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/contributors")
def api_contributors():
    """Fetch repository contributors."""
    try:
        return gh.get_contributors()
    except Exception as exc:
        logger.exception("Error fetching contributors")
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/repo-info")
def api_repo_info():
    """Fetch basic repository metadata."""
    try:
        return gh.get_repo_info()
    except Exception as exc:
        logger.exception("Error fetching repo info")
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/commit-activity")
def api_commit_activity():
    """Weekly commit activity for the past year."""
    try:
        return gh.get_commit_activity()
    except Exception as exc:
        logger.exception("Error fetching commit activity")
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/pull-requests")
def api_pull_requests(
    state: str = Query("all", pattern="^(open|closed|all)$"),
    per_page: int = Query(10, ge=1, le=100),
):
    """Fetch recent pull requests."""
    try:
        return gh.get_recent_pull_requests(state=state, per_page=per_page)
    except Exception as exc:
        logger.exception("Error fetching pull requests")
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/branches")
def api_branches():
    """List repository branches."""
    try:
        return gh.get_branches()
    except Exception as exc:
        logger.exception("Error fetching branches")
        raise HTTPException(status_code=502, detail=str(exc))
