from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from .logging_setup import get_logger


class RateLimiter:
    """Per-host minimum interval between requests (thread-safe)."""

    def __init__(self, min_interval: float):
        self.min_interval = max(0.0, min_interval)
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def acquire(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            last = self._last.get(host, 0.0)
            wait = self.min_interval - (now - last)
            if wait > 0:
                time.sleep(wait)
            self._last[host] = time.monotonic()


class RequestError(Exception):
    def __init__(self, url: str, category: str, message: str):
        super().__init__(f"[{category}] {url}: {message}")
        self.url = url
        self.category = category
        self.message = message


class CurlClient:
    """HTTP client shelling out to Windows curl.exe.

    HTML is captured via stdout (no disk round-trip). Optional debug_dir
    saves the HTML to disk when a debug_name is provided.
    """

    def __init__(
        self,
        timeout_seconds: int,
        retries: int = 3,
        retry_delay_seconds: float = 2.0,
        rate_limiter: RateLimiter | None = None,
        debug_dir: Path | None = None,
    ):
        self.timeout_seconds = max(1, timeout_seconds)
        self.retries = max(0, retries)
        self.retry_delay_seconds = max(0.0, retry_delay_seconds)
        self.rate_limiter = rate_limiter
        self.debug_dir = debug_dir
        if self.debug_dir:
            self.debug_dir.mkdir(parents=True, exist_ok=True)

    def get(self, url: str, debug_name: str | None = None) -> str:
        host = urlparse(url).netloc or "unknown"
        attempts = self.retries + 1
        last_error = "unknown"
        last_category = "unknown"

        for attempt in range(1, attempts + 1):
            if self.rate_limiter:
                self.rate_limiter.acquire(host)

            t0 = time.monotonic()
            try:
                result = subprocess.run(
                    [
                        "curl.exe",
                        "--location",
                        "--silent",
                        "--show-error",
                        "--max-time",
                        str(self.timeout_seconds),
                        url,
                    ],
                    capture_output=True,
                    text=False,
                    check=False,
                )
            except FileNotFoundError as exc:
                raise RequestError(url, "curl-missing", str(exc)) from exc

            elapsed_ms = int((time.monotonic() - t0) * 1000)
            log = get_logger()

            if result.returncode == 0:
                html = result.stdout.decode("utf-8", errors="replace")
                if self.debug_dir and debug_name:
                    (self.debug_dir / debug_name).write_text(html, encoding="utf-8")
                if log:
                    log.log(url=url, status="ok", ms=elapsed_ms, attempt=attempt)
                return html

            stderr = (result.stderr or b"").decode("utf-8", errors="replace").strip()
            code = result.returncode
            category = _categorize(code)
            last_error = stderr or f"curl exit {code}"
            last_category = category

            if log:
                log.log(
                    url=url,
                    status="error",
                    ms=elapsed_ms,
                    attempt=attempt,
                    code=code,
                    category=category,
                    error=last_error,
                )

            if attempt < attempts:
                # Exponential backoff (rate-limit / transient safety net)
                time.sleep(self.retry_delay_seconds * (2 ** (attempt - 1)))

        raise RequestError(url, last_category, last_error)


def _categorize(curl_code: int) -> str:
    if curl_code == 28:
        return "timeout"
    if curl_code in (6, 7):
        return "dns"
    if curl_code in (22, 47):
        return "http"
    if curl_code in (52, 55, 56):
        return "transfer"
    return "curl"