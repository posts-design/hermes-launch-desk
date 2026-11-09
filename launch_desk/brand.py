"""Read a product site once and extract its brand. Pure Python, stdlib only."""
from __future__ import annotations

import colorsys
import io
import math
import re
import urllib.parse
import urllib.request
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Sequence

from PIL import Image

from .models import BrandKit
from .rules import _private_host

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

# Bounds, so any stylesheet within the 2 MB fetch cap reads in about a second: rules kept,
# brace nesting followed, a value's length after var() and the var()s replaced in one value.
MAX_RULES = 20_000
MAX_DEPTH = 32
MAX_VALUE = 2_000
MAX_SUBS = 32
SCAN_BUDGET = 1_000_000  # characters of resolved values scanned for colours in one read
MAX_COLOURS = 4  # colours taken from one value; every caller uses the first one or few

_VAR_RE = re.compile(r"var\(\s*(--[\w-]+)\s*(?:,\s*([^()]*(?:\([^()]*\)[^()]*)*))?\)")
_ROOTS = frozenset({":root", "html", "body"})
_ROOT_START_RE = re.compile(r"(?::root|html|body)(?![\w-])")
_STATE_RE = re.compile(r":(?:hover|active|focus|focus-visible|focus-within|disabled|visited)\b|::")
_BUTTON_RE = re.compile(
    r"(?:^|[\s>+~])button(?![\w-])"
    r"|\.(?:btn|button|cta)(?:-primary)?(?![\w-])"
    r"|\.(?:primary-btn|primary-button)(?![\w-])"
    r"|\[type=[\"']?submit")
_H1_RE = re.compile(r"(?:^|[\s>+~])h1(?![\w-])")
_BG_TOKEN_RE = re.compile(r"--(?:(?:color|theme|site|page)-)?(?:background|bg)(?:-colou?r)?")
_DARK_MEDIA_RE = re.compile(r"prefers-color-scheme\s*:\s*dark", re.I)
_ACCENT_WORDS = ("accent", "brand", "primary")
_ACCENT_EXTRA = frozenset({"color", "colour", "theme", "site", "ui"})
_NAMED = {"white": "#FFFFFF", "black": "#000000"}
_COLOUR_RE = re.compile(
    r"#[0-9a-zA-Z]{3,8}(?![0-9a-zA-Z_-])|(?:rgba?|hsla?|oklch|oklab)\([^()]*\)|\b(?:white|black)\b", re.I)
_HEX_DIGITS_RE = re.compile(r"[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8}")
_BARE_HSL_RE = re.compile(r"^\s*(-?[\d.]+)(?:deg)?\s+([\d.]+)%\s+([\d.]+)%\s*(?:/\s*[\d.]+%?)?\s*$")
_SCAN_RE = re.compile(r"[{}\"']")
DARK_LUMINANCE = 0.4  # below this the background is dark and the text goes light


def _strip_comments(text: str) -> str:
    """Comments out, in one pass; an unterminated comment drops the rest of the text."""
    out: list[str] = []
    i = 0
    while True:
        j = text.find("/*", i)
        if j < 0:
            out.append(text[i:])
            break
        out.append(text[i:j])
        k = text.find("*/", j + 2)
        if k < 0:
            break
        i = k + 2
    return "".join(out)


def _escaped(text: str, i: int) -> bool:
    """The character at i follows an odd number of backslashes."""
    n = 0
    while i - 1 - n >= 0 and text[i - 1 - n] == "\\":
        n += 1
    return n % 2 == 1


def _string_end(text: str, i: int) -> int:
    """Where the string opening at i ends: its closing quote, else the end of its line."""
    quote, j = text[i], i + 1
    while True:
        k = text.find(quote, j)
        if k < 0:
            nl = text.find("\n", i)
            return nl if nl >= 0 else len(text)
        if "\n" in text[j:k]:
            return text.find("\n", j)
        if not _escaped(text, k):
            return k
        j = k + 1


