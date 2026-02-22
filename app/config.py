"""
Configuration module for environment variables.
"""
import os
from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # Database
    database_url: str = Field(
        default="postgresql://user:password@localhost:5432/meet_processor",
        description="PostgreSQL connection URL"
    )
    postgres_user: str = Field(default="postgres")
    postgres_password: str = Field(default="password")
    postgres_db: str = Field(default="meet_processor")
    postgres_host: str = Field(default="localhost")
    postgres_port: int = Field(default=5432)
    
    # Recording Transcription Settings
    recordings_dir: str = Field(
        default="./recordings",
        description="Directory to watch for new recording files"
    )
    whisper_model_size: str = Field(
        default="base",
        description="Whisper model size: tiny, base, small, medium, large-v2, large-v3"
    )
    whisper_device: str = Field(
        default="cpu",
        description="Device for Whisper: cpu or cuda"
    )
    whisper_compute_type: str = Field(
        default="int8",
        description="Compute type for Whisper: int8, float16, float32"
    )
    
    # Jira Configuration
    jira_server: str = Field(
        default="https://your-domain.atlassian.net",
        description="Jira Cloud server URL"
    )
    jira_email: str = Field(default="")
    jira_api_token: str = Field(default="")
    jira_project_key: str = Field(default="PROJ")
    
    # LLM Configuration (Groq)
    groq_api_key: str = Field(default="")
    groq_model: str = Field(default="llama-3.1-8b-instant")
    
    # Confluence Configuration
    confluence_base_url: str = Field(
        default="https://your-domain.atlassian.net",
        description="Confluence Cloud base URL"
    )
    confluence_email: str = Field(default="")
    confluence_api_token: str = Field(default="")
    confluence_space_key: str = Field(
        default="MEET",
        description="Confluence space key for meeting pages"
    )
    
    # Polling Configuration
    recordings_poll_interval: int = Field(
        default=30,
        description="Interval in seconds to check for new recordings"
    )
    
    # Application Settings
    app_env: str = Field(default="development")
    debug: bool = Field(default=True)
    log_level: str = Field(default="INFO")
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    """
    Get cached settings instance.
    Uses lru_cache to ensure settings are loaded only once.
    """
    return Settings()


# Convenience function for direct access
settings = get_settings()
