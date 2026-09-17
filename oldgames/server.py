from __future__ import annotations

from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from .database import (
    coverage_report, get_game, list_files_for_game, list_games, set_status,
)


class StatusPayload(BaseModel):
    status: str


def _serialize_game(game, files) -> dict:
    return {
        "id": game["id"],
        "title": game["title"],
        "game_url": game["game_url"],
        "year": game["year"],
        "status": game["status"],
        "files": [
            {
                "file_name": f["file_name"],
                "file_size": f["file_size"],
                "mediafire_url": f["mediafire_url"],
                "mediafire_available": bool(f["mediafire_available"]),
                "mega_available": bool(f["mega_available"]),
                "mirror_checked_at": f["mirror_checked_at"],
            }
            for f in files
        ],
    }


def create_app(stale_after_days: int = 30) -> FastAPI:
    app = FastAPI(title="oldgames crawler", version="2.0")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return INDEX_HTML

    @app.get("/api/stats")
    def api_stats():
        return JSONResponse(coverage_report(stale_after_days))

    @app.get("/api/games")
    def api_games(status: str | None = None):
        games = list_games(status)
        return JSONResponse([
            _serialize_game(g, list_files_for_game(g["id"])) for g in games
        ])

    @app.get("/api/games/{game_id}")
    def api_game(game_id: int):
        g = get_game(game_id)
        if not g:
            raise HTTPException(404, "not found")
        return JSONResponse(_serialize_game(g, list_files_for_game(game_id)))

    @app.post("/api/games/{game_id}/status")
    def api_set_status(game_id: int, payload: StatusPayload):
        g = get_game(game_id)
        if not g:
            raise HTTPException(404, "not found")
        if payload.status not in {"review", "approved", "blocked"}:
            raise HTTPException(400, "invalid status")
        set_status(game_id, payload.status)
        return {"ok": True, "id": game_id, "status": payload.status}

    return app


def run(host: str, port: int, stale_after_days: int, db_path: Path) -> None:
    app = create_app(stale_after_days)
    uvicorn.run(app, host=host, port=port, log_level="info")


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>oldgames crawler</title>
<style>
  :root { color-scheme: dark; }
  body { font-family: system-ui, sans-serif; margin: 2rem; background: #111; color: #eee; }
  h1 { color: #6cf; }
  table { border-collapse: collapse; width: 100%; margin-top: 1rem; }
  th, td { padding: .4rem .6rem; border-bottom: 1px solid #333; text-align: left; }
  th { background: #1a1a1a; cursor: pointer; }
  tr:hover { background: #1a1a1a; }
  .approved { color: #6f6; } .review { color: #fc6; } .blocked { color: #f66; }
  button { background: #234; color: #cff; border: 1px solid #345; padding: .2rem .6rem;
           border-radius: .25rem; cursor: pointer; margin-right: .25rem; }
  button:hover { background: #345; }
  .stats { display: flex; gap: 2rem; flex-wrap: wrap; margin: 1rem 0; }
  .stats div { background: #1a1a1a; padding: .6rem 1rem; border-radius: .4rem; }
  .stats b { color: #6cf; font-size: 1.2rem; }
  input { padding: .4rem; width: 20rem; background: #1a1a1a; color: #eee;
          border: 1px solid #333; border-radius: .25rem; }
</style>
</head>
<body>
<h1>oldgames crawler</h1>
<div class="stats" id="stats"></div>
<div>
  <input id="search" placeholder="Search title or filename...">
  <select id="status">
    <option value="">All statuses</option>
    <option value="review">review</option>
    <option value="approved">approved</option>
    <option value="blocked">blocked</option>
  </select>
</div>
<table id="games">
  <thead><tr>
    <th data-k="title">Title</th>
    <th data-k="year">Year</th>
    <th data-k="status">Status</th>
    <th data-k="files">Files</th>
    <th>MediaFire</th>
    <th>Actions</th>
  </tr></thead>
  <tbody></tbody>
</table>
<script>
let allGames = [];
let sortKey = 'title', sortDir = 1;

async function loadStats() {
  const r = await fetch('/api/stats'); const s = await r.json();
  document.getElementById('stats').innerHTML = `
    <div>Games<br><b>${s.games}</b></div>
    <div>Files<br><b>${s.files}</b></div>
    <div>MediaFire URLs<br><b>${s.mediafire_urls}</b></div>
    <div>No mirror<br><b>${s.no_mirror}</b></div>
    <div>Stale<br><b>${s.stale_or_unchecked}</b></div>
  `;
}

async function loadGames() {
  const status = document.getElementById('status').value;
  const url = status ? `/api/games?status=${status}` : '/api/games';
  const r = await fetch(url); allGames = await r.json(); render();
}

function render() {
  const q = document.getElementById('search').value.toLowerCase();
  let list = allGames.filter(g =>
    !q || g.title.toLowerCase().includes(q) ||
    g.files.some(f => f.file_name.toLowerCase().includes(q))
  );
  list.sort((a,b) => {
    let A, B;
    if (sortKey === 'files') { A = a.files.length; B = b.files.length; }
    else { A = (a[sortKey] || '').toString().toLowerCase();
           B = (b[sortKey] || '').toString().toLowerCase(); }
    return A < B ? -sortDir : A > B ? sortDir : 0;
  });

  const tb = document.querySelector('#games tbody');
  tb.innerHTML = list.map(g => {
    const mf = g.files.filter(f => f.mediafire_url).length;
    return `<tr>
      <td>${escapeHtml(g.title)}</td>
      <td>${g.year || ''}</td>
      <td class="${g.status}">${g.status}</td>
      <td>${g.files.length}</td>
      <td>${mf}/${g.files.length}</td>
      <td>
        <button onclick="setStatus(${g.id}, 'approved')">approve</button>
        <button onclick="setStatus(${g.id}, 'review')">review</button>
        <button onclick="setStatus(${g.id}, 'blocked')">block</button>
      </td>
    </tr>`;
  }).join('');
}

async function setStatus(id, status) {
  await fetch(`/api/games/${id}/status`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({status})
  });
  const g = allGames.find(x => x.id === id);
  if (g) g.status = status;
  render(); loadStats();
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

document.querySelectorAll('th[data-k]').forEach(th => {
  th.addEventListener('click', () => {
    const k = th.dataset.k;
    if (sortKey === k) sortDir *= -1;
    else { sortKey = k; sortDir = 1; }
    render();
  });
});
document.getElementById('search').addEventListener('input', render);
document.getElementById('status').addEventListener('change', loadGames);

loadStats(); loadGames();
</script>
</body>
</html>
"""