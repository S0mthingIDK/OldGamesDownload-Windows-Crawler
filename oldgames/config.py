from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields
from pathlib import Path

ENV_PREFIX = "OGD_"


@dataclass(frozen=True)
class Config:
    base_url: str
    delay_seconds: float = 1.5
    timeout_seconds: int = 60
    user_agent: str = "OldGamesMetadataCrawler/2.0"
    retries: int = 3
    retry_delay_seconds: float = 3.0
    db_path: str = "oldgames.db"
    log_path: str = "oldgames.log.jsonl"
    max_workers: int = 6
    stale_after_days: int = 30
    append_version_suffix: bool = False

    @classmethod
    def load(cls, path: Path) -> "Config":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        allowed = {f.name for f in fields(cls)}
        unknown = set(raw) - allowed
        if unknown:
            raise ValueError(f"Unknown keys in config: {sorted(unknown)}")

        # Environment overrides (OGD_DB_PATH=..., OGD_MAX_WORKERS=8, ...)
        for name in list(allowed):
            env_key = ENV_PREFIX + name.upper()
            if env_key in os.environ:
                raw[name] = os.environ[env_key]

        cfg = cls(
            base_url=raw["base_url"],
            delay_seconds=float(raw.get("delay_seconds", 1.5)),
            timeout_seconds=int(raw.get("timeout_seconds", 60)),
            user_agent=raw.get("user_agent", "OldGamesMetadataCrawler/2.0"),
            retries=int(raw.get("retries", 3)),
            retry_delay_seconds=float(raw.get("retry_delay_seconds", 3)),
            db_path=str(raw.get("db_path", "oldgames.db")),
            log_path=str(raw.get("log_path", "oldgames.log.jsonl")),
            max_workers=int(raw.get("max_workers", 6)),
            stale_after_days=int(raw.get("stale_after_days", 30)),
            append_version_suffix=_to_bool(raw.get("append_version_suffix", False)),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        if self.delay_seconds < 0:
            raise ValueError("delay_seconds must be >= 0")
        if self.timeout_seconds < 1:
            raise ValueError("timeout_seconds must be >= 1")
        if self.retries < 0:
            raise ValueError("retries must be >= 0")
        if self.retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds must be >= 0")
        if self.max_workers < 1:
            raise ValueError("max_workers must be >= 1")
        if self.stale_after_days < 1:
            raise ValueError("stale_after_days must be >= 1")

    def resolve_db_path(self, root: Path) -> Path:
        p = Path(self.db_path)
        return p if p.is_absolute() else root / p

    def resolve_log_path(self, root: Path) -> Path:
        p = Path(self.log_path)
        return p if p.is_absolute() else root / p


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)