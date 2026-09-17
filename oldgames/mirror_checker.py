from __future__ import annotations

import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from rich.console import Console
from rich.progress import (
    BarColumn, MofNCompleteColumn, Progress, SpinnerColumn,
    TextColumn, TimeElapsedColumn,
)
from rich.table import Table

from .database import bulk_update_mirrors, list_files_needing_mirror_check
from .http_client import CurlClient, RequestError
from .parser import parse_file_page


def _check_one(client: CurlClient, file_url: str):
    try:
        html = client.get(file_url, debug_name="file.html")
        return file_url, parse_file_page(html), None
    except RequestError as exc:
        return file_url, None, exc


def check_mirrors(
    client: CurlClient,
    console: Console,
    status: str | None,
    limit: int | None,
    skip_checked: bool,
    stale_after_days: int,
    max_workers: int,
) -> None:
    files = list_files_needing_mirror_check(status, skip_checked, stale_after_days)
    if limit is not None:
        files = files[:limit]

    if not files:
        console.print("[yellow]No files to check.[/yellow]")
        return

    errors: Counter[str] = Counter()
    captured = 0
    checked = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    ) as progress:
        task = progress.add_task("Mirror checks", total=len(files))
        pending: list[tuple[str, bool, str | None, bool]] = []

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for url, mirrors, err in pool.map(
                lambda f: _check_one(client, f["file_url"]), files
            ):
                if err:
                    errors[err.category] += 1
                else:
                    pending.append(
                        (url, mirrors.mediafire_available, mirrors.mediafire_url, mirrors.mega_available)
                    )
                    if mirrors.mediafire_url:
                        captured += 1
                    checked += 1
                progress.advance(task)

                if len(pending) >= 50:
                    bulk_update_mirrors(pending)
                    pending.clear()

        if pending:
            bulk_update_mirrors(pending)

    table = Table(title="Mirror check summary", show_header=False, box=None)
    table.add_row("Checked", str(checked))
    table.add_row("MediaFire URLs captured", str(captured))
    table.add_row("Errors", f"[red]{sum(errors.values())}[/red]" if errors else "0")
    console.print(table)
    if errors:
        for cat, n in errors.most_common():
            console.print(f"  [red]{cat}[/red]: {n}")


def watch_mirrors(
    client: CurlClient,
    console: Console,
    status: str | None,
    limit: int | None,
    skip_checked: bool,
    stale_after_days: int,
    max_workers: int,
    interval_seconds: int,
) -> None:
    console.print(
        f"[cyan]Watch mode active. Re-checking every {interval_seconds}s. Ctrl+C to stop.[/cyan]"
    )
    try:
        while True:
            console.rule(f"[bold]Run at {datetime.now().isoformat(timespec='seconds')}")
            check_mirrors(
                client, console, status, limit, skip_checked,
                stale_after_days, max_workers,
            )
            console.print(f"[dim]Sleeping {interval_seconds}s...[/dim]")
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        console.print("\n[yellow]Watch mode stopped.[/yellow]")