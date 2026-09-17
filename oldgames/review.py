from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from .database import get_game, list_files_for_game, list_games, set_status
from .display import _status_cell, confirm


def interactive_review(console: Console, start_status: str = "review") -> None:
    games = list_games(start_status)
    if not games:
        console.print(f"[yellow]No games with status {start_status!r}.[/yellow]")
        return

    console.print(
        f"[cyan]{len(games)} game(s) to review. "
        "Commands: [a]pprove  [b]lock  [s]kip  [o]pen  [q]uit[/cyan]"
    )

    for idx, game in enumerate(games, 1):
        files = list_files_for_game(game["id"])
        lines = [
            f"[bold]{game['title']}[/bold]",
            f"Status: {_status_cell(game['status'])}   "
            f"Year: {game['year'] or '?'}   Files: {len(files)}",
        ]
        for f in files[:5]:
            url = f["mediafire_url"] or "—"
            lines.append(f"  • {f['file_name']}  [dim]{f['file_size'] or '?'}[/dim]")
            lines.append(f"    [blue]{url}[/blue]")
        if len(files) > 5:
            lines.append(f"  … and {len(files) - 5} more")

        console.print(Panel("\n".join(lines), title=f"[{idx}/{len(games)}] id={game['id']}"))

        choice = Prompt.ask(
            "[a]pprove / [b]lock / [s]kip / [o]pen first / [q]uit",
            choices=["a", "b", "s", "o", "q"],
            default="s",
        ).lower()

        if choice == "q":
            console.print("[yellow]Stopped.[/yellow]")
            return
        if choice == "a":
            set_status(game["id"], "approved")
            console.print("[green]Approved.[/green]")
        elif choice == "b":
            set_status(game["id"], "blocked")
            console.print("[red]Blocked.[/red]")
        elif choice == "o":
            import webbrowser
            target = next((f["mediafire_url"] for f in files if f["mediafire_url"]), None)
            if target:
                webbrowser.open(target)
                console.print("[cyan]Opened in browser.[/cyan]")
            else:
                console.print("[yellow]No MediaFire URL to open.[/yellow]")


def change_status(console: Console, game_id: int, new_status: str, assume_yes: bool) -> None:
    game = get_game(game_id)
    if not game:
        raise SystemExit(f"Game ID {game_id} not found.")
    if not confirm(
        console,
        f"Change [{game_id}] {game['title']} from {game['status']} to {new_status}?",
        assume_yes,
    ):
        console.print("[yellow]Cancelled.[/yellow]")
        return
    set_status(game_id, new_status)
    console.print(f"Updated [{game_id}] {game['title']} -> {new_status}")