from pathlib import Path

from launch_desk.brand import NEUTRAL, PRESET_ACCENTS, _cut_tagline, fetch_brand, parse_brand

FIX = Path(__file__).parent / "fixtures"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20


def _fix(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_pawr_fixture_gives_name_tagline_bg():
    kit, _logo = parse_brand("https://pawr.link", _fix("pawr.html"), [_fix("pawr.css")])
    assert kit.name == "pawr.link"
    assert kit.tagline.startswith("one pawrful page for everything")
    assert len(kit.tagline) <= 120
    assert kit.bg == "#FAFAFA"
    assert kit.font_family == "Inter"


def test_accent_skips_greys_and_near_white():
    css = (
        ".a{color:#888888}.b{color:#fafafa}.c{color:#222222}.d{color:#fefefe}"
        ".e{color:#fafafa}.f{color:#fafafa}.g{background:#e8590c}"
    )
    kit, _ = parse_brand("https://x.test", "<html></html>", [css])
    assert kit.accent == "#E8590C"


def test_inline_styles_count_for_accent():
    html = '<div style="color:#2f9e44"></div><p style="color:#2f9e44"></p>'
    kit, _ = parse_brand("https://x.test", html, [".a{color:#e8590c}"])
    assert kit.accent == "#2F9E44"


def test_spa_page_without_css_falls_back_to_neutral():
    kit, logo = parse_brand("https://app.test", _fix("spa.html"), [])
    assert kit.name == "App"
    assert kit.accent == NEUTRAL.accent
    assert kit.bg == NEUTRAL.bg
    assert kit.ink == NEUTRAL.ink
    assert kit.font_family is None
    assert logo is None


def test_name_from_title_split():
    kit, _ = parse_brand("https://x.test", "<title>Acme | Rockets for all</title>", [])
    assert kit.name == "Acme"


def test_tagline_cut_at_word_within_120():
    desc = " ".join(["word"] * 60)
    html = f'<meta name="description" content="{desc}">'
    kit, _ = parse_brand("https://x.test", html, [])
    assert len(kit.tagline) <= 120
    assert kit.tagline.endswith("word…")  # cut at a word, marked as cut


def test_font_family_skips_generics():
    css = '.a{font-family:system-ui,"Space Grotesk",sans-serif}'
    kit, _ = parse_brand("https://x.test", "", [css])
    assert kit.font_family == "Space Grotesk"


def test_logo_url_is_absolute():
    html = (
        '<link rel="icon" href="/favicon.png">'
        '<link rel="apple-touch-icon" href="/apple-icon.png">'
    )
    _, logo = parse_brand("https://pawr.link/some/page", html, [])
    assert logo == "https://pawr.link/apple-icon.png"
    _, logo = parse_brand("https://pawr.link", '<link rel="icon" href="img/f.png">', [])
    assert logo == "https://pawr.link/img/f.png"


def test_presets_are_five_hex_colours():
    assert len(PRESET_ACCENTS) == 5
    assert all(len(c) == 7 and c.startswith("#") for c in PRESET_ACCENTS)


def test_fetch_errors_return_neutral_kit(tmp_path):
    def get(url: str) -> bytes:
        raise TimeoutError("slow")

    kit = fetch_brand("https://slow.test", get, tmp_path)
    assert kit.accent == NEUTRAL.accent
    assert kit.bg == NEUTRAL.bg
    assert kit.logo_path is None
    assert kit.url == "https://slow.test"


def test_fetch_downloads_logo_and_respects_budget(tmp_path):
    calls: list[str] = []
    html = (
        '<link rel="stylesheet" href="/a.css"><link rel="stylesheet" href="/b.css">'
        '<link rel="stylesheet" href="/c.css">'
        '<link rel="stylesheet" href="https://cdn.other.test/d.css">'
        '<link rel="apple-touch-icon" href="/logo.png">'
    )

    def get(url: str) -> bytes:
        calls.append(url)
        if url.endswith("/logo.png"):
            return PNG
        if url.endswith(".css"):
            return b".a{color:#e8590c}"
        return html.encode()

    kit = fetch_brand("https://x.test", get, tmp_path)
    assert len(calls) == 4
    assert all(c.startswith("https://x.test") for c in calls)
    assert kit.accent == "#E8590C"
    assert kit.logo_path is not None
    p = Path(kit.logo_path)
    assert p.parent == tmp_path and p.read_bytes() == PNG


def test_logo_rejected_when_not_an_image(tmp_path):
    def get(url: str) -> bytes:
        if url.endswith("/i.svg"):
            return b"<svg></svg>"
        return b'<link rel="icon" href="/i.svg">'

    kit = fetch_brand("https://x.test", get, tmp_path)
    assert kit.logo_path is None


def test_logo_failure_keeps_rest_of_kit(tmp_path):
    def get(url: str) -> bytes:
        if url.endswith("/i.png"):
            raise OSError("404")
        return b'<meta property="og:site_name" content="Acme"><link rel="icon" href="/i.png">'

    kit = fetch_brand("https://x.test", get, tmp_path)
    assert kit.name == "Acme"
    assert kit.logo_path is None


# --- http_get, against a local server ---------------------------------------

import http.server  # noqa: E402
import threading  # noqa: E402

import pytest  # noqa: E402

from launch_desk.brand import USER_AGENT, http_get  # noqa: E402


class _Handler(http.server.BaseHTTPRequestHandler):
    seen_agents: list[str] = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        try:
            self._route()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _route(self):
        path = self.path
        if path == "/ok":
            type(self).seen_agents.append(self.headers.get("User-Agent", ""))
            body = b"hello"
        elif path == "/big":
            body = b"x" * (2 * 1024 * 1024 + 10)
        elif path == "/ftp":
            self.send_response(302)
            self.send_header("Location", "ftp://example.com/x")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        elif path.startswith("/hop/"):
            n = int(path.split("/")[2])
            self.send_response(302)
            self.send_header("Location", f"/hop/{n - 1}" if n > 0 else "/ok")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()
    t.join()


def test_http_get_returns_bytes_with_user_agent(server):
    _Handler.seen_agents.clear()
    assert http_get(server + "/ok") == b"hello"
    assert _Handler.seen_agents == [USER_AGENT]


def test_http_get_oversize_raises(server):
    with pytest.raises(ValueError):
        http_get(server + "/big")


def test_http_get_404_raises(server):
    with pytest.raises(OSError):
        http_get(server + "/missing")


def test_http_get_redirect_to_ftp_raises(server):
    with pytest.raises(ValueError):
        http_get(server + "/ftp")


def test_http_get_allows_three_redirects_not_four(server):
    assert http_get(server + "/hop/2") == b"hello"  # 3 hops in total
    with pytest.raises(OSError):
        http_get(server + "/hop/3")  # 4 hops


@pytest.mark.parametrize("bad", ["ftp://x", "file:///x", "x"])
def test_http_get_refuses_non_http(bad):
    with pytest.raises(ValueError):
        http_get(bad)


@pytest.mark.parametrize("text,expected", [
    ("one pawrful page for everything you are, do, and care about. 50+ rich link types, "
     "drag-and-drop bento grid, free for 7 days. More text here.",
     "one pawrful page for everything you are, do, and care about."),
    ("Social media post design inspiration: 4,300+ real posts from 1,060+ brands, including launch, "
     "hiring, event and product posts for every kind of team",
     "Social media post design inspiration: 4,300+ real posts from 1,060+ brands"),
])
def test_long_tagline_is_cut_at_a_sentence_or_clause(text, expected):
    assert _cut_tagline(text) == expected


def test_long_tagline_without_a_break_ends_with_ellipsis():
    out = _cut_tagline("word " * 60)
    assert len(out) <= 121 and out.endswith("…")
