"""Read a product site once and extract its brand. Pure Python, stdlib only."""
from __future__ import annotations

import colorsys
import re
import urllib.parse
import urllib.request
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Sequence

from .models import BrandKit

NEUTRAL = BrandKit(
    name="",
    url="",
    tagline="",
    bg="#FAFAFA",
    ink="#111111",
    accent="#3B5BDB",
    font_family=None,
    logo_path=None,
)

PRESET_ACCENTS: tuple[str, ...] = ("#3B5BDB", "#E8590C", "#2F9E44", "#C2255C", "#7048E8")

USER_AGENT = "LaunchDesk/0.1 (+https://github.com/posts-design/hermes-launch-desk)"
TIMEOUT_S = 5
MAX_BYTES = 2 * 1024 * 1024
MAX_STYLESHEETS = 2
TAGLINE_MAX = 120

_GENERIC_FONTS = {
    "serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui",
    "ui-serif", "ui-sans-serif", "ui-monospace", "ui-rounded", "emoji", "math",
    "fangsong", "inherit", "initial", "unset", "revert", "-apple-system",
    "blinkmacsystemfont",
}
_HEX_RE = re.compile(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})(?![0-9a-zA-Z_-])")
_FONT_RE = re.compile(r"font-family\s*:\s*([^;}{]+)", re.IGNORECASE)
_TITLE_SPLIT_RE = re.compile(r"\s+[–|-]\s+")


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.theme_colors: list[tuple[str, str]] = []  # (content, media), in page order
        self.links: list[dict[str, str]] = []
        self.title = ""
        self.styles: list[str] = []
        self._in_title = False
        self._in_style = False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key and key not in self.meta:
                self.meta[key] = a.get("content", "")
            if key == "theme-color":
                self.theme_colors.append((a.get("content", ""), a.get("media", "").lower()))
        elif tag == "link":
            self.links.append(a)
        elif tag == "title":
            self._in_title = True
        elif tag == "style":
            self._in_style = True
        if a.get("style"):
            self.styles.append(a["style"])

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "style":
            self._in_style = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif self._in_style:
            self.styles.append(data)


def _norm_hex(raw: str) -> str:
    h = raw.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return "#" + h.upper()


def _parse_hex(value: str) -> str | None:
    m = _HEX_RE.fullmatch(value.strip())
    return _norm_hex(m.group(0)) if m else None


def _is_saturated(hex_: str) -> bool:
    r, g, b = (int(hex_[i : i + 2], 16) / 255 for i in (1, 3, 5))
    _h, light, sat = colorsys.rgb_to_hls(r, g, b)
    return sat >= 0.35 and 0.25 <= light <= 0.75