def _css_rules(text: str) -> list[tuple[str, str, bool]]:
    """(selector, declarations, inside a dark-scheme block) for each style rule, in order.
    Linear in the text: strings are skipped, at most MAX_RULES rules are kept and rules
    nested deeper than MAX_DEPTH are left out."""
    text = _strip_comments(text)
    out: list[tuple[str, str, bool]] = []
    stack: list[tuple[str, bool]] = []  # (prelude, opens a dark-scheme block)
    dark_open = 0
    start = pos = 0
    while len(out) < MAX_RULES:
        m = _SCAN_RE.search(text, pos)
        if not m:
            break
        i, ch = m.start(), m.group(0)
        if _escaped(text, i):  # a selector like .content-\[\'\'\] escapes quotes and braces
            pos = i + 1
            continue
        if ch in "\"'":
            pos = _string_end(text, i) + 1
            continue
        pos = i + 1
        if ch == "{":
            prelude = text[start:i].rsplit(";", 1)[-1].strip()
            dark = prelude.startswith("@") and bool(_DARK_MEDIA_RE.search(prelude))
            stack.append((prelude, dark))
            dark_open += dark
        elif stack:
            prelude, dark = stack.pop()
            dark_open -= dark
            if prelude and not prelude.startswith("@") and len(stack) < MAX_DEPTH:
                out.append((prelude, text[start:i], dark_open > 0))
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


def _starts_at_root(selector: str) -> bool:
    """Every part of the selector starts with :root, html or body (":root:not([data-theme])")."""
    parts = _parts(selector)
    return bool(parts) and all(_ROOT_START_RE.match(p) for p in parts)


def _dark_only(value: str) -> bool:
    """A color-scheme value that offers dark and not light: "dark" or "only dark"."""
    words = set(value.lower().split())
    return "dark" in words and "light" not in words


def _clamp(v: float) -> int:
    return max(0, min(255, round(v)))


def _hex_rgb(r: float, g: float, b: float) -> str:
    return "#%02X%02X%02X" % (_clamp(r), _clamp(g), _clamp(b))


def _num(text: str, scale: float = 255) -> float:
    """A CSS number or percentage; ValueError for anything else, infinity and NaN included."""
    text = text.strip()
    value = float(text[:-1]) / 100 * scale if text.endswith("%") else float(text)
    if not math.isfinite(value):
        raise ValueError(text)
    return value


def _alpha_ok(text: str | None) -> bool:
    if not text:
        return True
    try:
        return _num(text, 1) >= 0.5
    except ValueError:
        return False


def _hsl_hex(h: float, s: float, light: float) -> str:
    r, g, b = colorsys.hls_to_rgb((h % 360) / 360, min(max(light, 0), 1), min(max(s, 0), 1))
    return _hex_rgb(r * 255, g * 255, b * 255)


