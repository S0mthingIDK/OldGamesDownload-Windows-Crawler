from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

_lock = threading.Lock()
_logger: "JsonLineLogger | None" = None


class JsonLineLogger:
    """Append-only JSON-lines logger. One JSON object per line."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, **fields) -> None:
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **fields,
        }
        line = json.dumps(rec, ensure_ascii=False)
        with _lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")


def setup_logger(path: Path) -> JsonLineLogger:
    global _logger
    _logger = JsonLineLogger(path)
    return _logger


def get_logger() -> JsonLineLogger | None:
    return _logger