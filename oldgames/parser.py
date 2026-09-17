from __future__ import annotations

import html as html_module
import json
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup


@dataclass(frozen=True)
class CatalogGame:
    title: str
    url: str


@dataclass(frozen=True)
class WindowsFile:
    file_url: str
    file_name: str
    file_size: str | None
    year: int | None


@dataclass(frozen=True)
class GameDetails:
    title: str
    year: int | None
    description: str | None
    windows_files: list[WindowsFile]


@dataclass(frozen=True)
class FilePageMirrors:
    mediafire_available: bool
    mediafire_url: str | None
    mega_available: bool


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = html_module.unescape(value)
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def _jsonld_objects(soup: BeautifulSoup) -> list[dict]:
    objects: list[dict] = []
    for script in soup.select("script[type='application/ld+json']"):
        raw = script.string or script.get_text()
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            objects.append(data)
            if isinstance(data.get("@graph"), list):
                objects.extend(x for x in data["@graph"] if isinstance(x, dict))
        elif isinstance(data, list):
            objects.extend(x for x in data if isinstance(x, dict))
    return objects


def parse_catalog_page(html: str, base_url: str) -> tuple[list[CatalogGame], list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    games: list[CatalogGame] = []
    seen_games: set[str] = set()

    links = soup.select("a.game-status-card[href]")
    if not links:
        links = soup.select("a[href*='/game/']")

    for link in links:
        href = urljoin(base_url, link.get("href", ""))
        parsed = urlparse(href)
        path_parts = [p for p in parsed.path.split("/") if p]
        if len(path_parts) != 2 or path_parts[0] != "game":
            continue
        if href in seen_games:
            continue
        seen_games.add(href)
        title = _clean(link.get_text(" ", strip=True)) or path_parts[1]
        games.append(CatalogGame(title=title, url=href))

    pagination: list[str] = []
    seen_pages: set[str] = set()
    for link in soup.select("a.page-numbers[href], a[href*='/platform/windows/page/']"):
        href = urljoin(base_url, link.get("href", ""))
        path = urlparse(href).path.rstrip("/")
        if not path.startswith("/platform/windows/page/"):
            continue
        if href not in seen_pages:
            seen_pages.add(href)
            pagination.append(href)

    next_link = soup.select_one("a[rel='next'][href]")
    if next_link:
        href = urljoin(base_url, next_link.get("href", ""))
        if href not in seen_pages:
            pagination.append(href)

    return games, pagination


def _find_windows_platform(soup: BeautifulSoup):
    return soup.select_one(".game-platform[data-platform='windows']")


def parse_game_page(html: str, game_url: str) -> GameDetails:
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.select_one("h1")
    title = _clean(h1.get_text(" ", strip=True) if h1 else None)
    if title is None:
        title = urlparse(game_url).path.strip("/").split("/")[-1].replace("-", " ").title()

    year: int | None = None
    description: str | None = None

    for obj in _jsonld_objects(soup):
        if not year and obj.get("datePublished"):
            m = re.search(r"(19|20)\d{2}", str(obj["datePublished"]))
            if m:
                year = int(m.group(0))
        if not description and obj.get("description"):
            description = _clean(str(obj["description"]))
        if obj.get("name") and title:
            title = _clean(str(obj["name"])) or title

    if not description:
        meta = soup.select_one("meta[name='description'][content]")
        if meta:
            description = _clean(meta.get("content"))

    windows = _find_windows_platform(soup)
    if not windows:
        return GameDetails(title=title, year=year, description=description, windows_files=[])

    year_node = windows.select_one(".game-platform-year")
    if year_node:
        m = re.search(r"(19|20)\d{2}", year_node.get_text(" ", strip=True))
        if m:
            year = int(m.group(0))

    files: list[WindowsFile] = []
    for item in windows.select(".game-filegroup-main li.game-file-file"):
        link = item.select_one("a.game-file-link[href]")
        if not link:
            continue
        file_url = urljoin(game_url, link.get("href", ""))
        name_node = item.select_one(".game-file-name")
        file_name = _clean(name_node.get_text(" ", strip=True) if name_node else None)
        if not file_name:
            file_name = file_url.rstrip("/").split("/")[-1]
        size_node = item.select_one(".game-file-size")
        file_size = _clean(size_node.get_text(" ", strip=True) if size_node else None)
        files.append(
            WindowsFile(
                file_url=file_url,
                file_name=file_name,
                file_size=file_size,
                year=year,
            )
        )

    return GameDetails(
        title=title,
        year=year,
        description=description,
        windows_files=files,
    )


def parse_file_page(html: str) -> FilePageMirrors:
    soup = BeautifulSoup(html, "html.parser")

    mediafire_link = soup.select_one(".file-mirror-mediafire a[href]")
    mediafire_available = bool(mediafire_link)
    mediafire_url = _clean(mediafire_link.get("href")) if mediafire_link else None

    mega = bool(soup.select_one(".file-mirror-mega a[href]"))

    return FilePageMirrors(
        mediafire_available=mediafire_available,
        mediafire_url=mediafire_url,
        mega_available=mega,
    )