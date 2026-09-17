from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import unquote

from .database import list_all_games_for_export, list_files_for_game

_ARCHIVE_EXT_RE = re.compile(r"\.(zip|rar|7z|iso|bin|cue)$", flags=re.IGNORECASE)


def _version_label(file_name: str) -> str | None:
    stem = _ARCHIVE_EXT_RE.sub("", file_name)
    m = re.search(r"_Win_(.+)$", stem, flags=re.IGNORECASE)
    if not m:
        return None
    label = m.group(1).replace("_", " ")
    label = unquote(unquote(label))
    label = re.sub(r"\s+", " ", label).strip()
    return label or None


def _display_title(
    game_title: str, file_name: str, has_multiple: bool, append_suffix: bool
) -> str:
    if not has_multiple or not append_suffix:
        return game_title
    label = _version_label(file_name)
    if label:
        return f"{game_title} ({label})"
    return f"{game_title} ({_ARCHIVE_EXT_RE.sub('', file_name)})"


def _iter_records(append_suffix: bool):
    for game in list_all_games_for_export():
        files = list_files_for_game(game["id"])
        if not files:
            continue
        has_multiple = len(files) > 1
        for f in files:
            uri = f["mediafire_url"] or f["file_url"]
            title = _display_title(
                game["title"], f["file_name"], has_multiple, append_suffix
            )
            yield {
                "title": title,
                "uris": [uri],
                "uploadDate": (
                    f"{f['year']:04d}-01-01T00:00:00Z" if f["year"] else None
                ),
                "fileSize": f["file_size"],
            }


def export_hydra_json(
    output_path: str, append_suffix: bool = False, stream: bool = False
) -> int:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    count = 0

    if stream:
        with target.open("w", encoding="utf-8") as fh:
            fh.write('{\n  "name": "oldgamesdownload",\n  "downloads": [\n')
            first = True
            for rec in _iter_records(append_suffix):
                if not first:
                    fh.write(",\n")
                fh.write("    " + json.dumps(rec, ensure_ascii=False))
                first = False
                count += 1
            fh.write("\n  ]\n}\n")
    else:
        downloads = list(_iter_records(append_suffix))
        count = len(downloads)
        payload = {"name": "oldgamesdownload", "downloads": downloads}
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    return count