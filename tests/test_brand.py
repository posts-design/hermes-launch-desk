from pathlib import Path

from launch_desk.brand import NEUTRAL, PRESET_ACCENTS, _cut_tagline, fetch_brand, parse_brand

FIX = Path(__file__).parent / "fixtures"
def _png(size=(16, 16), mode="RGB") -> bytes:
    import io as _io
    from PIL import Image as _Image
    out = _io.BytesIO()
    _Image.new(mode, size).save(out, "PNG")
    return out.getvalue()


PNG = _png()


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
        elif path in ("/to-metadata", "/to-private"):
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/" if path == "/to-metadata"
                             else "http://10.0.0.7/admin")
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


def test_posts_design_reads_dark_and_falls_back_to_its_most_used_colour():
    # posts.design names no saturated accent token and its buttons are grey, so the accent is
    # the frequency fallback: #FF9602, a stop in a decorative gradient (owner to confirm).
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


# --- a bounded, forgiving CSS read (Task 20 fixes) ----------------------------

import time  # noqa: E402

PAGE = '<meta property="og:site_name" content="Acme"><title>Acme</title>'


def _timed(html: str, css: list[str]):
    start = time.perf_counter()
    kit, _ = parse_brand("https://x.test", html, css)
    return kit, time.perf_counter() - start


def test_a_var_chain_resolves_in_linear_time():
    n = 30
    css = ":root{--accent:" + " ".join(["var(--l1)"] * n) + ";"
    css += "".join(f"--l{i}:" + " ".join([f"var(--l{i + 1})"] * n) + ";" for i in range(1, 7))
    css += "--l7:#e8590c}.a{color:#2f9e44}.b{color:#2f9e44}"
    kit, took = _timed(PAGE, [css])
    assert took < 1.0
    assert kit.accent == "#2F9E44"  # the token is too large to be a colour: the next signal decides


def test_a_huge_token_used_by_many_buttons_resolves_once():
    css = ":root{--big:" + "x" * 1_000_000 + "}" + "".join(
        f".btn.b{i}{{background:var(--big)}}" for i in range(10_000)) + ".a{color:#2f9e44}"
    kit, took = _timed(PAGE, [css])
    assert took < 1.0
    assert kit.accent == "#2F9E44"


def test_var_cycles_and_deep_chains_do_not_loop():
    cycle = ":root{--a:var(--b);--b:var(--a);--accent:var(--a)}.x{color:#2f9e44}"
    deep = ":root{--accent:var(--d0);" + "".join(f"--d{i}:var(--d{i + 1});" for i in range(5000)) + \
        "--d5000:#e8590c}.x{color:#2f9e44}.y{color:#2f9e44}"
    for css in (cycle, deep):
        kit, took = _timed(PAGE, [css])
        assert took < 1.0 and kit.accent == "#2F9E44"


def test_a_short_var_chain_still_resolves():
    css = ":root{--orange:#e8590c;--brand-color:var(--tone, #000);--tone:var(--orange)}.a{color:#2f9e44}"
    kit, _ = parse_brand("https://x.test", "", [css])
    assert kit.accent == "#E8590C"


TWO_MB = 2 * 1024 * 1024


