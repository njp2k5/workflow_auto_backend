"""
REST API Bridge – Exposes the MCP server's data over standard HTTP endpoints
so the React frontend can consume it directly without needing an MCP client.

Run standalone:
    uvicorn github_mcp_server.api:app --port 3003 --reload
"""
import logging
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from . import github_client as gh
from . import summarizer as llm_summary
from .config import settings

logger = logging.getLogger(__name__)

app = FastAPI(
    title="GitHub MCP – REST Bridge",
    description="HTTP API that mirrors the GitHub MCP server tools for the React frontend.",
    version="1.0.0",
)

# CORS – allow the React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Health ─────────────────────────────────────────────────────────────────


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "github-mcp-server",
        "github_configured": bool(settings.github_token),
        "llm_configured": bool(settings.groq_api_key),
    }


# ── Commits ────────────────────────────────────────────────────────────────


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
        logger.exception(f"Error fetching commit {sha}")
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/commits/{sha}/summary")
def api_commit_summary(sha: str):
    """Get an LLM-generated summary of a single commit."""
    try:
        detail = gh.get_commit_detail(sha)
        summary = llm_summary.summarize_commit_detail(detail)
        return {"sha": sha, "summary": summary}
    except Exception as exc:
        logger.exception(f"Error summarizing commit {sha}")
        raise HTTPException(status_code=502, detail=str(exc))


# ── Commit Summary ────────────────────────────────────────────────────────


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


# ── Progress Report ───────────────────────────────────────────────────────


@app.get("/api/progress-report")
def api_progress_report(since_days: int = Query(7, ge=1, le=365)):
    """Full LLM-powered progress report for the frontend dashboard."""
    try:
        commits = gh.get_recent_commits(since_days=since_days)
        contributors = gh.get_contributors()
        repo_info = gh.get_repo_info()
        prs = gh.get_recent_pull_requests(state="all", per_page=10)
        report = llm_summary.generate_progress_report(commits, contributors, repo_info, prs)
        return report
    except Exception as exc:
        logger.exception("Error generating progress report")
        raise HTTPException(status_code=502, detail=str(exc))


# ── Contributors ──────────────────────────────────────────────────────────


@app.get("/api/contributors")
def api_contributors():
    """Fetch repository contributors."""
    try:
        return gh.get_contributors()
    except Exception as exc:
        logger.exception("Error fetching contributors")
        raise HTTPException(status_code=502, detail=str(exc))


# ── Repo Info ─────────────────────────────────────────────────────────────


@app.get("/api/repo-info")
def api_repo_info():
    """Fetch basic repository metadata."""
    try:
        return gh.get_repo_info()
    except Exception as exc:
        logger.exception("Error fetching repo info")
        raise HTTPException(status_code=502, detail=str(exc))


# ── Commit Activity ──────────────────────────────────────────────────────


@app.get("/api/commit-activity")
def api_commit_activity():
    """Weekly commit activity for the past year."""
    try:
        return gh.get_commit_activity()
    except Exception as exc:
        logger.exception("Error fetching commit activity")
        raise HTTPException(status_code=502, detail=str(exc))


# ── Pull Requests ─────────────────────────────────────────────────────────


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


# ── Branches ──────────────────────────────────────────────────────────────


@app.get("/api/branches")
def api_branches():
    """List repository branches."""
    try:
        return gh.get_branches()
    except Exception as exc:
        logger.exception("Error fetching branches")
        raise HTTPException(status_code=502, detail=str(exc))
