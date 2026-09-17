from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from rich.console import Console

from . import __version__
from .config import Config
from .crawler import crawl_catalog, discover_total_pages
from .database import (
    add_uri, cleanup_non_game_rows, clear_catalog_pages, get_game, init_db,
    list_files_for_game, list_recently_crawled_pages, page_crawl_overview,
    set_db_path,
)
from .display import (
    confirm, print_banner, print_coverage_report, print_duplicates, print_list,
    print_search_results, print_stats,
)
from .exporter import export_hydra_json
from .http_client import CurlClient, RateLimiter
from .logging_setup import setup_logger
from .mirror_checker import check_mirrors, watch_mirrors
from .review import change_status, interactive_review


ROOT = Path(__file__).resolve().parent.parent


def _build_client(cfg: Config) -> CurlClient:
    return CurlClient(
        timeout_seconds=cfg.timeout_seconds,
        retries=cfg.retries,
        retry_delay_seconds=cfg.retry_delay_seconds,
        rate_limiter=RateLimiter(cfg.delay_seconds),
        debug_dir=ROOT / "debug_html",
    )


def _add_common_confirm(p: argparse.ArgumentParser) -> None:
    p.add_argument("--yes", "-y", action="store_true",
                   help="skip confirmation prompts")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scraper.py",
        description="OldGamesDownload Windows metadata crawler",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    # crawl
    c = sub.add_parser(
        "crawl",
        help="crawl catalog + game metadata + MediaFire URLs",
    )
    c.add_argument("--start-page", type=int, default=1)
    c.add_argument("--pages", type=int, default=None,
                   help="last catalog page to crawl (ignored when --all is set)")
    c.add_argument("--all", action="store_true",
                   help="crawl every catalog page until the site runs out")
    c.add_argument("--limit", type=int, default=None)
    c.add_argument("--no-mirrors", action="store_true",
                   help="skip MediaFire mirror checks during the crawl")
    c.add_argument("--workers", type=int, default=None,
                   help="override config max_workers")
    c.add_argument("--resume", action="store_true",
                   help="skip pages already crawled recently "
                        "(on by default with --all)")
    c.add_argument("--no-resume", dest="resume", action="store_false",
                   help="disable page skipping")
    c.add_argument("--refresh", action="store_true",
                   help="ignore page cache and re-crawl everything in range")
    c.set_defaults(resume=None)

    # page-count
    sub.add_parser(
        "page-count",
        help="quickly probe how many catalog pages exist right now",
    )

    # pages
    sub.add_parser("pages", help="show which catalog pages have been crawled")

    # pages-clear
    sub.add_parser("pages-clear", help="wipe the page crawl cache")

    # check-mirrors
    m = sub.add_parser("check-mirrors", help="re-check mirror URLs for stored files")
    m.add_argument("--status", choices=["review", "approved", "blocked"], default=None)
    m.add_argument("--limit", type=int, default=None)
    m.add_argument("--skip-checked", action="store_true",
                   help="skip files checked more recently than stale_after_days")
    m.add_argument("--workers", type=int, default=None)
    m.add_argument("--watch", action="store_true", help="run continuously")
    m.add_argument("--interval", type=int, default=86400,
                   help="seconds between watch runs (default 24h)")

    # list
    ls = sub.add_parser("list", help="list games and Windows files")
    ls.add_argument("--status", choices=["review", "approved", "blocked"], default=None)
    ls.add_argument("--verbose", "-v", action="store_true", help="show files per game")

    # find
    fd = sub.add_parser("find", help="search titles and filenames")
    fd.add_argument("query")
    fd.add_argument("--status", choices=["review", "approved", "blocked"], default=None)
    fd.add_argument("--with-mediafire", dest="mf", action="store_true", default=None)
    fd.add_argument("--without-mediafire", dest="mf", action="store_false")
    fd.add_argument("--open", type=int, metavar="N",
                    help="open the Nth match in the default browser")

    # stats / report / duplicates
    sub.add_parser("stats", help="print database totals")
    sub.add_parser("report", help="print mirror coverage report")
    sub.add_parser("duplicates", help="list filenames appearing under multiple games")

    # status / add-uri / cleanup
    st = sub.add_parser("status", help="change a game's review status")
    st.add_argument("game_id", type=int)
    st.add_argument("new_status", choices=["review", "approved", "blocked"])
    _add_common_confirm(st)

    au = sub.add_parser("add-uri", help="add a manual URI to a game")
    au.add_argument("game_id", type=int)
    au.add_argument("uri")
    _add_common_confirm(au)

    cl = sub.add_parser("cleanup", help="remove non-game rows")
    _add_common_confirm(cl)

    # review
    rv = sub.add_parser("review", help="interactive approve/block review")
    rv.add_argument("--status", choices=["review", "approved", "blocked"],
                    default="review")

    # export
    ex = sub.add_parser("export", help="export Hydra-shaped JSON")
    ex.add_argument("--output", default="oldgames.json")
    ex.add_argument("--stream", action="store_true",
                    help="write incrementally (lower memory for huge DBs)")

    # serve
    sv = sub.add_parser("serve", help="run optional local web UI")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)

    return p