def _luminance(hex_: str) -> float:
    r, g, b = (int(hex_[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


# Colours of embedded social posts and buttons (X, LinkedIn, Facebook, Instagram, YouTube):
# common on a product page, never its brand. Normalised upper-case hex.
SOCIAL_COLOURS = frozenset({"#1D9BF0", "#1DA1F2", "#0A66C2", "#0077B5", "#1877F2", "#E1306C", "#FF0000"})


def _accent(sources: Sequence[str]) -> str:
    counts: Counter[str] = Counter()
    for text in sources:
        for m in _HEX_RE.finditer(text):
            h = _norm_hex(m.group(0))
            if _is_saturated(h) and h not in SOCIAL_COLOURS:
                counts[h] += 1
    return counts.most_common(1)[0][0] if counts else NEUTRAL.accent


def _first_family(decl: str) -> str | None:
    for part in decl.split(","):
        name = part.strip().strip("'\"").strip()
        if not name or "(" in name or "!" in name:
            continue
        if name.lower() in _GENERIC_FONTS:
            continue
        return name.title() if name.islower() and " " not in name else name
    return None


def _font_decl(sources: Sequence[str]) -> str | None:
    """The most used font-family list that names a family."""
    counts: Counter[str] = Counter()
    for text in sources:
        for m in _FONT_RE.finditer(text):
            counts[re.sub(r"\s+", " ", m.group(1).strip())] += 1
    for decl, _n in counts.most_common():
        if _first_family(decl):
            return decl
    return None


def _font_family(sources: Sequence[str]) -> str | None:
    decl = _font_decl(sources)
    return _first_family(decl) if decl else None


# ------------------------------------------------------------------ CSS, read just enough
# A small reader for the few things the card needs: the page background (light or dark),
# the accent the site names or puts on its buttons, and the headings' font. It is not a CSS
# engine: no cascade, no specificity, only the rules as written.

_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
_VAR_RE = re.compile(r"var\(\s*(--[\w-]+)\s*(?:,\s*([^()]*(?:\([^()]*\)[^()]*)*))?\)")
_ROOTS = frozenset({":root", "html", "body"})
_STATE_RE = re.compile(r":(?:hover|active|focus|focus-visible|focus-within|disabled|visited)\b|::")
_BUTTON_RE = re.compile(
    r"(?:^|[\s>+~])button(?![\w-])"
    r"|\.(?:btn|button|cta)(?:-primary)?(?![\w-])"
    r"|\.(?:primary-btn|primary-button)(?![\w-])"
    r"|\[type=[\"']?submit")
_H1_RE = re.compile(r"(?:^|[\s>+~])h1(?![\w-])")
_BG_TOKEN_RE = re.compile(r"--(?:(?:color|theme|site|page)-)?(?:background|bg)(?:-colou?r)?")
_ACCENT_WORDS = ("accent", "brand", "primary")
_ACCENT_EXTRA = frozenset({"color", "colour", "theme", "site", "ui"})
_NAMED = {"white": "#FFFFFF", "black": "#000000"}
_COLOUR_RE = re.compile(
    r"#[0-9a-fA-F]{3,8}(?![0-9a-zA-Z_-])|(?:rgba?|hsla?)\([^()]*\)|\b(?:white|black)\b", re.I)
_BARE_HSL_RE = re.compile(r"^\s*(-?[\d.]+)(?:deg)?\s+([\d.]+)%\s+([\d.]+)%\s*(?:/\s*[\d.]+%?)?\s*$")
DARK_LUMINANCE = 0.4  # below this the background is dark and the text goes light


def _css_rules(text: str) -> list[tuple[str, str, str]]:
    """(selector, declarations, enclosing at-rules) for each style rule, in order."""
    text = _COMMENT_RE.sub("", text)
    out: list[tuple[str, str, str]] = []
    stack: list[str] = []
    start = 0
    for m in re.finditer(r"[{}]", text):
        i, ch = m.start(), m.group(0)
        if ch == "{":
            stack.append(text[start:i].rsplit(";", 1)[-1].strip())
            start = i + 1
        else:
            if stack:
                prelude = stack.pop()
                if prelude and not prelude.startswith("@"):
                    out.append((prelude, text[start:i], " ".join(p for p in stack if p.startswith("@"))))
            start = i + 1
    return out


def _decls(body: str) -> list[tuple[str, str]]:
    out = []
    for part in body.split(";"):
        prop, sep, value = part.partition(":")
        if sep and prop.strip():
            out.append((prop.strip().lower(), re.sub(r"\s*!important\s*$", "", value.strip(), flags=re.I)))
    return out


def _parts(selector: str) -> list[str]:
    return [p.strip() for p in selector.split(",") if p.strip()]


def _is_root(selector: str) -> bool:
    parts = _parts(selector)
    return bool(parts) and all(p in _ROOTS for p in parts)


def _dark_context(context: str) -> bool:
    return "dark" in context.lower()


def _clamp(v: float) -> int:
    return max(0, min(255, round(v)))


def _hex_rgb(r: float, g: float, b: float) -> str:
    return "#%02X%02X%02X" % (_clamp(r), _clamp(g), _clamp(b))


def _num(text: str, scale: float = 255) -> float:
    text = text.strip()
    if text.endswith("%"):
        return float(text[:-1]) / 100 * scale
    return float(text)


def _alpha_ok(text: str | None) -> bool:
    if not text:
        return True
    try:
        return _num(text, 1) >= 0.5
    except ValueError:
        return False


def _hsl_hex(h: float, s: float, light: float) -> str:
    r, g, b = colorsys.hls_to_rgb((h % 360) / 360, light, s)
    return _hex_rgb(r * 255, g * 255, b * 255)


def _colour(token: str) -> str | None:
    """One CSS colour as upper-case #RRGGBB; None for anything mostly transparent or unknown."""
    t = token.strip()
    low = t.lower()
    if low in _NAMED:
        return _NAMED[low]
    if t.startswith("#"):
        h = t[1:]
        if len(h) in (4, 8):
            if int(h[3] * 2 if len(h) == 4 else h[6:8], 16) < 128:
                return None
            h = h[:3] if len(h) == 4 else h[:6]
        return _norm_hex(h) if len(h) in (3, 6) else None
    m = re.fullmatch(r"(rgba?|hsla?)\((.*)\)", low, re.S)
    if not m:
        return None
    body = m.group(2).replace("/", " / ")
    alpha = None
    if "/" in body:
        body, alpha = body.split("/", 1)
    args = [a for a in re.split(r"[\s,]+", body.strip()) if a]
    if len(args) == 4:
        args, alpha = args[:3], args[3]
    if len(args) != 3 or not _alpha_ok(alpha):
        return None
    try:
        if m.group(1).startswith("rgb"):
            return _hex_rgb(*(_num(a) for a in args))
        return _hsl_hex(float(args[0].replace("deg", "")), _num(args[1], 1), _num(args[2], 1))
    except ValueError:
        return None


def _resolve(value: str, tokens: dict[str, str], depth: int = 0) -> str:
    """The value with var(--x, fallback) replaced from the tokens, a few levels deep."""
    if depth > 5 or "var(" not in value:
        return value

    def repl(m: re.Match) -> str:
        found = tokens.get(m.group(1))
        return found if found is not None else (m.group(2) or "")
    return _resolve(_VAR_RE.sub(repl, value), tokens, depth + 1)


def _colours(value: str, tokens: dict[str, str]) -> list[str]:
    value = _resolve(value, tokens)
    bare = _BARE_HSL_RE.match(value)
    if bare:  # a token written as bare HSL numbers, "220 50% 10%"
        return [_hsl_hex(float(bare.group(1)), float(bare.group(2)) / 100, float(bare.group(3)) / 100)]
    return [c for c in (_colour(m.group(0)) for m in _COLOUR_RE.finditer(value)) if c]


class _Css:
    """The rules of every stylesheet and <style> block, and the custom properties set on
    :root, html or body outside any dark-mode block (the site's default theme)."""

    def __init__(self, sources: Sequence[str]) -> None:
        self.rules = [r for text in sources for r in _css_rules(text)]
        self.tokens: dict[str, str] = {}
        for selector, body, context in self.rules:
            if _is_root(selector) and not _dark_context(context):
                for prop, value in _decls(body):
                    if prop.startswith("--"):
                        self.tokens[prop] = value

    def default_rules(self):
        return [(s, b) for s, b, c in self.rules if not _dark_context(c)]

    def scheme_dark(self) -> bool:
        return any(prop == "color-scheme" and "dark" in value.lower()
                   for _s, body, _c in self.rules for prop, value in _decls(body))

    def page_background(self) -> str | None:
        """The background html, body or :root is given, else a background token on them."""
        found = None
        for selector, body in self.default_rules():
            if not _is_root(selector):
                continue
            for prop, value in _decls(body):
                if prop in ("background", "background-color"):
                    found = next(iter(_colours(value, self.tokens)), found)
        if found:
            return found
        for name, value in self.tokens.items():
            if _BG_TOKEN_RE.fullmatch(name):
                found = next(iter(_colours(value, self.tokens)), found)
        return found

    def accent_token(self) -> str | None:
        """The saturated colour of a token named like --accent, --brand or --color-primary."""
        ranked = []
        for pos, (name, value) in enumerate(self.tokens.items()):
            words = name[2:].lower().split("-")
            key = next((w for w in _ACCENT_WORDS if w in words), None)
            if key is None or any(w not in _ACCENT_EXTRA and w != key for w in words):
                continue
            colour = next(iter(_colours(value, self.tokens)), None)
            if colour and _brand_colour(colour):
                ranked.append((_ACCENT_WORDS.index(key), len(words), pos, colour))
        return min(ranked)[3] if ranked else None

    def button_colour(self) -> str | None:
        """The most common saturated background on button-like rules (resting state only)."""
        counts: Counter[str] = Counter()
        for selector, body in self.default_rules():
            parts = [p for p in _parts(selector) if _BUTTON_RE.search(p) and not _STATE_RE.search(p)]
            if not parts:
                continue
            for prop, value in _decls(body):
                if prop in ("background", "background-color", "background-image"):
                    for c in _colours(value, self.tokens):
                        if _brand_colour(c):
                            counts[c] += 1
        return counts.most_common(1)[0][0] if counts else None

    def heading_font(self) -> str | None:
        """The font-family declared for h1, when the site sets one."""
        for selector, body in self.default_rules():
            if not any(_H1_RE.search(p) for p in _parts(selector)):
                continue
            for prop, value in _decls(body):
                if prop == "font-family":
                    resolved = _resolve(value, self.tokens).strip()
                    if resolved and "var(" not in resolved:
                        return resolved
        return None


def _brand_colour(hex_: str) -> bool:
    return _is_saturated(hex_) and hex_ not in SOCIAL_COLOURS


# ------------------------------------------------------------------ the headline face

FACES = ("serif", "rounded", "sans")
_ROUNDED_RE = re.compile(
    r"\b(?:rounded|round|nunito|quicksand|comfortaa|baloo|fredoka|varela round|m plus rounded)\b")
_SERIF_RE = re.compile(
    r"\b(?:serif|georgia|times|iowan|palatino|garamond|charter|newsreader|baskerville|didot|bodoni"
    r"|caslon|cambria|book antiqua|merriweather|lora|playfair|fraunces|crimson|spectral|literata"
    r"|charis|minion|hoefler|big caslon|tiempos|canela|domine|cormorant)\b")


def font_face(family: str | None) -> str:
    """Which bundled face suits a font family: "serif", "rounded" or "sans" (Inter)."""
    name = re.sub(r"[_-]+", " ", (family or "").strip().strip("'\"").lower())
    if _ROUNDED_RE.search(name):
        return "rounded"
    if re.search(r"\bsans\b", name):
        return "sans"
    return "serif" if _SERIF_RE.search(name) else "sans"


def _stack_face(decl: str | None) -> str:
    """The face for a font-family list: its first named family, else its generic family."""
    names = [p.strip().strip("'\"").strip() for p in (decl or "").split(",")]
    named = [n for n in names if n and n.lower() not in _GENERIC_FONTS]
    if named:
        return font_face(named[0])
    generics = {n.lower() for n in names}
    if "ui-rounded" in generics:
        return "rounded"
    return "serif" if generics & {"serif", "ui-serif"} else "sans"


_TAGLINE_MIN_PART = 40  # a sentence or clause shorter than this is not worth keeping alone


def _cut_tagline(text: str) -> str:
    """At most 120 characters, ending on a whole sentence, else a whole clause, else a word
    plus an ellipsis, so the card never shows a phrase that stops mid-way."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= TAGLINE_MAX:
        return text
    head = text[: TAGLINE_MAX + 1]
    ends = [m.end() for m in re.finditer(r"[.!?](?=\s)", head)]
    if ends and ends[-1] >= _TAGLINE_MIN_PART:
        return head[: ends[-1]]
    # The first clause break past the minimum: a later comma is often inside a list.
    breaks = [m.start() for m in re.finditer(r",\s|;\s|\s[–-]\s", head) if m.start() >= _TAGLINE_MIN_PART]
    if breaks:
        return head[: breaks[0]].rstrip(" ,;:-–")
    cut = head[:TAGLINE_MAX - 1]
    cut = cut.rsplit(" ", 1)[0] if " " in cut else cut
    return cut.rstrip(" ,;:-–") + "…"


def _name(page: _Page, url: str) -> str:
    site = page.meta.get("og:site_name", "").strip()
    if site:
        return site
    title = re.sub(r"\s+", " ", page.title).strip()
    if title:
        return _TITLE_SPLIT_RE.split(title, maxsplit=1)[0].strip()
    return urllib.parse.urlsplit(url).hostname or ""


def _logo_url(page: _Page, url: str) -> str | None:
    def find(word: str) -> str | None:
        for link in page.links:
            rels = link.get("rel", "").lower().split()
            if word in rels and link.get("href", "").strip():
                return link["href"].strip()
        return None

    href = find("apple-touch-icon") or find("icon")
    if not href:
        return None
    absolute = urllib.parse.urljoin(url, href)
    return absolute if urllib.parse.urlsplit(absolute).scheme in ("http", "https") else None


def _background(page: _Page, sheet: _Css) -> str:
    """The card background. Dark when the site is dark: a dark theme-color for the dark
    scheme while the site declares color-scheme dark, a dark default theme-color, or a dark
    page background (html, body or :root, or their --background / --bg token). Else the
    default theme-color, the page background, or the neutral one."""
    light = dark = None
    for content, media in page.theme_colors:
        colour = _colour(content)
        if not colour:
            continue
        if "dark" in media:
            dark = dark or colour
        else:
            light = light or colour
    scheme = page.meta.get("color-scheme", "").lower()
    page_bg = sheet.page_background()

    def is_dark(c: str | None) -> bool:
        return bool(c) and _luminance(c) < DARK_LUMINANCE

    if dark and is_dark(dark) and ("dark" in scheme or sheet.scheme_dark()):
        return dark
    if is_dark(light):
        return light
    if is_dark(page_bg):
        return page_bg
    return light or page_bg or NEUTRAL.bg


def parse_brand(url: str, html: str, css: Sequence[str]) -> tuple[BrandKit, str | None]:
    """Brand kit from a page and its stylesheets, plus the absolute logo URL."""
    page = _Page()
    try:
        page.feed(html)
        page.close()
    except Exception:  # malformed markup: keep what was parsed
        pass
    tagline = page.meta.get("og:description", "").strip() or page.meta.get(
        "description", ""
    ).strip()
    sources = [*css, *page.styles]
    sheet = _Css(sources)
    bg = _background(page, sheet)
    ink = NEUTRAL.ink if _luminance(bg) >= DARK_LUMINANCE else NEUTRAL.bg
    decl = _font_decl(sources)
    kit = BrandKit(
        name=_name(page, url),
        url=url,
        tagline=_cut_tagline(tagline),
        bg=bg,
        ink=ink,
        accent=sheet.accent_token() or sheet.button_colour() or _accent(sources),
        font_family=_first_family(decl) if decl else None,
        logo_path=None,
        font_face=_stack_face(sheet.heading_font() or decl),
    )
    return kit, _logo_url(page, url)


MAX_REDIRECTS = 3


class _HttpOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """Follow at most 3 redirects, and only to http(s) URLs."""

    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl)
        if urllib.parse.urlsplit(target).scheme not in ("http", "https"):
            raise ValueError("redirected to a non-http URL")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def http_get(url: str) -> bytes:
    """GET with a 5 s timeout, a 2 MB cap, http(s) only. Raises on any failure."""
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        raise ValueError("only http and https URLs are allowed")
    opener = urllib.request.build_opener(_HttpOnlyRedirects)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with opener.open(req, timeout=TIMEOUT_S) as resp:  # noqa: S310
        if urllib.parse.urlsplit(resp.geturl()).scheme not in ("http", "https"):
            raise ValueError("redirected to a non-http URL")
        data = resp.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("response larger than 2 MB")
    return data


def _origin(url: str) -> tuple[str, str]:
    p = urllib.parse.urlsplit(url)
    return p.scheme.lower(), p.netloc.lower()


def _stylesheet_urls(html: str, url: str) -> list[str]:
    page = _Page()
    try:
        page.feed(html)
    except Exception:
        pass
    out: list[str] = []
    for link in page.links:
        if "stylesheet" not in link.get("rel", "").lower().split():
            continue
        href = link.get("href", "").strip()
        if not href:
            continue
        absolute = urllib.parse.urljoin(url, href)
        if _origin(absolute) == _origin(url) and absolute not in out:
            out.append(absolute)
        if len(out) == MAX_STYLESHEETS:
            break
    return out


def _logo_ext(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x00\x00\x01\x00"):
        return ".ico"
    return None


def _neutral_for(url: str) -> BrandKit:
    host = urllib.parse.urlsplit(url).hostname or ""
    return BrandKit(**{**NEUTRAL.__dict__, "name": host, "url": url})


def fetch_brand(url: str, get: Callable[[str], bytes], out_dir: Path) -> BrandKit:
    """Read the site with at most 4 GETs (page, 2 stylesheets, logo). Never raises."""
    try:
        html = get(url).decode("utf-8", errors="replace")
    except Exception:
        return _neutral_for(url)
    css: list[str] = []
    try:
        for sheet in _stylesheet_urls(html, url):
            try:
                css.append(get(sheet).decode("utf-8", errors="replace"))
            except Exception:
                continue
        kit, logo_url = parse_brand(url, html, css)
    except Exception:
        return _neutral_for(url)
    if logo_url:
        try:
            data = get(logo_url)
            ext = _logo_ext(data)
            if ext:
                out_dir = Path(out_dir)
                out_dir.mkdir(parents=True, exist_ok=True)
                dest = out_dir / f"logo{ext}"
                dest.write_bytes(data)
                kit = BrandKit(**{**kit.__dict__, "logo_path": str(dest)})
        except Exception:
            pass
    return kit
