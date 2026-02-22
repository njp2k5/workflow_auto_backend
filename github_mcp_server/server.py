"""
MCP Server – Exposes GitHub data as MCP Resources and Tools.

Resources (read-only context for LLMs):
    github://commits          – recent commits
    github://contributors     – contributor list
    github://repo-info        – repository metadata
    github://commit-activity  – weekly commit activity
    github://pull-requests    – recent PRs
    github://branches         – branch list

Tools (callable actions):
    get_commits          – fetch commits with filters
    get_commit_detail    – inspect a single commit
    summarize_commits    – LLM-summarized commit report
    summarize_commit     – LLM-summarized single commit
    get_progress_report  – full LLM progress report for dashboard
    get_contributors     – fetch contributors
    get_repo_info        – fetch repo metadata
    get_pull_requests    – fetch recent PRs
    get_branches         – fetch branch list
"""
import json
import logging
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from . import github_client as gh
from . import summarizer as llm_summary
from .config import settings

logger = logging.getLogger(__name__)

# ── Create the MCP server instance ────────────────────────────────────────

mcp = FastMCP(
    "GitHub Commit Status Server",
    instructions=(
        "MCP server that exposes GitHub repository commit history, "
        "contributor stats, and LLM-powered progress summaries."
    ),
)


# ═══════════════════════════════════════════════════════════════════════════
#  RESOURCES  –  read-only data exposed as context to LLMs / clients
# ═══════════════════════════════════════════════════════════════════════════


@mcp.resource("github://commits")
def resource_commits() -> str:
    """Recent commits from the repository (last 7 days)."""
    commits = gh.get_recent_commits(since_days=7)
    return json.dumps(commits, indent=2)


@mcp.resource("github://contributors")
def resource_contributors() -> str:
    """Repository contributors with commit counts."""
    contributors = gh.get_contributors()
    return json.dumps(contributors, indent=2)


@mcp.resource("github://repo-info")
def resource_repo_info() -> str:
    """Basic repository metadata (name, language, stars, etc.)."""
    info = gh.get_repo_info()
    return json.dumps(info, indent=2)


@mcp.resource("github://commit-activity")
def resource_commit_activity() -> str:
    """Weekly commit activity for the past year."""
    activity = gh.get_commit_activity()
    return json.dumps(activity, indent=2)


@mcp.resource("github://pull-requests")
def resource_pull_requests() -> str:
    """Recent pull requests (all states)."""
    prs = gh.get_recent_pull_requests(state="all", per_page=15)
    return json.dumps(prs, indent=2)


@mcp.resource("github://branches")
def resource_branches() -> str:
    """List of repository branches."""
    branches = gh.get_branches()
    return json.dumps(branches, indent=2)


# ═══════════════════════════════════════════════════════════════════════════
#  TOOLS  –  callable actions
# ═══════════════════════════════════════════════════════════════════════════


@mcp.tool()
def get_commits(
    branch: Optional[str] = None,
    since_days: int = 7,
    per_page: int = 30,
) -> str:
    """
    Fetch recent commits from the GitHub repository.

    Args:
        branch: Branch name (defaults to configured default branch).
        since_days: Number of days to look back (default 7).
        per_page: Max number of commits to return (default 30).
    """
    commits = gh.get_recent_commits(branch=branch, since_days=since_days, per_page=per_page)
    return json.dumps(commits, indent=2)


@mcp.tool()
def get_commit_detail(sha: str) -> str:
    """
    Get detailed information about a single commit including file changes.

    Args:
        sha: The commit SHA (short or full).
    """
    detail = gh.get_commit_detail(sha)
    return json.dumps(detail, indent=2)


@mcp.tool()
def summarize_commits(
    branch: Optional[str] = None,
    since_days: int = 7,
) -> str:
    """
    Fetch recent commits and return an LLM-generated progress summary.

    Args:
        branch: Branch name (defaults to configured default branch).
        since_days: Number of days to look back (default 7).
    """
    commits = gh.get_recent_commits(branch=branch, since_days=since_days)
    summary = llm_summary.summarize_commits(commits)
    return summary


@mcp.tool()
def summarize_commit(sha: str) -> str:
    """
    Get an LLM-generated summary of a single commit's changes.

    Args:
        sha: The commit SHA (short or full).
    """
    detail = gh.get_commit_detail(sha)
    summary = llm_summary.summarize_commit_detail(detail)
    return summary


@mcp.tool()
def get_progress_report(since_days: int = 7) -> str:
    """
    Generate a comprehensive LLM-powered progress report for the dashboard.
    Combines commits, contributors, repo info, and PRs into a structured JSON report.

    Args:
        since_days: Number of days to look back (default 7).
    """
    commits = gh.get_recent_commits(since_days=since_days)
    contributors = gh.get_contributors()
    repo_info = gh.get_repo_info()
    prs = gh.get_recent_pull_requests(state="all", per_page=10)

    report = llm_summary.generate_progress_report(commits, contributors, repo_info, prs)
    return json.dumps(report, indent=2)


@mcp.tool()
def get_contributors() -> str:
    """Fetch repository contributors with their commit counts."""
    contributors = gh.get_contributors()
    return json.dumps(contributors, indent=2)


@mcp.tool()
def get_repo_info() -> str:
    """Fetch basic repository metadata."""
    info = gh.get_repo_info()
    return json.dumps(info, indent=2)


@mcp.tool()
def get_pull_requests(state: str = "all", per_page: int = 10) -> str:
    """
    Fetch recent pull requests.

    Args:
        state: Filter by state – 'open', 'closed', or 'all' (default 'all').
        per_page: Max number of PRs (default 10).
    """
    prs = gh.get_recent_pull_requests(state=state, per_page=per_page)
    return json.dumps(prs, indent=2)


@mcp.tool()
def get_branches() -> str:
    """List all branches in the repository."""
    branches = gh.get_branches()
    return json.dumps(branches, indent=2)