def main(argv: list[str] | None = None) -> int:
    cfg = Config.load(ROOT / "config.json")
    db_path = cfg.resolve_db_path(ROOT)
    log_path = cfg.resolve_log_path(ROOT)

    set_db_path(db_path)
    setup_logger(log_path)
    init_db()

    args = build_parser().parse_args(argv)
    console = Console()

    if args.command not in {"export", "stats", "report", "duplicates"}:
        print_banner(console, cfg, db_path)

    if args.command == "page-count":
        client = _build_client(cfg)
        total = discover_total_pages(client, console, cfg.base_url)
        if total is None:
            console.print("[red]Could not determine page count.[/red]")
            return 1
        console.print(
            f"[green]Catalog currently reports [bold]{total}[/bold] page(s).[/green]"
        )
        crawled = list_recently_crawled_pages(cfg.stale_after_days)
        console.print(
            f"Locally crawled (within {cfg.stale_after_days}d): "
            f"[cyan]{len(crawled)}[/cyan] page(s)."
        )
        remaining = total - len({p for p in crawled if p <= total})
        console.print(f"Remaining: [yellow]{max(0, remaining)}[/yellow] page(s).")
        return 0

    if args.command == "pages":
        rows = page_crawl_overview()
        if not rows:
            console.print("[yellow]No catalog pages recorded yet.[/yellow]")
            return 0
        from rich.table import Table
        t = Table(title=f"Crawled pages ({len(rows)})")
        t.add_column("Page", justify="right", style="cyan")
        t.add_column("Games", justify="right")
        t.add_column("Status")
        t.add_column("Last crawled")
        for r in rows:
            style = "green" if r["status"] == "ok" else "yellow"
            t.add_row(
                str(r["page_number"]),
                str(r["games_found"]),
                f"[{style}]{r['status']}[/{style}]",
                r["last_crawled_at"],
            )
        console.print(t)
        return 0

    if args.command == "pages-clear":
        n = clear_catalog_pages()
        console.print(f"[green]Cleared {n} page-cache row(s).[/green]")
        return 0

    if args.command == "crawl":
        if args.all:
            if args.pages is not None:
                console.print(
                    "[yellow]--pages is ignored when --all is set.[/yellow]"
                )
            max_pages = 0
        else:
            if args.pages is None:
                console.print(
                    "[red]Either --pages N or --all is required.[/red]"
                )
                return 2
            max_pages = max(1, args.pages)

        if args.refresh:
            resume = False
        elif args.resume is None:
            resume = bool(args.all)
        else:
            resume = args.resume

        workers = args.workers or cfg.max_workers
        client = _build_client(cfg)
        crawl_catalog(
            client, console,
            base_url=cfg.base_url,
            start_page=max(1, args.start_page),
            max_pages=max_pages,
            game_limit=args.limit,
            check_mirrors_flag=not args.no_mirrors,
            max_workers=workers,
            crawl_all=args.all,
            resume=resume,
            refresh=args.refresh,
            page_max_age_days=cfg.stale_after_days,
        )
        return 0

    if args.command == "check-mirrors":
        workers = args.workers or cfg.max_workers
        client = _build_client(cfg)
        if args.watch:
            watch_mirrors(
                client, console, args.status, args.limit,
                args.skip_checked, cfg.stale_after_days, workers, args.interval,
            )
        else:
            check_mirrors(
                client, console, args.status, args.limit,
                args.skip_checked, cfg.stale_after_days, workers,
            )
        return 0

    if args.command == "list":
        print_list(console, args.status, cfg.stale_after_days, verbose=args.verbose)
        return 0

    if args.command == "find":
        rows = print_search_results(console, args.query, args.status, args.mf)
        if args.open is not None and rows:
            idx = args.open - 1
            if 0 <= idx < len(rows):
                g = rows[idx]
                files = list_files_for_game(g["id"])
                target = next(
                    (f["mediafire_url"] for f in files if f["mediafire_url"]),
                    None,
                ) or g["game_url"]
                webbrowser.open(target)
                console.print(f"[cyan]Opened: {target}[/cyan]")
            else:
                console.print(f"[red]Index out of range: {args.open}[/red]")
        return 0

    if args.command == "stats":
        print_stats(console, cfg.stale_after_days)
        return 0

    if args.command == "report":
        print_coverage_report(console, cfg.stale_after_days)
        return 0

    if args.command == "duplicates":
        print_duplicates(console)
        return 0

    if args.command == "status":
        change_status(console, args.game_id, args.new_status, args.yes)
        return 0

    if args.command == "add-uri":
        game = get_game(args.game_id)
        if not game:
            raise SystemExit(f"Game ID {args.game_id} not found.")
        if not confirm(
            console,
            f"Add URI to [{args.game_id}] {game['title']}?",
            args.yes,
        ):
            console.print("[yellow]Cancelled.[/yellow]")
            return 0
        add_uri(args.game_id, args.uri)
        console.print(f"[green]Added URI to [{args.game_id}] {game['title']}[/green]")
        return 0

    if args.command == "cleanup":
        if not confirm(
            console,
            "Delete all rows whose game_url does not contain '/game/'?",
            args.yes,
        ):
            console.print("[yellow]Cancelled.[/yellow]")
            return 0
        removed = cleanup_non_game_rows()
        console.print(f"[green]Removed {removed} row(s).[/green]")
        return 0

    if args.command == "review":
        interactive_review(console, args.status)
        return 0

    if args.command == "export":
        count = export_hydra_json(
            args.output,
            append_suffix=cfg.append_version_suffix,
            stream=args.stream,
        )
        console.print(f"[green]Exported {count} record(s) to {args.output}[/green]")
        return 0

    if args.command == "serve":
        from .server import run as run_server
        console.print(
            f"[cyan]Serving on http://{args.host}:{args.port}  (Ctrl+C to stop)[/cyan]"
        )
        run_server(args.host, args.port, cfg.stale_after_days, db_path)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())