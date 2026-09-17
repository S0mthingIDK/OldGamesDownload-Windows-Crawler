from __future__ import annotations

from datetime import datetime, timedelta, timezone

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .database import (
    coverage_report, find_duplicate_filenames, list_files_for_game,
    list_games, search_games,
)

_STATUS_STYLE = {
    "approved": "green",
    "review": "yellow",
    "blocked": "red",
}


def _status_cell(status: str) -> str:
    return f"[{_STATUS_STYLE.get(status, 'white')}]{status}[/]"


def _is_stale(ts: str | None, stale_after_days: int) -> bool:
    if not ts:
        return True
    try:
        when = datetime.fromisoformat(ts)
    except ValueError:
        return True
    return when < datetime.now(timezone.utc) - timedelta(days=stale_after_days)


def print_list(
    console: Console,
    status: str | None,
    stale_after_days: int,
    verbose: bool = False,
) -> None:
    games = list_games(status)
    if not games:
        console.print("[yellow]No games found.[/yellow]")
        return

    table = Table(title=f"Games ({len(games)})", show_lines=False)
    table.add_column("ID", justify="right", style="cyan")
    table.add_column("Title")
    table.add_column("Status")
    table.add_column("Files", justify="right")
    table.add_column("MediaFire", justify="right")
    table.add_column("Stale", justify="right")

    for game in games:
        files = list_files_for_game(game["id"])
        mf = sum(1 for f in files if f["mediafire_url"])
        stale = sum(1 for f in files if _is_stale(f["mirror_checked_at"], stale_after_days))
        table.add_row(
            str(game["id"]),
            game["title"],
            _status_cell(game["status"]),
            str(len(files)),
            f"{mf}/{len(files)}",
            f"[red]{stale}[/red]" if stale else "0",
        )
        if verbose:
            for f in files:
                url = f["mediafire_url"] or "—"
                table.add_row(
                    "", f"  ↳ [dim]{f['file_name']}[/dim]", "",
                    f["file_size"] or "?", url, "",
                )

    console.print(table)


def print_stats(console: Console, stale_after_days: int) -> None:
    r = coverage_report(stale_after_days)
    table = Table(title="Database stats", show_header=False, box=None)
    table.add_row("Games", str(r["games"]))
    table.add_row("Windows files", str(r["files"]))
    table.add_row("MediaFire URLs captured", str(r["mediafire_urls"]))
    table.add_row("MediaFire available", str(r["mediafire_available"]))
    table.add_row("MEGA available", str(r["mega_available"]))
    table.add_row("No mirror available", f"[red]{r['no_mirror']}[/red]" if r["no_mirror"] else "0")
    table.add_row(
        f"Stale / unchecked (>{stale_after_days}d)",
        f"[yellow]{r['stale_or_unchecked']}[/yellow]" if r["stale_or_unchecked"] else "0",
    )
    console.print(table)

    if r["by_status"]:
        st = Table(title="By status", box=None)
        st.add_column("Status")
        st.add_column("Games", justify="right")
        for s, n in r["by_status"].items():
            st.add_row(_status_cell(s), str(n))
        console.print(st)


def print_coverage_report(console: Console, stale_after_days: int) -> None:
    r = coverage_report(stale_after_days)
    pct = lambda n, d: f"{(100 * n / d):.1f}%" if d else "—"
    table = Table(title="Mirror coverage report")
    table.add_column("Metric")
    table.add_column("Count", justify="right")
    table.add_column("Share", justify="right")
    table.add_row("Games", str(r["games"]), "")
    table.add_row("Windows files", str(r["files"]), "")
    table.add_row("MediaFire URLs captured", str(r["mediafire_urls"]),
                  pct(r["mediafire_urls"], r["files"]))
    table.add_row("MediaFire available", str(r["mediafire_available"]),
                  pct(r["mediafire_available"], r["files"]))
    table.add_row("MEGA available", str(r["mega_available"]),
                  pct(r["mega_available"], r["files"]))
    table.add_row("No mirror at all", str(r["no_mirror"]),
                  pct(r["no_mirror"], r["files"]))
    table.add_row(
        f"Stale / unchecked (>{stale_after_days}d)",
        str(r["stale_or_unchecked"]), pct(r["stale_or_unchecked"], r["files"]),
    )
    console.print(table)


def print_search_results(
    console: Console,
    query: str,
    status: str | None,
    has_mediafire: bool | None,
) -> list:
    rows = search_games(query, status, has_mediafire)
    if not rows:
        console.print(f"[yellow]No matches for {query!r}.[/yellow]")
        return []

    table = Table(title=f"Matches for {query!r} ({len(rows)})")
    table.add_column("#", justify="right", style="cyan")
    table.add_column("Title")
    table.add_column("Status")
    table.add_column("Files", justify="right")
    table.add_column("MF", justify="right")
    for i, r in enumerate(rows, 1):
        table.add_row(
            str(i), r["title"], _status_cell(r["status"]),
            str(r["file_count"]), f"{r['mf_count']}/{r['file_count']}",
        )
    console.print(table)
    return rows


def print_duplicates(console: Console) -> None:
    rows = find_duplicate_filenames()
    if not rows:
        console.print("[green]No duplicate filenames across games.[/green]")
        return
    table = Table(title=f"Duplicate filenames ({len(rows)})")
    table.add_column("Filename")
    table.add_column("Games", justify="right")
    table.add_column("Titles")
    for r in rows:
        table.add_row(r["file_name"], str(r["game_count"]), r["games"])
    console.print(table)


def confirm(console: Console, message: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    from rich.prompt import Confirm
    return Confirm.ask(message, console=console, default=False)


def print_banner(console: Console, cfg, db_path) -> None:
    body = (
        f"[bold]base_url[/bold]  {cfg.base_url}\n"
        f"[bold]db[/bold]        {db_path}\n"
        f"[bold]workers[/bold]   {cfg.max_workers}    "
        f"[bold]delay[/bold] {cfg.delay_seconds}s    "
        f"[bold]timeout[/bold] {cfg.timeout_seconds}s\n"
        f"[bold]log[/bold]       {cfg.log_path}"
    )
    console.print(Panel(body, title="oldgames crawler", border_style="cyan"))