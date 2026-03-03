from pathlib import Path
from typing import List, Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve once at module level
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # Project Root
    PROJECT_ROOT: Path = _PROJECT_ROOT

    # Telegram Bot Settings
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_ALLOWED_USERS: Any = Field(default_factory=list)
    TELEGRAM_ADMIN_USER: int

    # Anthropic Proxy Settings
    ANTHROPIC_BASE_URL: str = "http://localhost:8080"
    ANTHROPIC_API_KEY: str = "dummy"
    ANTHROPIC_MODEL: str = "claude-opus-4-6-thinking"
    ANTHROPIC_MAX_TOKENS: int = 8192

    # Agent Execution Settings
    AGENT_MAX_TOOL_ITERATIONS: int = 200
    AGENT_TOOL_TIMEOUT: int = 120
    AGENT_WORKSPACE: Path = _PROJECT_ROOT / "data"

    # Memory & Embeddings
    EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"
    DATABASE_PATH: Path = _PROJECT_ROOT / "data" / "anti-claw.db"

    # Safety & Sandboxing
    ALLOWED_PATHS: Any = Field(default_factory=list)
    BLOCKED_COMMANDS: Any = Field(default_factory=list)

    @field_validator("TELEGRAM_ALLOWED_USERS", mode="before")
    @classmethod
    def parse_allowed_users(cls, v):
        if isinstance(v, str):
            return [int(u.strip()) for u in v.split(",") if u.strip()]
        return v

    @field_validator("ALLOWED_PATHS", mode="before")
    @classmethod
    def parse_allowed_paths(cls, v):
        if isinstance(v, str):
            return [Path(p.strip()).resolve() for p in v.split(",") if p.strip()]
        return v

    @field_validator("BLOCKED_COMMANDS", mode="before")
    @classmethod
    def parse_blocked_commands(cls, v):
        if isinstance(v, str):
            return [c.strip() for c in v.split(",") if c.strip()]
        return v

    model_config = SettingsConfigDict(
        env_file=_PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