def _gamma(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return 12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055


def _oklab_hex(light: float, a: float, b: float) -> str:
    """OKLab to sRGB (Björn Ottosson's matrices), clipped to the sRGB gamut."""
    l_ = (light + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (light - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (light - 0.0894841775 * a - 1.2914855480 * b) ** 3
    r = 4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_
    g = -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_
    bl = -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076127010 * s_
    return _hex_rgb(_gamma(r) * 255, _gamma(g) * 255, _gamma(bl) * 255)


def _colour(token: str) -> str | None:
    """One CSS colour as upper-case #RRGGBB; None for anything mostly transparent, unknown or
    malformed. Reads hex, rgb(), hsl(), oklch(), oklab(), white and black; not color-mix()."""
    try:
        return _colour_or_raise(token)
    except (ValueError, OverflowError, ZeroDivisionError, IndexError):
        return None


def _colour_or_raise(token: str) -> str | None:
    t = token.strip()
    low = t.lower()
    if low in _NAMED:
        return _NAMED[low]
    if t.startswith("#"):
        h = t[1:]
        if not _HEX_DIGITS_RE.fullmatch(h):
            return None
        if len(h) in (4, 8):
            if int(h[3] * 2 if len(h) == 4 else h[6:8], 16) < 128:
                return None
            h = h[:3] if len(h) == 4 else h[:6]
        return _norm_hex(h)
    m = re.fullmatch(r"(rgba?|hsla?|oklch|oklab)\((.*)\)", low, re.S)
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
    kind = m.group(1)
    if kind.startswith("rgb"):
        return _hex_rgb(*(_num(a) for a in args))
    if kind.startswith("hsl"):
        return _hsl_hex(_num(args[0].replace("deg", "")), _num(args[1], 1), _num(args[2], 1))
    light = _num(args[0], 1)
    if kind == "oklch":
        chroma, hue = _num(args[1], 0.4), math.radians(_num(args[2].replace("deg", "")))
        return _oklab_hex(light, chroma * math.cos(hue), chroma * math.sin(hue))
    return _oklab_hex(light, _num(args[1], 0.4), _num(args[2], 0.4))


def _bare_hsl(value: str) -> str | None:
    """A token written as bare HSL numbers, "220 50% 10%"; None when it is not one."""
    m = _BARE_HSL_RE.match(value)
    if not m:
        return None
    try:
        h, s, light = (_num(m.group(1), 1), _num(m.group(2), 1), _num(m.group(3), 1))
        return _hsl_hex(h, s / 100, light / 100)
    except (ValueError, OverflowError):
        return None


def _brand_colour(hex_: str) -> bool:
    return _is_saturated(hex_) and hex_ not in SOCIAL_COLOURS


class _Css:
    """The rules of every stylesheet and <style> block, and the custom properties set on
    :root, html or body outside any dark-scheme block (the site's default theme).

    var() is resolved with each token worked out once (memoised, cycle-safe, at most
    MAX_DEPTH deep); a value that grows past MAX_VALUE characters or holds more than
    MAX_SUBS var()s resolves to "", so it never becomes a colour."""

    def __init__(self, sources: Sequence[str]) -> None:
        self.rules: list[tuple[str, str, bool]] = []
        for text in sources:
            if len(self.rules) >= MAX_RULES:
                break
            self.rules += _css_rules(text)[: MAX_RULES - len(self.rules)]
        self.raw: dict[str, str] = {}
        for selector, body, dark in self.rules:
            if _is_root(selector) and not dark:
                for prop, value in _decls(body):
                    if prop.startswith("--"):
                        self.raw[prop] = value
        self._memo: dict[str, str | None] = {}
        self._busy: set[str] = set()
        self._resolved: dict[str, str] = {}
        self._scanned: dict[str, list[str]] = {}
        self._budget = SCAN_BUDGET

    def _token(self, name: str) -> str | None:
        if name in self._memo:
            return self._memo[name]
        if name not in self.raw or name in self._busy or len(self._busy) >= MAX_DEPTH:
            return None
        self._busy.add(name)
        try:
            value = self.resolve(self.raw[name])
        finally:
            self._busy.discard(name)
        self._memo[name] = value
        return value

    def resolve(self, value: str) -> str:
        if len(value) > MAX_VALUE:
            return ""
        if "var(" not in value:
            return value
        parts: list[str] = []
        size = pos = 0
        for n, m in enumerate(_VAR_RE.finditer(value), 1):
            if n > MAX_SUBS:
                return ""
            found = self._token(m.group(1))
            if found is None:
                found = self.resolve(m.group(2)) if m.group(2) else ""
            parts += [value[pos:m.start()], found]
            size += m.start() - pos + len(found)
            if size > MAX_VALUE:
                return ""
            pos = m.end()
        parts.append(value[pos:])
        out = "".join(parts)
        return "" if len(out) > MAX_VALUE else out

    def colours(self, value: str) -> list[str]:
        """The first MAX_COLOURS colours in a value, var() resolved. Each distinct value is
        scanned once, and one read scans at most SCAN_BUDGET characters; past it, []."""
        if value not in self._resolved:
            if len(self._resolved) >= MAX_RULES:
                return []
            self._resolved[value] = self.resolve(value)
        resolved = self._resolved[value]
        if resolved in self._scanned:
            return self._scanned[resolved]
        if self._budget < len(resolved):
            self._budget = 0
            return []
        self._budget -= len(resolved)
        bare = _bare_hsl(resolved)
        found: list[str] = [bare] if bare else []
        if not bare:
            for m in _COLOUR_RE.finditer(resolved):
                c = _colour(m.group(0))
                if c:
                    found.append(c)
                    if len(found) == MAX_COLOURS:
                        break
        self._scanned[resolved] = found
        return found

    def default_rules(self):
        return [(s, b) for s, b, dark in self.rules if not dark]

    def scheme_dark(self) -> bool:
        """The site declares a dark-only color-scheme on :root, html or body."""
        return any(prop == "color-scheme" and _dark_only(value)
                   for selector, body, _d in self.rules if _starts_at_root(selector)
                   for prop, value in _decls(body))

    def page_background(self) -> str | None:
        """The background html, body or :root is given, else a background token on them."""
        found = None
        for selector, body in self.default_rules():
            if not _is_root(selector):
                continue
            for prop, value in _decls(body):
                if prop in ("background", "background-color"):
                    found = next(iter(self.colours(value)), found)
        if found:
            return found
        for name in self.raw:
            if _BG_TOKEN_RE.fullmatch(name):
                found = next(iter(self.colours(f"var({name})")), found)
        return found

    def accent_token(self) -> str | None:
        """The saturated colour of a token named like --accent, --brand or --color-primary."""
        ranked = []
        for pos, name in enumerate(self.raw):
            words = name[2:].lower().split("-")
            key = next((w for w in _ACCENT_WORDS if w in words), None)
            if key is None or any(w not in _ACCENT_EXTRA and w != key for w in words):
                continue
            colour = next(iter(self.colours(f"var({name})")), None)
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
                    for c in self.colours(value):
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
                    resolved = self.resolve(value).strip()
                    if resolved and "var(" not in resolved:
                        return resolved
        return None


def _signal(read: Callable[[], Any], default: Any = None) -> Any:
    """One brand signal; a value the reader cannot handle drops this signal, not the brand."""
    try:
        return read()
    except Exception:
        return default


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
        if _DARK_MEDIA_RE.search(media):
            dark = dark or colour
        else:
            light = light or colour
    scheme_meta = _dark_only(page.meta.get("color-scheme", ""))
    page_bg = _signal(sheet.page_background)

    def is_dark(c: str | None) -> bool:
        return bool(c) and _luminance(c) < DARK_LUMINANCE

    if dark and is_dark(dark) and (scheme_meta or _signal(sheet.scheme_dark, False)):
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
    sheet = _signal(lambda: _Css(sources)) or _Css([])
    bg = _signal(lambda: _background(page, sheet), NEUTRAL.bg)
    ink = NEUTRAL.ink if _luminance(bg) >= DARK_LUMINANCE else NEUTRAL.bg
    decl = _font_decl(sources)
    kit = BrandKit(
        name=_name(page, url),
        url=url,
        tagline=_cut_tagline(tagline),
        bg=bg,
        ink=ink,
        accent=(_signal(sheet.accent_token) or _signal(sheet.button_colour)
                or _signal(lambda: _accent(sources), NEUTRAL.accent)),
        font_family=_first_family(decl) if decl else None,
        logo_path=None,
        font_face=_signal(lambda: _stack_face(_signal(sheet.heading_font) or decl), "sans"),
    )
    return kit, _logo_url(page, url)


MAX_REDIRECTS = 3


def _host(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _private_elsewhere(target: str, own: str) -> bool:
    """A private, loopback or link-local host other than the founder's own (R59): a public site
    must not send the read to the founder's network or a metadata address. The founder's own
    staging host (http://127.0.0.1:8731/...) is still read."""
    host = _host(target)
    return bool(host) and host != _host(own) and _private_host(host)


class _HttpOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """Follow at most 3 redirects, only to http(s) URLs, and never to a private host other than
    the one first asked for."""

    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl)
        if urllib.parse.urlsplit(target).scheme not in ("http", "https"):
            raise ValueError("redirected to a non-http URL")
        first = getattr(req, "launch_desk_first_url", req.full_url)
        if _private_elsewhere(target, first):
            raise ValueError("redirected to a private address")
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            new.launch_desk_first_url = first
        return new


def http_get(url: str) -> bytes:
    """GET with a 5 s timeout, a 2 MB cap, http(s) only. Raises on any failure."""
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        raise ValueError("only http and https URLs are allowed")
    opener = urllib.request.build_opener(_HttpOnlyRedirects)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with opener.open(req, timeout=TIMEOUT_S) as resp:  # noqa: S310
        if urllib.parse.urlsplit(resp.geturl()).scheme not in ("http", "https"):
            raise ValueError("redirected to a non-http URL")
        if _private_elsewhere(resp.geturl(), url):
            raise ValueError("redirected to a private address")
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


LOGO_MAX_PIXELS = 4096 * 4096


def _small_enough(data: bytes) -> bool:
    """An image a logo can be: it opens (lazily, without decoding) and has at most 4096² pixels.
    A pixel-bomb icon would cost the gateway gigabytes to decode (I3)."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            width, height = im.size
    except Exception:
        return False
    return width * height <= LOGO_MAX_PIXELS


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
    if logo_url and not _private_elsewhere(logo_url, url):
        try:
            data = get(logo_url)
            ext = _logo_ext(data)
            if ext and _small_enough(data):
                out_dir = Path(out_dir)
                out_dir.mkdir(parents=True, exist_ok=True)
                dest = out_dir / f"logo{ext}"
                dest.write_bytes(data)
                kit = BrandKit(**{**kit.__dict__, "logo_path": str(dest)})
        except Exception:
            pass
    return kit
