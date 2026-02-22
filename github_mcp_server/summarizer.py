"""
LLM Summarizer – Uses Groq (Llama 3.1) to summarize GitHub commit history
and generate a human-readable progress report for the frontend dashboard.
"""
import json
import logging
from typing import Any, Dict, List, Optional

try:
    from langchain_groq import ChatGroq
    from langchain_core.messages import HumanMessage, SystemMessage
    from pydantic import SecretStr

    LANGCHAIN_AVAILABLE = True
except ImportError:
    ChatGroq = None  # type: ignore
    HumanMessage = None  # type: ignore
    SystemMessage = None  # type: ignore
    SecretStr = None  # type: ignore
    LANGCHAIN_AVAILABLE = False

from .config import settings

logger = logging.getLogger(__name__)

# ── LLM setup ─────────────────────────────────────────────────────────────


def _get_llm():
    """Build a ChatGroq LLM instance."""
    if not LANGCHAIN_AVAILABLE:
        raise RuntimeError("langchain-groq is not installed.")
    if not settings.groq_api_key:
        raise RuntimeError("GROQ_API_KEY is not set.")
    return ChatGroq(
        model=settings.groq_model,
        temperature=0.3,
        api_key=SecretStr(settings.groq_api_key),
        max_retries=2,
    )


# ── Public summarizers ────────────────────────────────────────────────────


def summarize_commits(commits: List[Dict[str, Any]]) -> str:
    """
    Summarize a list of commits into a concise progress report.

    Args:
        commits: List of commit dicts (sha, message, author, date …).

    Returns:
        A markdown-formatted progress summary string.
    """
    if not commits:
        return "No commits found for the requested period."

    llm = _get_llm()

    # Build a compact textual representation of commits
    commit_lines = []
    for c in commits[:40]:  # cap to avoid token overflow
        commit_lines.append(
            f"- [{c['sha']}] {c['author']} ({c.get('date', 'N/A')}): {c['message']}"
        )
    commit_text = "\n".join(commit_lines)

    system = SystemMessage(
        content=(
            "You are a software project analyst. Given a list of recent Git commits, "
            "produce a short, well-structured progress report in **Markdown**. "
            "Include:\n"
            "1. **Overview** – one-paragraph high-level summary.\n"
            "2. **Key Changes** – bullet list of the most important changes.\n"
            "3. **Active Contributors** – who contributed and roughly how much.\n"
            "4. **Areas of Activity** – which parts of the codebase were touched.\n"
            "Keep it concise (≤ 300 words). Do NOT invent information."
        )
    )
    human = HumanMessage(content=f"Here are the recent commits:\n\n{commit_text}")

    response = llm.invoke([system, human])
    summary = response.content if hasattr(response, "content") else str(response)
    logger.info("Generated commit summary via LLM")
    return summary


def summarize_commit_detail(detail: Dict[str, Any]) -> str:
    """
    Summarize a single commit's diff/changes.
    """
    if not detail:
        return "No commit detail provided."

    llm = _get_llm()

    files_text = ""
    for f in detail.get("files_changed", []):
        files_text += (
            f"  - {f['filename']} ({f['status']}) "
            f"+{f['additions']} -{f['deletions']}\n"
        )

    commit_text = (
        f"Commit: {detail['sha']}\n"
        f"Author: {detail['author']}\n"
        f"Date: {detail.get('date', 'N/A')}\n"
        f"Message: {detail['message']}\n"
        f"Stats: +{detail['stats']['additions']} -{detail['stats']['deletions']} "
        f"(total {detail['stats']['total']})\n"
        f"Files:\n{files_text}"
    )

    system = SystemMessage(
        content=(
            "You are a code reviewer assistant. Given a commit's details and changed files, "
            "write a brief summary (3–5 sentences) explaining what this commit does, "
            "the scope of changes, and any notable observations."
        )
    )
    human = HumanMessage(content=commit_text)

    response = llm.invoke([system, human])
    return response.content if hasattr(response, "content") else str(response)


def generate_progress_report(
    commits: List[Dict[str, Any]],
    contributors: List[Dict[str, Any]],
    repo_info: Dict[str, Any],
    pull_requests: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Generate a full progress report combining commits, contributors, PRs, and repo info.
    Returns a structured dict ready for the React frontend.
    """
    llm = _get_llm()

    # Build combined context
    context_parts = []

    # Repo info
    context_parts.append(
        f"Repository: {repo_info.get('name', 'N/A')}\n"
        f"Language: {repo_info.get('language', 'N/A')}\n"
        f"Stars: {repo_info.get('stars', 0)} | Forks: {repo_info.get('forks', 0)} | "
        f"Open Issues: {repo_info.get('open_issues', 0)}\n"
    )

    # Recent commits
    commit_lines = [
        f"- [{c['sha']}] {c['author']}: {c['message']}" for c in commits[:30]
    ]
    context_parts.append("Recent Commits:\n" + "\n".join(commit_lines))

    # Contributors
    contrib_lines = [
        f"- {c['login']}: {c['contributions']} commits" for c in contributors[:10]
    ]
    context_parts.append("Contributors:\n" + "\n".join(contrib_lines))

    # PRs
    if pull_requests:
        pr_lines = [
            f"- PR #{p['number']} ({p['state']}): {p['title']} by {p['author']}"
            for p in pull_requests[:10]
        ]
        context_parts.append("Recent Pull Requests:\n" + "\n".join(pr_lines))

    full_context = "\n\n".join(context_parts)

    system = SystemMessage(
        content=(
            "You are a project manager assistant. Given repository data, "
            "generate a JSON object with the following keys:\n"
            '  "summary": a 2-3 sentence overall progress summary,\n'
            '  "highlights": list of 3-5 key highlights (strings),\n'
            '  "risks": list of 0-3 potential risks or concerns (strings),\n'
            '  "contributor_summary": a sentence about team activity,\n'
            '  "velocity": a sentence about development speed/pace.\n'
            "Return ONLY valid JSON, no markdown fences."
        )
    )
    human = HumanMessage(content=full_context)

    response = llm.invoke([system, human])
    raw = response.content if hasattr(response, "content") else str(response)

    # Parse LLM JSON output
    try:
        report = json.loads(raw)
    except json.JSONDecodeError:
        # Fallback: wrap the raw text as the summary
        report = {
            "summary": raw,
            "highlights": [],
            "risks": [],
            "contributor_summary": "",
            "velocity": "",
        }

    # Attach raw data alongside the LLM report
    report["raw_data"] = {
        "total_commits": len(commits),
        "total_contributors": len(contributors),
        "repo": repo_info.get("name", ""),
        "period_days": 7,
    }

    logger.info("Generated full progress report via LLM")
    return report
