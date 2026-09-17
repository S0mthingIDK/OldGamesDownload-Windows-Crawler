# OldGamesDownload Windows Metadata Crawler

A Python crawler that harvests the OldGamesDownload Windows catalog and
produces a Hydra-compatible JSON catalog. Game metadata, Windows file
metadata, and captured MediaFire mirror URLs are stored in SQLite along the
way.

---

## Table of contents

- [What it does](#what-it-does)
- [Pipeline](#pipeline)
- [Requirements](#requirements)
- [Setup](#setup)
- [Quick start](#quick-start)
- [Commands](#commands)
  - [`crawl`](#crawl)
  - [`page-count`](#page-count)
  - [`pages` / `pages-clear`](#pages--pages-clear)
  - [`check-mirrors`](#check-mirrors)
  - [`list`](#list)
  - [`find`](#find)
  - [`stats` / `report` / `duplicates`](#stats--report--duplicates)
  - [`status` / `add-uri` / `cleanup`](#status--add-uri--cleanup)
  - [`review`](#review)
  - [`export`](#export)
  - [`serve`](#serve)
- [Configuration](#configuration)
- [Environment variables](#environment-variables)
- [Database schema](#database-schema)
- [Hydra JSON format](#hydra-json-format)
- [Multi-version games](#multi-version-games)
- [Web UI](#web-ui)
- [Logs](#logs)
- [Crawl resilience](#crawl-resilience)
- [Project layout](#project-layout)

---

## What it does

1. Crawls every catalog page of the OldGamesDownload Windows section.
2. Visits each game page and extracts title, release year, description,
   and every Windows download file (name + size).
3. Visits each download file page and captures the MediaFire mirror URL
   (if present) plus a MEGA availability flag.
4. Stores everything in a local SQLite database (`oldgames.db`) using WAL
   mode and batched writes.
5. Exports a Hydra-shaped JSON catalog with MediaFire URLs as the primary
   `uris`, falling back to the OldGamesDownload file-page URL when no
   MediaFire mirror exists.

---

## Pipeline

```text
OldGamesDownload
        │
        ▼
crawl catalog + game pages + file pages   (parallel workers, resumable)
        │
        ▼
oldgames.db                               (WAL, batched writes, page cache)
        │
        ▼
oldgames.json                             (Hydra-compatible)
```

---

## Requirements

- Python 3.10 or newer
- Windows `curl.exe` on `PATH` (bundled with Windows 10/11)
- Internet access to `oldgamesdownload.com` and `mediafire.com`

---

## Setup

```powershell
cd D:\test\OldGamesDownload-Windows-Crawler (or wherever you want to put it)
pip install -r requirements.txt
```

The database (`oldgames.db`) is created automatically the first time you run
any command that touches it.

---

## Quick start

```powershell
# 1. Crawl the first catalog page (metadata + MediaFire URLs)
python scraper.py crawl --pages 1 --limit 10

# 2. Inspect what was stored
python scraper.py list
python scraper.py stats

# 3. Export the Hydra JSON
python scraper.py export --output oldgames.json
```

---

## Commands

### `crawl`

Crawls catalog pages and game pages in parallel, upserts rows, and (by
default) captures MediaFire URLs.

You can either specify an explicit page range with `--pages N`, or use
`--all` to let the crawler detect the last page automatically. When
`--all` is used, previously crawled pages are skipped (see
[Resumable crawls](#resumable-crawls)).

| Flag | Default | Description |
|------|---------|-------------|
| `--start-page N` | `1` | First catalog page (inclusive) |
| `--pages N` | none | Last catalog page (inclusive). Required unless `--all` is set |
| `--all` | off | Crawl every catalog page until the site runs out. `--pages` is ignored |
| `--limit N` | none | Stop after N new game URLs |
| `--no-mirrors` | off | Skip mirror checks (faster metadata-only pass) |
| `--workers N` | config | Override `max_workers` |
| `--resume` | on for `--all` | Skip pages already crawled recently |
| `--no-resume` | — | Disable page skipping |
| `--refresh` | off | Ignore page cache and re-crawl everything in range |

**Explicit page range**

```powershell
python scraper.py crawl --start-page 1 --pages 5
python scraper.py crawl --start-page 17 --pages 342
python scraper.py crawl --pages 20 --no-mirrors
```

**Crawl the whole site without knowing the page count**

```powershell
python scraper.py crawl --all
```

`--all` works like this:

1. Fetches page 1, reads the pagination links, and computes the highest page
   number the site currently reports.
2. Skips pages already crawled within `stale_after_days` (default 30) —
   see [Resumable crawls](#resumable-crawls).
3. Crawls the remaining pages sequentially.
4. **Extends the target automatically** if later pages reveal a higher page
   number (sites sometimes grow while you crawl).
5. **Stops early** after two consecutive empty catalog pages, or three
   consecutive failed fetches — so a site hiccup doesn't cause an infinite
   loop.
6. **Hard safety cap** of 2000 pages in case pagination is ever broken.

**Resume after a break**

```powershell
# First run, interrupted at page 125
python scraper.py crawl --all

# Next day: automatically continues from page 126
python scraper.py crawl --all
```

**Force a full re-crawl**

```powershell
python scraper.py crawl --all --refresh
```

---

### Resumable crawls

Every catalog page that is crawled successfully is recorded in the
`catalog_pages` table along with a timestamp and how many games were found.

On the next `crawl --all`, pages crawled within `stale_after_days` are
skipped, and only the remaining pages are fetched. This means:

- Stopping at page 125 and re-running `crawl --all` resumes at page 126.
- Partial pages (fetched OK but with some game failures) are re-attempted.
- Pages older than `stale_after_days` are re-crawled automatically to pick
  up any changes on the site.
- `--refresh` forces every page in range to be re-crawled regardless of
  the cache.
- `--pages N` (explicit range) still re-crawls those pages by default —
  pass `--resume` to skip them instead.

Toggle the cache or inspect it with the [`pages`](#pages--pages-clear)
and [`pages-clear`](#pages--pages-clear) commands.

---

### `page-count`

Probes the site and prints how many catalog pages currently exist, plus a
comparison against your local crawl progress.

```powershell
python scraper.py page-count
```

Output:

```text
Catalog currently reports 342 page(s).
Locally crawled (within 30d): 125 page(s).
Remaining: 217 page(s).
```

---

### `pages` / `pages-clear`

`pages` shows every catalog page you've crawled, when it was last touched,
and how many games it yielded.

```powershell
python scraper.py pages
```

Output:

```
Crawled pages (125)
 Page  Games  Status   Last crawled
    1     24  ok       2026-01-14T09:12:33+00:00
    2     24  ok       2026-01-14T09:13:05+00:00
    ...
  124     24  ok       2026-01-14T10:41:51+00:00
  125     24  partial  2026-01-14T10:42:14+00:00
```

Status legend:

- `ok` — page and all its games fetched cleanly
- `partial` — page fetched but at least one game on it failed

`pages-clear` wipes the page cache so the next `crawl --all` starts from
scratch (the game and file data itself is untouched).

```powershell
python scraper.py pages-clear
```

---

### `check-mirrors`

Re-checks mirror availability and MediaFire URLs for stored files.

| Flag | Default | Description |
|------|---------|-------------|
| `--status {review,approved,blocked}` | all | Filter by game status |
| `--limit N` | none | Max file pages to check |
| `--skip-checked` | off | Skip rows checked within `stale_after_days` |
| `--workers N` | config | Override `max_workers` |
| `--watch` | off | Run continuously |
| `--interval N` | `86400` | Seconds between watch runs |

```powershell
python scraper.py check-mirrors --skip-checked
python scraper.py check-mirrors --watch --interval 3600
```

---

### `list`

Formatted table of games with file / MediaFire counts.

```powershell
python scraper.py list
python scraper.py list --status approved
python scraper.py list -v          # show files per game
```

---

### `find`

Search titles and filenames.

```powershell
python scraper.py find "need for speed"
python scraper.py find blur --with-mediafire
python scraper.py find wolverine --open 1
```

---

### `stats` / `report` / `duplicates`

```powershell
python scraper.py stats         # quick totals
python scraper.py report        # mirror coverage report with percentages
python scraper.py duplicates    # same filename under multiple games
```

---

### `status` / `add-uri` / `cleanup`

All destructive commands ask for confirmation unless `--yes` is passed.

```powershell
python scraper.py status 19 approved
python scraper.py add-uri 19 "YOUR_VERIFIED_URI"
python scraper.py cleanup --yes
```

---

### `review`

Interactive approve/block workflow:

```powershell
python scraper.py review
python scraper.py review --status review
```

Keys: `a` approve, `b` block, `s` skip, `o` open MediaFire in browser,
`q` quit.

---

### `export`

```powershell
python scraper.py export --output oldgames.json
python scraper.py export --output oldgames.json --stream
```

---

### `serve`

Optional local web UI:

```powershell
python scraper.py serve --port 8000
```

Then open <http://127.0.0.1:8000>. See [Web UI](#web-ui).

---

## Configuration

`config.json` at the project root:

```json
{
  "base_url": "https://oldgamesdownload.com/platform/windows/",
  "delay_seconds": 1.5,
  "timeout_seconds": 60,
  "user_agent": "OldGamesMetadataCrawler/2.0",
  "retries": 3,
  "retry_delay_seconds": 3,
  "db_path": "oldgames.db",
  "log_path": "oldgames.log.jsonl",
  "max_workers": 6,
  "stale_after_days": 30,
  "append_version_suffix": false
}
```

| Key | Purpose |
|-----|---------|
| `base_url` | Entry point of the Windows catalog |
| `delay_seconds` | Per-host minimum delay between requests |
| `timeout_seconds` | Per-request `curl --max-time` |
| `user_agent` | Reserved for future direct-HTTP support |
| `retries` | Extra attempts after first failure |
| `retry_delay_seconds` | Base delay for exponential backoff |
| `db_path` | SQLite file (relative to project root) |
| `log_path` | JSON-lines request log |
| `max_workers` | Default worker threads |
| `stale_after_days` | Age before page/mirror caches are considered stale |
| `append_version_suffix` | `true` = one entry per version with a suffix |

Validation runs on load; unknown keys raise an error.

`stale_after_days` affects **two** caches:

- Catalog page skip list (used by `crawl --all` and `--resume`)
- Mirror check freshness (used by `check-mirrors --skip-checked`)

Raise it if you want longer memory, lower it if you want more frequent
refreshes.

---

## Environment variables

Any config key can be overridden with `OGD_<KEY_UPPER>`:

```powershell
$env:OGD_DB_PATH = "D:\scratch\oldgames.db"
$env:OGD_MAX_WORKERS = "10"
$env:OGD_STALE_AFTER_DAYS = "7"
python scraper.py crawl --all
```

Useful for CI or running against a scratch DB.

---

## Database schema

### `games`

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER | Primary key |
| `title` | TEXT | Game title (HTML entities decoded) |
| `game_url` | TEXT | Unique canonical game URL |
| `year` | INTEGER | Release year |
| `description` | TEXT | Short description |
| `status` | TEXT | `review` / `approved` / `blocked` |
| `created_at`, `updated_at` | TEXT | UTC ISO-8601 |

### `windows_files`

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER | Primary key |
| `game_id` | INTEGER | FK → `games.id` |
| `file_url` | TEXT | Unique OldGamesDownload `/file/.../` URL |
| `file_name` | TEXT | Archive filename |
| `file_size` | TEXT | Human size string |
| `year` | INTEGER | Copied from parent game |
| `mediafire_available` | INTEGER | `0`/`1` |
| `mediafire_url` | TEXT | Captured MediaFire file page |
| `mega_available` | INTEGER | `0`/`1` |
| `mirror_checked_at` | TEXT | Last check timestamp |
| `created_at`, `updated_at` | TEXT | UTC ISO-8601 |

### `approved_uris`

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER | Primary key |
| `game_id` | INTEGER | FK → `games.id` |
| `uri` | TEXT | Manual URI |

### `catalog_pages`

Page crawl cache used by `crawl --all` / `--resume`.

| Column | Type | Notes |
|--------|------|-------|
| `page_number` | INTEGER | Primary key |
| `page_url` | TEXT | Full URL that was fetched |
| `games_found` | INTEGER | Games discovered on that page |
| `status` | TEXT | `ok` / `partial` / `error` |
| `last_crawled_at` | TEXT | UTC ISO-8601 timestamp |

Indexes: `status`, `game_id`, `mirror_checked_at`, `file_name COLLATE NOCASE`,
`catalog_pages.last_crawled_at`.

WAL mode and `synchronous = NORMAL` are enabled automatically.

---

## Hydra JSON format

```json
{
  "name": "oldgamesdownload",
  "downloads": [
    {
      "title": "Need For Speed: Most Wanted",
      "uris": ["https://www.mediafire.com/file/.../file"],
      "uploadDate": "2005-01-01T00:00:00Z",
      "fileSize": "1.85 GB"
    }
  ]
}
```

---

## Multi-version games

By default, all files for a game share the same plain `title`, so Hydra
shows **one** game with multiple download options.

Set `append_version_suffix: true` in `config.json` to emit one entry per
version with a `(version label)` suffix instead.

---

## Web UI

An optional FastAPI app is included:

```powershell
python scraper.py serve --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000> in a browser. Features:

- Live search over titles and filenames
- Status filter
- Sortable columns
- Approve / review / block buttons
- Running stats panel

The UI is read + status-only. Crawling still happens from the CLI.

---

## Logs

Every HTTP request is appended to `oldgames.log.jsonl`:

```json
{"ts":"2026-01-01T12:00:00+00:00","url":"...","status":"ok","ms":412,"attempt":1}
{"ts":"2026-01-01T12:00:01+00:00","url":"...","status":"error","code":28,"category":"timeout","error":"..."}
```

Useful for offline analysis of failure rates and slow URLs.

---

## Crawl resilience

- Parallel workers (default 6) with a shared per-host rate limiter.
- Exponential backoff on retries (handles transient 429/503-style issues).
- Failed pages are logged and skipped, never aborting the whole run.
- **Resumable crawls**: pages crawled successfully are cached in the
  `catalog_pages` table and skipped on subsequent `crawl --all` runs,
  so interrupting and re-running picks up where you left off.
- `--all` mode auto-detects the last page and extends the target if the
  site reveals more pages mid-crawl.
- `--all` mode also stops early after consecutive empty pages or failed
  fetches, and enforces a 2000-page hard cap.
- `--skip-checked` on `check-mirrors` allows fast incremental refreshes.
- WAL SQLite + batched writes keep concurrent writes lock-free in practice.

---

## Project layout

```text
OldGamesDownload-Windows-Crawler/
├── scraper.py               # entry shim
├── config.json
├── requirements.txt
├── README.md
├── oldgames/
│   ├── __init__.py
│   ├── cli.py               # argparse + dispatch
│   ├── config.py            # typed Config + env overrides
│   ├── database.py          # SQLite + WAL + batched writes + page cache
│   ├── http_client.py       # curl wrapper + rate limiter
│   ├── parser.py            # HTML → dataclasses
│   ├── crawler.py           # parallel crawl + resume + rich progress
│   ├── mirror_checker.py    # check + watch
│   ├── exporter.py          # Hydra JSON
│   ├── display.py           # rich tables + confirm
│   ├── review.py            # interactive review
│   ├── server.py            # FastAPI web UI
│   └── logging_setup.py     # JSON-lines logger
├── tests/
│   ├── test_parser.py
│   └── test_export.py
├── oldgames.db              # (created at runtime)
├── oldgames.log.jsonl       # (created at runtime)
├── debug_html/              # (created at runtime, last-seen HTML)
└── oldgames.json            # (created by export)
```

### Running the smoke tests

```powershell
python test_parser.py
python test_export.py
```

Both print `PASS` on success and exit non-zero on failure.
