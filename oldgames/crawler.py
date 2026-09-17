from __future__ import annotations

import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from rich.console import Console
from rich.progress import (
    BarColumn, MofNCompleteColumn, Progress, SpinnerColumn,
    TextColumn, TimeElapsedColumn,
)
from rich.table import Table

from .database import (
    bulk_update_mirrors, list_recently_crawled_pages, mark_page_crawled,
    upsert_game_with_files_by_url,
)
from .http_client import CurlClient, RequestError
from .parser import (
    GameDetails, parse_catalog_page, parse_file_page, parse_game_page,
)


PAGE_URL_RE = re.compile(r"/platform/windows/page/(\d+)/?")
HARD_PAGE_CAP = 2000  # safety valve against runaway loops


@dataclass
class CrawlCounters:
    pages: int = 0
    games: int = 0
    files: int = 0
    mirror_urls: int = 0
    errors: int = 0


def valid_game_url(url: str) -> bool:
    parts = [p for p in urlparse(url).path.split("/") if p]
    return len(parts) == 2 and parts[0] == "game"


def catalog_page_url(base_url: str, page_number: int) -> str:
    if page_number <= 1:
        return base_url
    return urljoin(base_url, f"/platform/windows/page/{page_number}/")


def _max_page_from_pagination(pagination: list[str]) -> int | None:
    """Return the highest page number referenced by pagination links."""
    numbers: set[int] = set()
    for url in pagination:
        m = PAGE_URL_RE.search(url)
        if m:
            numbers.add(int(m.group(1)))
    return max(numbers) if numbers else None


def discover_total_pages(
    client: CurlClient, console: Console, base_url: str
) -> int | None:
    """Fetch page 1 and return the highest page number referenced."""
    try:
        html = client.get(base_url, debug_name="catalog.html")
    except RequestError as exc:
        console.print(f"[red]Failed to fetch page 1: {exc}[/red]")
        return None
    _, pagination = parse_catalog_page(html, base_url)
    total = _max_page_from_pagination(pagination)
    return total if total is not None else 1


def _process_game(
    client: CurlClient, game_url: str, check_mirrors: bool
) -> tuple[GameDetails, list, list[tuple[str, RequestError]]]:
    html = client.get(game_url, debug_name="game.html")
    details = parse_game_page(html, game_url)
    mirrors: list = []
    mirror_errors: list[tuple[str, RequestError]] = []

    if check_mirrors:
        for f in details.windows_files:
            try:
                fh = client.get(f.file_url, debug_name="file.html")
                mirrors.append((f.file_url, parse_file_page(fh)))
            except RequestError as exc:
                mirror_errors.append((f.file_url, exc))

    return details, mirrors, mirror_errors


