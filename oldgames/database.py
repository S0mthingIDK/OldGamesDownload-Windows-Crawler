from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .parser import GameDetails

_db_path: Path = Path("oldgames.db")
_write_lock = threading.Lock()


def set_db_path(path: Path) -> None:
    global _db_path
    _db_path = Path(path)
    _db_path.parent.mkdir(parents=True, exist_ok=True)


def get_db_path() -> Path:
    return _db_path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    conn = sqlite3.connect(_db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                game_url TEXT NOT NULL UNIQUE,
                year INTEGER,
                description TEXT,
                status TEXT NOT NULL DEFAULT 'review'
                    CHECK (status IN ('review', 'approved', 'blocked')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS windows_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id INTEGER NOT NULL,
                file_url TEXT NOT NULL UNIQUE,
                file_name TEXT NOT NULL,
                file_size TEXT,
                year INTEGER,
                mediafire_available INTEGER NOT NULL DEFAULT 0,
                mediafire_url TEXT,
                mega_available INTEGER NOT NULL DEFAULT 0,
                mirror_checked_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS approved_uris (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id INTEGER NOT NULL,
                uri TEXT NOT NULL,
                UNIQUE(game_id, uri),
                FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS catalog_pages (
                page_number INTEGER PRIMARY KEY,
                page_url TEXT NOT NULL,
                games_found INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'ok',
                last_crawled_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_games_status ON games(status);
            CREATE INDEX IF NOT EXISTS idx_files_game_id ON windows_files(game_id);
            CREATE INDEX IF NOT EXISTS idx_files_mirror_checked
                ON windows_files(mirror_checked_at);
            CREATE INDEX IF NOT EXISTS idx_files_file_name
                ON windows_files(file_name COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS idx_pages_last_crawled
                ON catalog_pages(last_crawled_at);
            """
        )
        _migrate_windows_files(conn)


def _migrate_windows_files(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(windows_files)")}
    additions = {
        "mediafire_available": "INTEGER NOT NULL DEFAULT 0",
        "mediafire_url": "TEXT",
        "mega_available": "INTEGER NOT NULL DEFAULT 0",
        "mirror_checked_at": "TEXT",
        "created_at": "TEXT",
        "updated_at": "TEXT",
    }
    for name, definition in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE windows_files ADD COLUMN {name} {definition}")

    now = utc_now()
    conn.execute(
        "UPDATE windows_files SET created_at = COALESCE(created_at, ?), "
        "updated_at = COALESCE(updated_at, ?)",
        (now, now),
    )


# ---------------------------------------------------------------------------
# Batched writes
# ---------------------------------------------------------------------------

def upsert_game_with_files_by_url(
    title: str,
    game_url: str,
    year: int | None,
    description: str | None,
    files: list,
) -> int:
    """Insert/update a game and its files in one transaction. Returns game id."""
    now = utc_now()
    with _write_lock, connect() as conn:
        row = conn.execute(
            "SELECT id FROM games WHERE game_url = ?", (game_url,)
        ).fetchone()
        if row:
            game_id = int(row["id"])
            conn.execute(
                """
                UPDATE games
                SET title = ?,
                    year = COALESCE(?, year),
                    description = COALESCE(?, description),
                    updated_at = ?
                WHERE id = ?
                """,
                (title, year, description, now, game_id),
            )
        else:
            cur = conn.execute(
                """
                INSERT INTO games
                    (title, game_url, year, description, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'review', ?, ?)
                """,
                (title, game_url, year, description, now, now),
            )
            game_id = int(cur.lastrowid)

        for f in files:
            conn.execute(
                """
                INSERT INTO windows_files
                    (game_id, file_url, file_name, file_size, year, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_url) DO UPDATE SET
                    game_id = excluded.game_id,
                    file_name = excluded.file_name,
                    file_size = COALESCE(excluded.file_size, windows_files.file_size),
                    year = COALESCE(excluded.year, windows_files.year),
                    updated_at = excluded.updated_at
                """,
                (game_id, f.file_url, f.file_name, f.file_size, f.year, now, now),
            )

        return game_id


def bulk_update_mirrors(rows: list[tuple[str, bool, str | None, bool]]) -> None:
    """rows: list of (file_url, mediafire_available, mediafire_url, mega_available)."""
    if not rows:
        return
    now = utc_now()
    with _write_lock, connect() as conn:
        conn.executemany(
            """
            UPDATE windows_files
            SET mediafire_available = ?,
                mediafire_url = ?,
                mega_available = ?,
                mirror_checked_at = ?,
                updated_at = ?
            WHERE file_url = ?
            """,
            [
                (int(mf_av), mf_url, int(mg_av), now, now, url)
                for url, mf_av, mf_url, mg_av in rows
            ],
        )


def update_mirror_status(
    file_url: str,
    mediafire_available: bool,
    mediafire_url: str | None,
    mega_available: bool,
) -> None:
    bulk_update_mirrors([(file_url, mediafire_available, mediafire_url, mega_available)])


# ---------------------------------------------------------------------------
# Simple mutations
# ---------------------------------------------------------------------------

def set_status(game_id: int, status: str) -> None:
    if status not in {"review", "approved", "blocked"}:
        raise ValueError(f"Invalid status: {status}")
    with _write_lock, connect() as conn:
        conn.execute(
            "UPDATE games SET status = ?, updated_at = ? WHERE id = ?",
            (status, utc_now(), game_id),
        )


def add_uri(game_id: int, uri: str) -> None:
    with _write_lock, connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO approved_uris (game_id, uri) VALUES (?, ?)",
            (game_id, uri),
        )


def cleanup_non_game_rows() -> int:
    with _write_lock, connect() as conn:
        rows = conn.execute(
            "SELECT id FROM games WHERE game_url NOT LIKE '%/game/%'"
        ).fetchall()
        for row in rows:
            conn.execute("DELETE FROM games WHERE id = ?", (row["id"],))
        return len(rows)


# ---------------------------------------------------------------------------
# Page crawl cache
# ---------------------------------------------------------------------------

def mark_page_crawled(
    page_number: int,
    page_url: str,
    games_found: int,
    status: str = "ok",
) -> None:
    """Record that a catalog page has been crawled.

    status is 'ok' | 'partial' | 'error'. Only 'ok' rows participate in
    resume skipping.
    """
    if status not in {"ok", "partial", "error"}:
        raise ValueError(f"Invalid page status: {status}")
    with _write_lock, connect() as conn:
        conn.execute(
            """
            INSERT INTO catalog_pages
                (page_number, page_url, games_found, status, last_crawled_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(page_number) DO UPDATE SET
                page_url = excluded.page_url,
                games_found = excluded.games_found,
                status = excluded.status,
                last_crawled_at = excluded.last_crawled_at
            """,
            (page_number, page_url, games_found, status, utc_now()),
        )


def list_recently_crawled_pages(max_age_days: int) -> set[int]:
    """Return page numbers crawled successfully within max_age_days."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=max_age_days)
    ).isoformat(timespec="seconds")
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT page_number FROM catalog_pages
            WHERE status = 'ok' AND last_crawled_at >= ?
            """,
            (cutoff,),
        ).fetchall()
    return {int(r["page_number"]) for r in rows}


def page_crawl_overview() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM catalog_pages ORDER BY page_number"
        ).fetchall()


def clear_catalog_pages() -> int:
    with _write_lock, connect() as conn:
        cur = conn.execute("DELETE FROM catalog_pages")
        return cur.rowcount


# ---------------------------------------------------------------------------
# Read queries
# ---------------------------------------------------------------------------

def list_games(status: str | None = None) -> list[sqlite3.Row]:
    with connect() as conn:
        if status:
            return conn.execute(
                "SELECT * FROM games WHERE status = ? ORDER BY title COLLATE NOCASE",
                (status,),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM games ORDER BY title COLLATE NOCASE"
        ).fetchall()


def get_game(game_id: int) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM games WHERE id = ?", (game_id,)
        ).fetchone()


def list_files_for_game(game_id: int) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            """
            SELECT * FROM windows_files
            WHERE game_id = ?
            ORDER BY file_name COLLATE NOCASE
            """,
            (game_id,),
        ).fetchall()


def list_files_for_status(status: str) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            """
            SELECT wf.*, g.title AS game_title, g.status
            FROM windows_files wf
            JOIN games g ON g.id = wf.game_id
            WHERE g.status = ?
            ORDER BY g.title COLLATE NOCASE, wf.file_name COLLATE NOCASE
            """,
            (status,),
        ).fetchall()


def list_all_games_for_export() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM games ORDER BY title COLLATE NOCASE"
        ).fetchall()


def list_files_needing_mirror_check(
    status: str | None = None,
    skip_checked: bool = False,
    stale_after_days: int = 30,
) -> list[sqlite3.Row]:
    clauses = []
    params: list = []
    if status:
        clauses.append("g.status = ?")
        params.append(status)
    if skip_checked:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=stale_after_days)
        ).isoformat(timespec="seconds")
        clauses.append("(wf.mirror_checked_at IS NULL OR wf.mirror_checked_at < ?)")
        params.append(cutoff)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"""
        SELECT wf.*, g.title AS game_title, g.status
        FROM windows_files wf
        JOIN games g ON g.id = wf.game_id
        {where}
        ORDER BY g.title COLLATE NOCASE, wf.file_name COLLATE NOCASE
    """
    with connect() as conn:
        return conn.execute(sql, params).fetchall()


def search_games(
    query: str,
    status: str | None = None,
    has_mediafire: bool | None = None,
) -> list[sqlite3.Row]:
    like = f"%{query}%"
    clauses = ["(g.title LIKE ? OR EXISTS ("
               "SELECT 1 FROM windows_files wf "
               "WHERE wf.game_id = g.id AND wf.file_name LIKE ?))"]
    params: list = [like, like]
    if status:
        clauses.append("g.status = ?")
        params.append(status)
    if has_mediafire is True:
        clauses.append("EXISTS (SELECT 1 FROM windows_files wf "
                       "WHERE wf.game_id = g.id AND wf.mediafire_url IS NOT NULL)")
    elif has_mediafire is False:
        clauses.append("NOT EXISTS (SELECT 1 FROM windows_files wf "
                       "WHERE wf.game_id = g.id AND wf.mediafire_url IS NOT NULL)")

    sql = f"""
        SELECT g.*,
               (SELECT COUNT(*) FROM windows_files wf WHERE wf.game_id = g.id) AS file_count,
               (SELECT COUNT(*) FROM windows_files wf
                WHERE wf.game_id = g.id AND wf.mediafire_url IS NOT NULL) AS mf_count
        FROM games g
        WHERE {' AND '.join(clauses)}
        ORDER BY g.title COLLATE NOCASE
    """
    with connect() as conn:
        return conn.execute(sql, params).fetchall()


def find_duplicate_filenames() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            """
            SELECT wf.file_name,
                   COUNT(DISTINCT wf.game_id) AS game_count,
                   GROUP_CONCAT(DISTINCT g.title) AS games
            FROM windows_files wf
            JOIN games g ON g.id = wf.game_id
            GROUP BY LOWER(wf.file_name)
            HAVING game_count > 1
            ORDER BY game_count DESC, wf.file_name COLLATE NOCASE
            """
        ).fetchall()


def coverage_report(stale_after_days: int = 30) -> dict:
    with connect() as conn:
        games = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
        files = conn.execute("SELECT COUNT(*) FROM windows_files").fetchone()[0]
        with_mf = conn.execute(
            "SELECT COUNT(*) FROM windows_files WHERE mediafire_url IS NOT NULL"
        ).fetchone()[0]
        mf_avail = conn.execute(
            "SELECT COUNT(*) FROM windows_files WHERE mediafire_available = 1"
        ).fetchone()[0]
        mega = conn.execute(
            "SELECT COUNT(*) FROM windows_files WHERE mega_available = 1"
        ).fetchone()[0]
        no_mirror = conn.execute(
            """
            SELECT COUNT(*) FROM windows_files
            WHERE mediafire_available = 0 AND mega_available = 0
              AND mirror_checked_at IS NOT NULL
            """
        ).fetchone()[0]
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=stale_after_days)
        ).isoformat(timespec="seconds")
        stale = conn.execute(
            "SELECT COUNT(*) FROM windows_files "
            "WHERE mirror_checked_at IS NULL OR mirror_checked_at < ?",
            (cutoff,),
        ).fetchone()[0]

        by_status = {
            row["status"]: row["n"]
            for row in conn.execute(
                "SELECT status, COUNT(*) AS n FROM games GROUP BY status"
            )
        }

        return {
            "games": games,
            "files": files,
            "mediafire_urls": with_mf,
            "mediafire_available": mf_avail,
            "mega_available": mega,
            "no_mirror": no_mirror,
            "stale_or_unchecked": stale,
            "by_status": by_status,
            "stale_after_days": stale_after_days,
        }


def stats() -> dict[str, int]:
    return coverage_report()