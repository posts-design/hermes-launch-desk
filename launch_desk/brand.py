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


def _accent(sources: Sequence[str]) -> str:
    counts: Counter[str] = Counter()
    for text in sources:
        for m in _HEX_RE.finditer(text):
            h = _norm_hex(m.group(0))
            if _is_saturated(h):
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


def _font_family(sources: Sequence[str]) -> str | None:
    counts: Counter[str] = Counter()
    for text in sources:
        for m in _FONT_RE.finditer(text):
            counts[re.sub(r"\s+", " ", m.group(1).strip())] += 1
    for decl, _n in counts.most_common():
        fam = _first_family(decl)
        if fam:
            return fam
    return None


def _cut_tagline(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= TAGLINE_MAX:
        return text
    cut = text[: TAGLINE_MAX + 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    else:
        cut = cut[:TAGLINE_MAX]
    return cut.rstrip(" ,;:-–")


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
    bg = _parse_hex(page.meta.get("theme-color", "")) or NEUTRAL.bg
    ink = NEUTRAL.ink if _luminance(bg) >= 0.4 else NEUTRAL.bg
    sources = [*css, *page.styles]
    kit = BrandKit(
        name=_name(page, url),
        url=url,
        tagline=_cut_tagline(tagline),
        bg=bg,
        ink=ink,
        accent=_accent(sources),
        font_family=_font_family(sources),
        logo_path=None,
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