@pytest.mark.parametrize("css", [
    "/*a" * (TWO_MB // 3),
    "@m{" * (TWO_MB // 6) + "a{}" * (TWO_MB // 6),
    "x{" * (TWO_MB // 5) + "a{}" * (TWO_MB // 5),
    "a{}" * (TWO_MB // 3),
    "'" + "a" * TWO_MB,
    ("a{content:'{'}" + '"' + "{") * (TWO_MB // 16),
], ids=["comments", "at-rules", "nesting", "rules", "string", "braces-in-strings"])
def test_hostile_css_reads_within_two_seconds(css):
    kit, took = _timed(PAGE, [css])
    assert took < 2.0
    assert kit.name == "Acme"


def test_an_unterminated_comment_drops_only_the_rest():
    css = ":root{--accent:#e8590c}/* never closed :root{--accent:#2f9e44}"
    kit, _ = parse_brand("https://x.test", "", [css])
    assert kit.accent == "#E8590C"


def test_braces_inside_strings_do_not_shift_rules():
    css = '.q::before{content:"}{"}:root{--accent:#e8590c}.a{color:#2f9e44}.b{color:#2f9e44}'
    kit, _ = parse_brand("https://x.test", "", [css])
    assert kit.accent == "#E8590C"


@pytest.mark.parametrize("html,css", [
    ('<meta name="theme-color" content="#ggg8">', ""),
    ('<meta name="theme-color" content="#zzzzzz">', ""),
    ('<meta name="theme-color" content="rgb(1e999, 0, 0)">', ""),
    ("", ":root{--accent:rgb(1e999,0,0)}"),
    ("", ":root{--accent:hsl(1e999 50% 50%)}"),
    ("", ":root{--bg:1.2.3 50% 10%}"),
    ("", ":root{--bg:nan 50% 10%}"),
    ("", ":root{--accent:#ggg8;--primary:#zzzzzz}body{background:rgb(inf,0,0)}"),
    ("", ".btn{background:oklch(1e999 0.2 30)}"),
])
def test_a_malformed_colour_drops_only_that_signal(html, css):
    page = PAGE + html
    sheet = css + ".a{color:#2f9e44}.b{color:#2f9e44}"
    kit, _ = parse_brand("https://x.test", page, [sheet])
    assert kit.name == "Acme"
    assert kit.accent == "#2F9E44"
    assert kit.bg == NEUTRAL.bg


def test_a_malformed_colour_keeps_the_fetched_brand(tmp_path):
    def get(url: str) -> bytes:
        return (PAGE + '<meta name="theme-color" content="#ggg8"><meta name="description" content="Rockets.">'
                '<style>:root{--accent:rgb(1e999,0,0)}.a{color:#e8590c}</style>').encode()

    kit = fetch_brand("https://x.test", get, tmp_path)
    assert kit.name == "Acme" and kit.tagline == "Rockets." and kit.accent == "#E8590C"


def test_color_scheme_on_a_component_does_not_make_the_site_dark():
    css = ".login-dialog{color-scheme:dark}"
    kit, _ = parse_brand("https://x.test", DARK_TC, [css])
    assert not _dark(kit)


@pytest.mark.parametrize("css,dark", [
    (":root{color-scheme:light dark}", False),
    ("html{color-scheme:only dark}", True),
    (":root:not([data-theme]){color-scheme:dark}", True),
    ("body{color-scheme:dark}", True),
])
def test_only_a_dark_only_root_scheme_counts(css, dark):
    kit, _ = parse_brand("https://x.test", DARK_TC, [css])
    assert _dark(kit) is dark


def test_a_light_dark_scheme_meta_is_not_dark_only():
    kit, _ = parse_brand("https://x.test", DARK_TC + '<meta name="color-scheme" content="light dark">', [])
    assert not _dark(kit)


@pytest.mark.parametrize("wrapper", ["@supports (color: darkred){%s}", "@layer darkroom{%s}",
                                     "@media (min-width: 640px){%s}"])
def test_at_rules_that_only_mention_dark_still_count(wrapper):
    css = wrapper % ":root{--accent:#e8590c}" + ".a{color:#2f9e44}.b{color:#2f9e44}"
    kit, _ = parse_brand("https://x.test", "", [css])
    assert kit.accent == "#E8590C"


def test_a_dark_media_block_is_skipped_however_it_is_spaced():
    css = "@media screen and (prefers-color-scheme : dark){:root{--accent:#e8590c}}.a{color:#2f9e44}.b{color:#2f9e44}"
    kit, _ = parse_brand("https://x.test", "", [css])
    assert kit.accent == "#2F9E44"


@pytest.mark.parametrize("value,expected", [
    ("oklch(70.5% 0.213 47.604)", "#FF6900"),
    ("oklch(0.627 0.194 149.214)", "#00A63E"),
    ("oklab(0.452 -0.0325 -0.3115)", "#0000FF"),
])
def test_oklch_and_oklab_tokens_are_read(value, expected):
    css = f":root{{--primary:{value}}}.a{{color:#2f9e44}}"
    kit, _ = parse_brand("https://x.test", "", [css])
    r, g, b = (int(kit.accent[i:i + 2], 16) for i in (1, 3, 5))
    er, eg, eb = (int(expected[i:i + 2], 16) for i in (1, 3, 5))
    assert max(abs(r - er), abs(g - eg), abs(b - eb)) <= 4


@pytest.mark.parametrize("css", [
    "\\'\\{" * (TWO_MB // 4),
    ("\\" * 1000 + "'") * (TWO_MB // 1001),
], ids=["escapes", "backslashes"])
def test_escaped_characters_read_within_two_seconds(css):
    kit, took = _timed(PAGE, [css])
    assert took < 2.0
    assert kit.name == "Acme"


def test_escaped_quotes_in_a_selector_do_not_open_a_string():
    css = (".content-\\[\\'\\'\\]:before{--tw-content:''}"
           ":root{--accent:#e8590c}.a{color:#2f9e44}.b{color:#2f9e44}")
    kit, _ = parse_brand("https://x.test", "", [css])
    assert kit.accent == "#E8590C"


# --- one colour-scan budget per read (Task 20 fix round 2) --------------------

_COLOURS_60 = "#abc #abd #abe #abf #ab0 #ab1 #ab2 #ab3 #ab4 #ab5 #ab6 #ab7"  # 59 characters


@pytest.mark.parametrize("rule", ["button{background:var(--t)}", "html{background:var(--t)}"])
def test_many_rules_sharing_a_large_token_read_within_two_seconds(rule):
    token = ":root{--u:" + _COLOURS_60 + ";--t:" + " ".join(["var(--u)"] * 32) + "}"
    kit, took = _timed(PAGE, [token + rule * 20_000])
    assert took < 2.0
    assert kit.name == "Acme"


def test_many_distinct_large_tokens_read_within_two_seconds():
    css = ":root{--u:" + _COLOURS_60 + ";" + "".join(
        f"--t{i}:" + " ".join(["var(--u)"] * 32) + ";" for i in range(2_000)) + "}"
    css += "".join(f"button.b{i}{{background:var(--t{i % 2_000}) #{i % 4096:03x}}}" for i in range(20_000))
    kit, took = _timed(PAGE, [css])
    assert took < 2.0
    assert kit.name == "Acme"


def test_many_accent_named_tokens_read_within_two_seconds():
    css = ":root{" + "".join(f"--accent{'-color' * (i % 50)}:{_COLOURS_60};" for i in range(3_600)) + "}"
    kit, took = _timed(PAGE, [css])
    assert took < 2.0
    assert kit.name == "Acme"


def test_the_scan_budget_keeps_ordinary_reads_whole():
    for name, html in (("brookhour.html", _fix("brookhour.html")), ("fernlight.html", _fix("fernlight.html"))):
        kit, _ = parse_brand("http://127.0.0.1:8731/", html, [])
        assert kit.accent in ("#FF5B2E", "#17A34A"), name
    assert _posts_design().bg == "#101010"


# --- R59: no private hosts but the founder's own; I3: no pixel bombs ----------

@pytest.mark.parametrize("path", ["/to-metadata", "/to-private"])
def test_a_redirect_to_a_private_host_is_refused(server, path):
    with pytest.raises(ValueError):  # refused before any connection is tried
        http_get(server + path)


def test_a_redirect_on_the_same_local_host_is_followed(server):
    assert http_get(server + "/hop/1") == b"hello"  # a staging site on 127.0.0.1 still reads


@pytest.mark.parametrize("logo", ["http://169.254.169.254/icon.png", "http://10.1.2.3/icon.png",
                                  "http://localhost:9000/icon.png"])
def test_a_public_site_cannot_point_its_logo_at_a_private_host(tmp_path, logo):
    calls = []

    def get(url):
        calls.append(url)
        if url == logo:
            return PNG
        return f'<link rel="icon" href="{logo}">'.encode()

    kit = fetch_brand("https://x.test", get, tmp_path)
    assert kit.logo_path is None and logo not in calls


def test_a_local_staging_site_keeps_its_own_logo(tmp_path):
    def get(url):
        if url.endswith("/icon.png"):
            return PNG
        return b'<link rel="icon" href="/icon.png">'

    kit = fetch_brand("http://127.0.0.1:8731/k3/", get, tmp_path)
    assert kit.logo_path is not None


def test_a_pixel_bomb_logo_is_not_kept_and_the_card_still_renders(tmp_path):
    from launch_desk.render import _open_image, render_card
    bomb = _png((9000, 9000), mode="1")

    def get(url):
        if url.endswith("/icon.png"):
            return bomb
        return b'<link rel="icon" href="/icon.png">'

    kit = fetch_brand("https://x.test", get, tmp_path)
    assert kit.logo_path is None
    path = tmp_path / "bomb.png"
    path.write_bytes(bomb)
    assert _open_image(path) is None  # above ~40 M pixels: never decoded
    from dataclasses import replace
    out = render_card(replace(kit, logo_path=str(path)), [path], "Pet pages take themes now.", (1200, 675),
                      tmp_path / "card.png")
    from PIL import Image
    assert Image.open(out).size == (1200, 675)


# --- R61: names and numeric forms that reach a private address; N1: ICO pixel bombs ----------

def _resolves(mapping):
    import socket

    def fake(host, *args, **kwargs):
        if host not in mapping:
            raise socket.gaierror("no such host")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in mapping[host]]
    return fake


@pytest.mark.parametrize("logo,mapping", [
    ("http://169.254.169.254.nip.io/icon.png", {"169.254.169.254.nip.io": ["169.254.169.254"]}),
    ("http://localtest.me/icon.png", {"localtest.me": ["127.0.0.1"]}),
    ("http://mixed.example/icon.png", {"mixed.example": ["93.184.216.34", "10.0.0.5"]}),
    ("http://0x7f000001/icon.png", {}),
    ("http://0xa9.0xfe.0xa9.0xfe/icon.png", {}),
])
def test_a_logo_whose_host_reaches_a_private_address_is_refused(tmp_path, monkeypatch, logo, mapping):
    from launch_desk import brand
    monkeypatch.setattr(brand.socket, "getaddrinfo", _resolves(mapping))
    calls = []

    def get(url):
        calls.append(url)
        return PNG if url == logo else f'<link rel="icon" href="{logo}">'.encode()

    assert fetch_brand("https://x.test", get, tmp_path).logo_path is None and logo not in calls


def test_a_logo_on_a_public_host_is_still_fetched(tmp_path, monkeypatch):
    from launch_desk import brand
    monkeypatch.setattr(brand.socket, "getaddrinfo", _resolves({"cdn.example": ["93.184.216.34"]}))

    def get(url):
        return PNG if url.endswith("/icon.png") else b'<link rel="icon" href="https://cdn.example/icon.png">'

    assert fetch_brand("https://x.test", get, tmp_path).logo_path is not None


def _ico_around(png: bytes, side: int = 0) -> bytes:
    import struct
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(png), 6 + 16)  # 0 means 256 or more
    return header + entry + png


def test_an_ico_wrapping_a_huge_png_is_refused_without_decoding(tmp_path, monkeypatch):
    from launch_desk import brand
    from launch_desk.render import _open_image
    bomb = _ico_around(_png((9000, 9000), mode="1"))
    opened = []
    real_open = brand.Image.open
    monkeypatch.setattr(brand.Image, "open", lambda *a, **k: opened.append(a) or real_open(*a, **k))

    def get(url):
        return bomb if url.endswith("/favicon.ico") else b'<link rel="icon" href="/favicon.ico">'

    assert fetch_brand("https://x.test", get, tmp_path).logo_path is None
    assert opened == []  # refused from the ICO directory, before Pillow decodes the frame
    path = tmp_path / "bomb.ico"
    path.write_bytes(bomb)
    assert _open_image(path) is None


def test_a_small_ico_logo_is_kept(tmp_path):
    small = _ico_around(_png((32, 32)), side=32)

    def get(url):
        return small if url.endswith("/favicon.ico") else b'<link rel="icon" href="/favicon.ico">'

    assert fetch_brand("https://x.test", get, tmp_path).logo_path is not None