def crawl_catalog(
    client: CurlClient,
    console: Console,
    base_url: str,
    start_page: int,
    max_pages: int,
    game_limit: int | None,
    check_mirrors_flag: bool,
    max_workers: int,
    crawl_all: bool = False,
    resume: bool = True,
    refresh: bool = False,
    page_max_age_days: int = 30,
) -> CrawlCounters:
    counters = CrawlCounters()
    errors_by_cat: Counter[str] = Counter()
    discovered: set[str] = set()

    # Determine the target page.
    if crawl_all:
        probe = discover_total_pages(client, console, base_url)
        if probe is None:
            console.print(
                "[yellow]Could not determine total pages; "
                "crawling page 1 only.[/yellow]"
            )
            target_page: int | None = start_page
        else:
            target_page = probe
            console.print(
                f"[cyan]Detected {target_page} catalog page(s) on the site.[/cyan]"
            )
    else:
        target_page = max_pages

    # Resume / skip set.
    skip_pages: set[int] = set()
    if refresh:
        console.print("[cyan]Refresh mode: re-crawling every page in range.[/cyan]")
    elif resume:
        skip_pages = list_recently_crawled_pages(page_max_age_days)
        if skip_pages:
            console.print(
                f"[cyan]{len(skip_pages)} page(s) already crawled within "
                f"{page_max_age_days}d; will skip them.[/cyan]"
            )
            console.print("[dim]Pass --refresh to force a full re-crawl.[/dim]")

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    )

    page_number = start_page
    empty_streak = 0
    pages_walked = 0
    pages_skipped = 0

    with progress:
        pages_task = progress.add_task(
            "Catalog pages",
            total=(None if crawl_all else max(0, (target_page or 0) - start_page + 1)),
        )
        games_task = progress.add_task("Games processed", total=None)

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            while True:
                if target_page is not None and page_number > target_page:
                    break
                if page_number > HARD_PAGE_CAP:
                    console.print(
                        f"[yellow]Hit safety cap of {HARD_PAGE_CAP} pages, "
                        "stopping.[/yellow]"
                    )
                    break

                # Skip already-crawled pages.
                if page_number in skip_pages:
                    pages_skipped += 1
                    page_number += 1
                    if not crawl_all:
                        progress.advance(pages_task)
                    else:
                        progress.update(
                            pages_task,
                            completed=pages_walked + pages_skipped,
                            total=target_page or (pages_walked + pages_skipped),
                        )
                    continue

                page_url = catalog_page_url(base_url, page_number)
                progress.update(
                    pages_task,
                    description=f"Catalog page {page_number}"
                    + (f"/{target_page}" if target_page else ""),
                )

                try:
                    html = client.get(page_url, debug_name="catalog.html")
                except RequestError as exc:
                    counters.errors += 1
                    errors_by_cat[exc.category] += 1
                    console.print(f"[red]  ✗ {exc}[/red]")
                    if crawl_all:
                        empty_streak += 1
                        if empty_streak >= 3:
                            console.print(
                                "[yellow]Three consecutive failed pages; "
                                "stopping.[/yellow]"
                            )
                            break
                    page_number += 1
                    if not crawl_all:
                        progress.advance(pages_task)
                    continue

                games, pagination = parse_catalog_page(html, base_url)
                counters.pages += 1
                pages_walked += 1

                if crawl_all:
                    discovered_max = _max_page_from_pagination(pagination)
                    if discovered_max is not None:
                        if target_page is None or discovered_max > target_page:
                            target_page = discovered_max
                            progress.update(pages_task, total=None)

                if not games:
                    empty_streak += 1
                    if crawl_all and empty_streak >= 2:
                        console.print(
                            "[yellow]Two consecutive empty catalog pages; "
                            "stopping.[/yellow]"
                        )
                        break
                else:
                    empty_streak = 0

                futures = {}
                for g in games:
                    if g.url in discovered or not valid_game_url(g.url):
                        continue
                    if game_limit is not None and counters.games + len(futures) >= game_limit:
                        break
                    discovered.add(g.url)
                    futures[pool.submit(
                        _process_game, client, g.url, check_mirrors_flag
                    )] = g

                page_errors = 0
                for fut in as_completed(futures):
                    g = futures[fut]
                    try:
                        details, mirrors, mirror_errors = fut.result()
                    except RequestError as exc:
                        counters.errors += 1
                        page_errors += 1
                        errors_by_cat[exc.category] += 1
                        console.print(f"[red]  ✗ {g.title}: {exc}[/red]")
                        progress.advance(games_task)
                        continue

                    upsert_game_with_files_by_url(
                        details.title, g.url, details.year,
                        details.description, details.windows_files,
                    )
                    counters.games += 1
                    counters.files += len(details.windows_files)
                    progress.advance(games_task)

                    if mirrors:
                        bulk_update_mirrors([
                            (url, m.mediafire_available, m.mediafire_url, m.mega_available)
                            for url, m in mirrors
                        ])
                        counters.mirror_urls += sum(
                            1 for _, m in mirrors if m.mediafire_url
                        )
                    for _url, err in mirror_errors:
                        counters.errors += 1
                        errors_by_cat[err.category] += 1

                    console.print(
                        f"  [green]✓[/green] {details.title} "
                        f"[dim]({len(details.windows_files)} files)[/dim]"
                    )

                # Mark the page as crawled (only clean pages count as 'ok').
                page_status = "ok" if page_errors == 0 else "partial"
                mark_page_crawled(page_number, page_url, len(games), page_status)

                if not crawl_all:
                    progress.advance(pages_task)
                else:
                    progress.update(
                        pages_task,
                        completed=pages_walked + pages_skipped,
                        total=target_page or (pages_walked + pages_skipped),
                    )

                page_number += 1

    _print_summary(
        console, counters, errors_by_cat, pages_walked, pages_skipped, crawl_all
    )
    return counters


def _print_summary(
    console: Console,
    counters: CrawlCounters,
    errors: Counter[str],
    pages_walked: int,
    pages_skipped: int,
    crawl_all: bool,
) -> None:
    table = Table(title="Crawl summary", show_header=False, box=None)
    table.add_row("Catalog pages walked", str(pages_walked))
    table.add_row("Catalog pages skipped", str(pages_skipped))
    table.add_row("Games processed", str(counters.games))
    table.add_row("Windows files", str(counters.files))
    table.add_row("MediaFire URLs captured", str(counters.mirror_urls))
    table.add_row(
        "Errors", f"[red]{counters.errors}[/red]" if counters.errors else "0"
    )
    console.print(table)

    if errors:
        err_table = Table(title="Errors by category", box=None)
        err_table.add_column("Category")
        err_table.add_column("Count", justify="right")
        for cat, n in sorted(errors.items(), key=lambda kv: -kv[1]):
            err_table.add_row(cat, str(n))
        console.print(err_table)