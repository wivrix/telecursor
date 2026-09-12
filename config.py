"""Application configuration loaded from environment / .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from paths import default_temp_upload_dir, env_path


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    bot_token: str = Field(..., min_length=10, description="Telegram Bot API token")
    allowed_users: str = Field(
        ...,
        description="Comma-separated Telegram user IDs and/or usernames",
    )
    allowed_workspace_path: Path = Field(
        ...,
        description="Jail root — agent may only run under this directory",
    )
    default_workspace_path: Path | None = Field(
        default=None,
        description="Initial workspace; defaults to allowed_workspace_path",
    )
    agent_bin: Path = Field(
        default=Path("agent"),
        description="Path to cursor-agent / agent executable",
    )
    cursor_api_key: str | None = Field(default=None)
    temp_upload_dir: Path | None = Field(default=None)
    stream_edit_interval: float = Field(default=1.5, ge=0.5, le=10.0)
    max_concurrent_runs_per_user: int = Field(default=1, ge=1, le=5)
    max_queue_size: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Max pending prompts queued per chat while an agent run is active",
    )
    default_mode: Literal["safe", "yolo"] = Field(default="safe")
    agent_model: str | None = Field(default=None)
    agent_effort: str | None = Field(
        default=None,
        description="Default model effort: low|medium|high|xhigh|max (or auto)",
    )
    log_level: str = Field(default="INFO")

    allowed_user_ids: set[int] = Field(default_factory=set, exclude=True)
    allowed_usernames: set[str] = Field(default_factory=set, exclude=True)

    @field_validator(
        "allowed_workspace_path",
        "default_workspace_path",
        "temp_upload_dir",
        mode="before",
    )
    @classmethod
    def _expand_path(cls, value: object) -> object:
        if value is None or value == "":
            return None
        if isinstance(value, (str, Path)):
            return Path(value).expanduser().resolve()
        return value

    @field_validator("agent_bin", mode="before")
    @classmethod
    def _expand_agent_bin(cls, value: object) -> object:
        if isinstance(value, (str, Path)):
            p = Path(value).expanduser()
            if p.exists() or p.is_absolute() or "/" in str(value) or "\\" in str(value):
                return p.resolve() if p.exists() else p.expanduser()
            return p
        return value

    @field_validator("agent_model", mode="before")
    @classmethod
    def _normalize_model(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        if not text or text.lower() in {"auto", "default", "none"}:
            return None
        return text

    @field_validator("agent_effort", mode="before")
    @classmethod
    def _normalize_effort(cls, value: object) -> object:
        if value is None or value == "":
            return None
        from effort import normalize_effort

        return normalize_effort(str(value))

    @field_validator("cursor_api_key", mode="before")
    @classmethod
    def _empty_api_key(cls, value: object) -> object:
        if value is None or str(value).strip() == "":
            return None
        return value

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def _finalize(self) -> Settings:
        ids: set[int] = set()
        names: set[str] = set()
        for raw in self.allowed_users.split(","):
            token = raw.strip()
            if not token:
                continue
            if token.lstrip("-").isdigit():
                ids.add(int(token))
            else:
                names.add(token.lstrip("@").lower())
        if not ids and not names:
            raise ValueError("ALLOWED_USERS must contain at least one user id or username")
        object.__setattr__(self, "allowed_user_ids", ids)
        object.__setattr__(self, "allowed_usernames", names)

        root = self.allowed_workspace_path.resolve()
        if not root.exists():
            raise ValueError(f"ALLOWED_WORKSPACE_PATH does not exist: {root}")
        if not root.is_dir():
            raise ValueError(f"ALLOWED_WORKSPACE_PATH is not a directory: {root}")
        object.__setattr__(self, "allowed_workspace_path", root)

        default = self.default_workspace_path or root
        default = default.resolve()
        if not _is_within(default, root):
            raise ValueError(
                f"DEFAULT_WORKSPACE_PATH ({default}) must be inside "
                f"ALLOWED_WORKSPACE_PATH ({root})"
            )
        if not default.exists() or not default.is_dir():
            raise ValueError(f"DEFAULT_WORKSPACE_PATH is not a directory: {default}")
        object.__setattr__(self, "default_workspace_path", default)

        temp = (self.temp_upload_dir or default_temp_upload_dir()).resolve()
        temp.mkdir(parents=True, exist_ok=True)
        object.__setattr__(self, "temp_upload_dir", temp)

        return self

    def is_allowed_user(self, user_id: int | None, username: str | None) -> bool:
        if user_id is not None and user_id in self.allowed_user_ids:
            return True
        if username and username.lstrip("@").lower() in self.allowed_usernames:
            return True
        return False

    @property
    def model_label(self) -> str:
        return self.agent_model or "auto"

    @property
    def effort_label(self) -> str:
        return self.agent_effort or "auto"


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def validate_workspace(candidate: str | Path, allowed_root: Path) -> Path:
    root = allowed_root.resolve()
    raw = Path(candidate).expanduser()
    resolved = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if not _is_within(resolved, root):
        raise ValueError("Path escapes allowed workspace jail")
    if not resolved.exists() or not resolved.is_dir():
        raise ValueError(f"Workspace path does not exist or is not a directory: {resolved}")
    return resolved


@lru_cache
def get_settings() -> Settings:
    return Settings(_env_file=env_path())  # type: ignore[call-arg]


def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()
