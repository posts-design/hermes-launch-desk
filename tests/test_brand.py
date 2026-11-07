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
    assert len(out) <= 120 and out.endswith("…")


def test_accent_skips_social_platform_colours():
    css = (".tweet{color:#1D9BF0}.a{color:#1d9bf0}.b{border-color:#1D9BF0}.li{color:#0A66C2}"
           ".yt{color:#ff0000}.brand{color:#E8590C}")
    kit, _ = parse_brand("https://x.test", "", [css])
    assert kit.accent == "#E8590C"
    only_social, _ = parse_brand("https://x.test", "", [".t{color:#1DA1F2}.f{color:#1877F2}"])
    assert only_social.accent == NEUTRAL.accent


# --- dark brands, exact accents and the headline face (Task 20) -------------

import gzip  # noqa: E402

from launch_desk.brand import font_face  # noqa: E402


def _gz(name: str) -> str:
    return gzip.decompress((FIX / name).read_bytes()).decode("utf-8")


def _posts_design():
    return parse_brand("https://posts.design/mcp", _gz("posts-mcp.html.gz"),
                       [_gz("posts-mcp-1.css.gz"), _gz("posts-mcp-2.css.gz")])[0]


def _lum(hex_: str) -> float:
    r, g, b = (int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _dark(kit) -> bool:
    return _lum(kit.bg) < 0.4 and _lum(kit.ink) > 0.6


def test_posts_design_reads_dark_with_its_orange_accent():
    kit = _posts_design()
    assert kit.bg == "#101010"
    assert _dark(kit)
    assert kit.accent == "#FF9602"


def test_brookhour_reads_light_orange_and_serif():
    kit, _ = parse_brand("http://127.0.0.1:8731/k2/", _fix("brookhour.html"), [])
    assert kit.bg == "#FFF6EC" and not _dark(kit)
    assert kit.accent == "#FF5B2E"
    assert kit.font_face == "serif"


def test_fernlight_reads_light_button_green_and_rounded():
    kit, _ = parse_brand("http://127.0.0.1:8731/k3/", _fix("fernlight.html"), [])
    assert kit.bg == "#F1F6E9" and not _dark(kit)
    assert kit.accent == "#17A34A"
    assert kit.font_face == "rounded"


def test_pawr_accent_and_face_unchanged():
    kit, _ = parse_brand("https://pawr.link", _fix("pawr.html"), [_fix("pawr.css")])
    assert kit.accent == "#6A3CFF"
    assert kit.font_face == "sans"
    assert not _dark(kit)


DARK_TC = ('<meta name="theme-color" content="#f5f5f5" media="(prefers-color-scheme: light)">'
           '<meta name="theme-color" content="#0b0b0b" media="(prefers-color-scheme: dark)">')


def test_dark_theme_color_counts_when_css_sets_dark_scheme():
    css = "@media (prefers-color-scheme:dark){:root{color-scheme:dark}}"
    kit, _ = parse_brand("https://x.test", DARK_TC, [css])
    assert kit.bg == "#0B0B0B" and _dark(kit)


def test_dark_theme_color_counts_with_a_color_scheme_meta():
    html = DARK_TC + '<meta name="color-scheme" content="dark">'
    kit, _ = parse_brand("https://x.test", html, [])
    assert kit.bg == "#0B0B0B" and _dark(kit)


def test_dark_theme_color_alone_keeps_the_light_background():
    kit, _ = parse_brand("https://x.test", DARK_TC, [":root{color-scheme:light}"])
    assert kit.bg == "#F5F5F5" and not _dark(kit)


def test_default_theme_color_that_is_dark_makes_a_dark_kit():
    kit, _ = parse_brand("https://x.test", '<meta name="theme-color" content="#111">', [])
    assert kit.bg == "#111111" and _dark(kit)


@pytest.mark.parametrize("css", [
    "body{margin:0;background:#0d0f12}",
    "html{background-color:rgb(12, 14, 20)}",
    ":root{--bg:#0d0f12}body{color:#fff}",
    ":root{--background:#0d0f12}",
    ":root{--page:#0d0f12}html,body{background:var(--page)}",
])
def test_dark_page_background_makes_a_dark_kit(css):
    kit, _ = parse_brand("https://x.test", '<meta name="theme-color" content="#ffffff">', [css])
    assert _dark(kit)


def test_light_page_background_with_dark_mode_tokens_stays_light():
    css = (":root{--bg:#fafafa}body{background:var(--bg)}"
           "@media (prefers-color-scheme:dark){:root{--bg:#0d0f12}}"
           "[data-theme=dark]{--background:#000}")
    kit, _ = parse_brand("https://x.test", "", [css])
    assert not _dark(kit)


def test_accent_token_beats_the_most_frequent_colour():
    css = (":root{--accent:#17a34a;--accent-deep:#0f7f38}"
           ".a{color:#0f7f38}.b{color:#0f7f38}.c{border-color:#0f7f38}")
    kit, _ = parse_brand("https://x.test", "", [css])
    assert kit.accent == "#17A34A"


@pytest.mark.parametrize("name", ["--brand", "--color-primary", "--primary", "--brand-color"])
def test_accent_token_names(name):
    css = f":root{{{name}:#e8590c}}.a{{color:#2f9e44}}.b{{color:#2f9e44}}"
    kit, _ = parse_brand("https://x.test", "", [css])
    assert kit.accent == "#E8590C"


def test_accent_token_scoped_to_a_theme_or_grey_is_skipped():
    css = ("[data-theme=sunny]{--accent:#ff9b3d}:root{--primary:#171717;--accent:#f3f3f4}"
           ".a{color:#6a3cff}.b{color:#6a3cff}")
    kit, _ = parse_brand("https://x.test", "", [css])
    assert kit.accent == "#6A3CFF"


def test_button_background_beats_the_most_frequent_colour():
    css = (":root{--go:#7048e8}.btn{background:var(--go);color:#fff}"
           ".a{color:#2f9e44}.b{color:#2f9e44}.c{color:#2f9e44}")
    kit, _ = parse_brand("https://x.test", "", [css])
    assert kit.accent == "#7048E8"


def test_button_hover_state_does_not_count():
    css = "button:hover{background:#c2255c}.a{color:#2f9e44}.b{color:#2f9e44}"
    kit, _ = parse_brand("https://x.test", "", [css])
    assert kit.accent == "#2F9E44"


@pytest.mark.parametrize("family,face", [
    ("Iowan Old Style", "serif"), ("Georgia", "serif"), ("Times New Roman", "serif"),
    ("Source Serif 4", "serif"), ("Newsreader", "serif"), ("EB Garamond", "serif"),
    ("Charter", "serif"), ("Palatino Linotype", "serif"),
    ("SF Pro Rounded", "rounded"), ("Nunito", "rounded"), ("Quicksand", "rounded"),
    ("Varela Round", "rounded"), ("ui-rounded", "rounded"),
    ("Inter", "sans"), ("Open Sans", "sans"), ("IBM Plex Sans", "sans"), ("PT Sans Serif", "sans"),
    (None, "sans"), ("", "sans"),
])
def test_font_face_from_family(family, face):
    assert font_face(family) == face


def test_face_follows_the_headings_font():
    css = 'body{font-family:Inter,sans-serif}h1,h2{font-family:"Iowan Old Style",Georgia,serif}'
    kit, _ = parse_brand("https://x.test", "", [css])
    assert kit.font_face == "serif"
