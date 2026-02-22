# GitHub MCP Server

A **standalone MCP server** that integrates with the GitHub API to expose commit history, contributor stats, and repository data. It includes an **LLM-powered summarizer** (via Groq) and a **REST API bridge** so the React frontend can consume the data directly.

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                GitHub REST API (v3)                  │
└────────────────────────┬─────────────────────────────┘
                         │
              ┌──────────▼──────────┐
              │   github_client.py  │  ← fetches commits, PRs, contributors
              └──────────┬──────────┘
                         │
          ┌──────────────┼──────────────┐
          │              │              │
  ┌───────▼──────┐ ┌────▼──────┐ ┌─────▼──────┐
  │  server.py   │ │  api.py   │ │summarizer  │
  │  (MCP Proto) │ │ (REST API)│ │  (Groq LLM)│
  └──────────────┘ └───────────┘ └────────────┘
        │                │
  MCP Clients       React Frontend
  (Claude, Cline)   (dashboard)
```

## Files

| File | Purpose |
|---|---|
| `config.py` | Settings from `.env` (GitHub token, Groq key, etc.) |
| `github_client.py` | GitHub API wrapper (commits, PRs, contributors, branches) |
| `summarizer.py` | LLM-powered summaries via Groq (Llama 3.1 8B) |
| `server.py` | MCP server – resources & tools for MCP clients |
| `api.py` | REST API bridge – HTTP endpoints for React frontend |
| `__main__.py` | Entry-point with multi-mode runner |

---

## Setup

### 1. Install dependencies

```bash
pip install -r github_mcp_server/requirements.txt
```

### 2. Configure environment variables

Add these to your `.env` file (in the project root):

```env
# GitHub
GITHUB_TOKEN=ghp_your_personal_access_token
GITHUB_OWNER=your-github-username-or-org
GITHUB_REPO=your-repo-name
GITHUB_DEFAULT_BRANCH=main

# Groq LLM (reuse existing key)
GROQ_API_KEY=gsk_your_groq_key
GROQ_MODEL=llama-3.1-8b-instant

# Server
MCP_SERVER_PORT=3003
```

### 3. Create a GitHub Personal Access Token

Go to **GitHub → Settings → Developer Settings → Personal Access Tokens → Fine-grained tokens**

Required permissions:
- `Contents: Read` (commits, files)
- `Metadata: Read` (repo info)
- `Pull requests: Read`

---

## Running

### Option A: MCP Server (for Claude Desktop / Cline / MCP clients)

```bash
python -m github_mcp_server --mode mcp-stdio
```

### Option B: REST API (for React frontend)

```bash
python -m github_mcp_server --mode rest
# or directly:
uvicorn github_mcp_server.api:app --host 0.0.0.0 --port 3003 --reload
```

### Option C: MCP over SSE

```bash
python -m github_mcp_server --mode mcp-sse
```

---

## REST API Endpoints (for React Frontend)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check |
| `GET` | `/api/commits?since_days=7&branch=main` | Recent commits |
| `GET` | `/api/commits/{sha}` | Single commit detail |
| `GET` | `/api/commits/{sha}/summary` | LLM summary of a commit |
| `GET` | `/api/commits-summary?since_days=7` | LLM summary of all recent commits |
| `GET` | `/api/progress-report?since_days=7` | Full LLM progress report (JSON) |
| `GET` | `/api/contributors` | Repository contributors |
| `GET` | `/api/repo-info` | Repository metadata |
| `GET` | `/api/commit-activity` | Weekly commit activity (last year) |
| `GET` | `/api/pull-requests?state=all` | Recent pull requests |
| `GET` | `/api/branches` | List branches |

---

## MCP Resources (for LLM context)

| URI | Description |
|-----|-------------|
| `github://commits` | Recent commits (last 7 days) |
| `github://contributors` | Contributor list with commit counts |
| `github://repo-info` | Repository metadata |
| `github://commit-activity` | Weekly commit activity |
| `github://pull-requests` | Recent pull requests |
| `github://branches` | Branch list |

## MCP Tools (callable actions)

| Tool | Description |
|------|-------------|
| `get_commits` | Fetch commits with branch/date filters |
| `get_commit_detail` | Detailed info for a single commit |
| `summarize_commits` | LLM-generated progress summary |
| `summarize_commit` | LLM summary of one commit |
| `get_progress_report` | Full dashboard progress report |
| `get_contributors` | Fetch contributor stats |
| `get_repo_info` | Fetch repo metadata |
| `get_pull_requests` | Fetch recent PRs |
| `get_branches` | List branches |

---

## React Frontend Usage

```tsx
// Example: Fetch progress report from the REST bridge
const fetchProgressReport = async () => {
  const res = await fetch("http://localhost:3003/api/progress-report?since_days=7");
  const report = await res.json();

  // report = {
  //   summary: "The team made significant progress...",
  //   highlights: ["Added authentication module", ...],
  //   risks: ["Test coverage is low", ...],
  //   contributor_summary: "3 active contributors this week",
  //   velocity: "Development pace is steady with 15 commits",
  //   raw_data: { total_commits: 15, total_contributors: 3, ... }
  // }

  setReport(report);
};
```

---

## Claude Desktop / Cline Configuration

Add to your MCP client config (e.g. `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "github-commits": {
      "command": "python",
      "args": ["-m", "github_mcp_server"],
      "cwd": "/path/to/workflow_auto_backend",
      "env": {
        "GITHUB_TOKEN": "ghp_...",
        "GITHUB_OWNER": "your-org",
        "GITHUB_REPO": "your-repo",
        "GROQ_API_KEY": "gsk_..."
      }
    }
  }
}
```
