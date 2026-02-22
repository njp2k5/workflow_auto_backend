"""
GitHub API client – fetches commits, contributors, repo stats, and PR data.
All methods are sync-friendly; async wrappers are in the MCP server layer.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

from .config import settings

logger = logging.getLogger(__name__)

# ── Helpers ────────────────────────────────────────────────────────────────


def _headers() -> Dict[str, str]:
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if settings.github_token:
        h["Authorization"] = f"Bearer {settings.github_token}"
    return h


def _repo_url(path: str = "") -> str:
    base = settings.github_api_base.rstrip("/")
    return f"{base}/repos/{settings.github_owner}/{settings.github_repo}{path}"


# ── Core fetch helper ─────────────────────────────────────────────────────


def _get(url: str, params: Optional[Dict[str, Any]] = None) -> Any:
    """Perform a GET request and return parsed JSON."""
    with httpx.Client(timeout=30) as client:
        resp = client.get(url, headers=_headers(), params=params)
        resp.raise_for_status()
        return resp.json()


# ── Public functions ──────────────────────────────────────────────────────


def get_recent_commits(
    branch: Optional[str] = None,
    since_days: int = 7,
    per_page: int = 30,
) -> List[Dict[str, Any]]:
    """
    Fetch recent commits from the repo.

    Args:
        branch: Branch name (defaults to configured default branch).
        since_days: How many days back to look.
        per_page: Max commits to return.

    Returns:
        List of simplified commit dicts.
    """
    branch = branch or settings.github_default_branch
    since = (datetime.now(timezone.utc) - timedelta(days=since_days)).isoformat()

    raw = _get(
        _repo_url("/commits"),
        params={"sha": branch, "since": since, "per_page": per_page},
    )

    commits = []
    for c in raw:
        commit_info = c.get("commit", {})
        author_info = commit_info.get("author", {})
        committer_info = commit_info.get("committer", {})
        commits.append(
            {
                "sha": c.get("sha", "")[:7],
                "full_sha": c.get("sha", ""),
                "message": commit_info.get("message", ""),
                "author": author_info.get("name", "Unknown"),
                "author_email": author_info.get("email", ""),
                "date": author_info.get("date", ""),
                "committer": committer_info.get("name", ""),
                "url": c.get("html_url", ""),
            }
        )

    logger.info(f"Fetched {len(commits)} commits from {branch} (last {since_days}d)")
    return commits


def get_commit_detail(sha: str) -> Dict[str, Any]:
    """
    Fetch detailed info for a single commit (including file changes).
    """
    raw = _get(_repo_url(f"/commits/{sha}"))
    files_changed = []
    for f in raw.get("files", []):
        files_changed.append(
            {
                "filename": f.get("filename", ""),
                "status": f.get("status", ""),
                "additions": f.get("additions", 0),
                "deletions": f.get("deletions", 0),
                "changes": f.get("changes", 0),
                "patch": (f.get("patch", "") or "")[:500],  # truncate large patches
            }
        )

    commit = raw.get("commit", {})
    stats = raw.get("stats", {})
    return {
        "sha": raw.get("sha", "")[:7],
        "full_sha": raw.get("sha", ""),
        "message": commit.get("message", ""),
        "author": commit.get("author", {}).get("name", "Unknown"),
        "date": commit.get("author", {}).get("date", ""),
        "stats": {
            "additions": stats.get("additions", 0),
            "deletions": stats.get("deletions", 0),
            "total": stats.get("total", 0),
        },
        "files_changed": files_changed,
        "url": raw.get("html_url", ""),
    }


def get_contributors() -> List[Dict[str, Any]]:
    """Fetch repository contributors with commit counts."""
    raw = _get(_repo_url("/contributors"), params={"per_page": 50})
    return [
        {
            "login": c.get("login", ""),
            "avatar_url": c.get("avatar_url", ""),
            "contributions": c.get("contributions", 0),
            "profile_url": c.get("html_url", ""),
        }
        for c in raw
    ]


def get_commit_activity() -> List[Dict[str, Any]]:
    """
    Weekly commit activity for the last year (GitHub stats endpoint).
    Returns list of {week_timestamp, total, days[Sun..Sat]}.
    May return empty on first call (GitHub computes in background).
    """
    try:
        raw = _get(_repo_url("/stats/commit_activity"))
        if not isinstance(raw, list):
            return []
        return [
            {
                "week": datetime.fromtimestamp(w["week"], tz=timezone.utc).isoformat(),
                "total": w.get("total", 0),
                "days": w.get("days", []),
            }
            for w in raw
        ]
    except Exception as exc:
        logger.warning(f"commit_activity not ready yet: {exc}")
        return []


def get_repo_info() -> Dict[str, Any]:
    """Fetch basic repository metadata."""
    raw = _get(_repo_url())
    return {
        "name": raw.get("full_name", ""),
        "description": raw.get("description", ""),
        "default_branch": raw.get("default_branch", ""),
        "language": raw.get("language", ""),
        "stars": raw.get("stargazers_count", 0),
        "forks": raw.get("forks_count", 0),
        "open_issues": raw.get("open_issues_count", 0),
        "created_at": raw.get("created_at", ""),
        "updated_at": raw.get("updated_at", ""),
        "url": raw.get("html_url", ""),
    }


def get_recent_pull_requests(
    state: str = "all",
    per_page: int = 10,
) -> List[Dict[str, Any]]:
    """Fetch recent pull requests."""
    raw = _get(
        _repo_url("/pulls"),
        params={"state": state, "per_page": per_page, "sort": "updated", "direction": "desc"},
    )
    return [
        {
            "number": pr.get("number"),
            "title": pr.get("title", ""),
            "state": pr.get("state", ""),
            "author": (pr.get("user") or {}).get("login", ""),
            "created_at": pr.get("created_at", ""),
            "updated_at": pr.get("updated_at", ""),
            "merged_at": pr.get("merged_at"),
            "url": pr.get("html_url", ""),
        }
        for pr in raw
    ]


def get_branches() -> List[Dict[str, Any]]:
    """List repository branches."""
    raw = _get(_repo_url("/branches"), params={"per_page": 50})
    return [
        {
            "name": b.get("name", ""),
            "sha": (b.get("commit") or {}).get("sha", "")[:7],
            "protected": b.get("protected", False),
        }
        for b in raw
    ]
