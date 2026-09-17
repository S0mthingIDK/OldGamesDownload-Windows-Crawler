import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from oldgames import database
from oldgames.database import (
    init_db, set_db_path, upsert_game_with_files_by_url, update_mirror_status,
)
from oldgames.exporter import export_hydra_json
from oldgames.parser import WindowsFile


def _reset(tmp_path: Path):
    set_db_path(tmp_path / "test.db")
    init_db()


def test_export_uses_mediafire_url_when_available(tmp_path):
    _reset(tmp_path)
    mf_url = "https://www.mediafire.com/file/abc/Test.zip/file"
    file_url = "https://oldgamesdownload.com/file/test/"
    files = [WindowsFile(file_url, "Test.zip", "1.2 GB", 2004)]
    upsert_game_with_files_by_url(
        "Test Game", "https://oldgamesdownload.com/game/test-game/",
        2004, None, files,
    )
    update_mirror_status(file_url, True, mf_url, False)

    output = tmp_path / "oldgames.json"
    count = export_hydra_json(str(output))
    data = json.loads(output.read_text(encoding="utf-8"))

    assert count == 1
    assert data["name"] == "oldgamesdownload"
    rec = data["downloads"][0]
    assert rec["title"] == "Test Game"
    assert rec["uris"] == [mf_url]
    assert rec["fileSize"] == "1.2 GB"
    assert set(rec) == {"title", "uris", "uploadDate", "fileSize"}


def test_export_falls_back_to_file_page_url(tmp_path):
    _reset(tmp_path)
    file_url = "https://oldgamesdownload.com/file/fallback/"
    files = [WindowsFile(file_url, "Fallback.zip", "500 MB", 1999)]
    upsert_game_with_files_by_url(
        "Fallback Game", "https://oldgamesdownload.com/game/fallback/",
        1999, None, files,
    )
    update_mirror_status(file_url, False, None, False)

    output = tmp_path / "oldgames.json"
    export_hydra_json(str(output))
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["downloads"][0]["uris"] == [file_url]


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        test_export_uses_mediafire_url_when_available(p)
        test_export_falls_back_to_file_page_url(p)
    print("export smoke tests: PASS")