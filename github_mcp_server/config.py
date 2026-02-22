"""Configuration for the GitHub MCP Server."""
import os
import logging
from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load .env from project root (one level above github_mcp_server/)
# Uses Path(__file__) so it works regardless of cwd.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"

if _ENV_FILE.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_ENV_FILE, override=False)
    except ImportError:
        with open(_ENV_FILE, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


class GitHubMCPSettings(BaseSettings):
    """Settings for the GitHub MCP Server."""

    github_token: str = Field(default="")
    github_owner: str = Field(default="")
    github_repo: str = Field(default="")
    github_api_base: str = Field(default="https://api.github.com")
    github_default_branch: str = Field(default="main")

    groq_api_key: str = Field(default="")
    groq_model: str = Field(default="llama-3.1-8b-instant")

    mcp_server_host: str = Field(default="0.0.0.0")
    mcp_server_port: int = Field(default=3003)
    log_level: str = Field(default="INFO")

    class Config:
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"


@lru_cache()
def get_settings() -> GitHubMCPSettings:
    """Return a cached settings instance."""
    return GitHubMCPSettings()


settings = get_settings()
