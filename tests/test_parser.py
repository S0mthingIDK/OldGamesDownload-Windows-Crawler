import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from oldgames.parser import parse_catalog_page, parse_file_page, parse_game_page


def test_catalog():
    html = '''
    <a class="game-status-card" href="/game/test-game-abc/">Test Game</a>
    <a class="game-status-card" href="/platform/windows/page/2/">2</a>
    <a class="game-status-card" href="/privacy-policy/">Privacy</a>
    '''
    games, pages = parse_catalog_page(html, "https://oldgamesdownload.com/platform/windows/")
    assert len(games) == 1
    assert games[0].title == "Test Game"
    assert games[0].url.endswith("/game/test-game-abc/")
    assert pages == ["https://oldgamesdownload.com/platform/windows/page/2/"]


def test_game():
    html = '''
    <html><head><script type="application/ld+json">
    {"@type":"VideoGame","name":"Test Game","datePublished":"2004-01-01"}
    </script></head><body>
    <h1>Test Game</h1>
    <section class="game-platform" data-platform="windows">
      <span class="game-platform-year">(2004)</span>
      <div class="game-filegroup game-filegroup-main">
        <ul class="game-filelist">
          <li class="game-file game-file-file">
            <a class="game-file-link" href="/file/abc/"><span class="game-file-name">Test_Setup.zip</span></a>
            <span class="game-file-size">1.2 GB</span>
          </li>
        </ul>
      </div>
      <div class="game-filegroup game-filegroup-documents">
        <li class="game-file game-file-manual"><a class="game-file-link" href="/file/manual/"><span class="game-file-name">Manual.pdf</span></a></li>
      </div>
    </section>
    <section class="game-platform" data-platform="playstation-2">
      <div class="game-filegroup game-filegroup-main"><li class="game-file game-file-file"><a class="game-file-link" href="/file/ps2/"><span class="game-file-name">PS2.iso</span></a></li></div>
    </section>
    </body></html>
    '''
    details = parse_game_page(html, "https://oldgamesdownload.com/game/test-game-abc/")
    assert details.title == "Test Game"
    assert details.year == 2004
    assert len(details.windows_files) == 1
    assert details.windows_files[0].file_name == "Test_Setup.zip"


def test_file_page_with_mediafire_url():
    html = '''
    <ul class="file-mirrors">
      <li class="file-mirror file-mirror-mediafire"><a href="https://www.mediafire.com/file/abc/Test.zip/file">MediaFire</a></li>
      <li class="file-mirror file-mirror-mega"><a href="https://example.invalid/mega">MEGA</a></li>
    </ul>
    '''
    mirrors = parse_file_page(html)
    assert mirrors.mediafire_available is True
    assert mirrors.mediafire_url == "https://www.mediafire.com/file/abc/Test.zip/file"
    assert mirrors.mega_available is True


def test_file_page_without_mediafire():
    html = '''
    <ul class="file-mirrors">
      <li class="file-mirror file-mirror-mega"><a href="https://example.invalid/mega">MEGA</a></li>
    </ul>
    '''
    mirrors = parse_file_page(html)
    assert mirrors.mediafire_available is False
    assert mirrors.mediafire_url is None
    assert mirrors.mega_available is True


if __name__ == "__main__":
    test_catalog()
    test_game()
    test_file_page_with_mediafire_url()
    test_file_page_without_mediafire()
    print("parser smoke tests: PASS")