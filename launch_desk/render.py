"""Share card and feed preview, drawn with Pillow from the founder's own assets.

Nothing here generates imagery: the card is the founder's screenshot, brand colours,
logo and headline. The headline is set in the bundled face nearest the brand's (Newsreader
for a serif brand, Nunito for a rounded one, else Inter); all smaller text is Inter. The
feed preview is a light, phone-width mock.
"""
from __future__ import annotations

import re
import urllib.parse
from functools import lru_cache
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat

from .brand import FACES, ICO_MAGIC, font_face, ico_within
from .models import BrandKit

FONT_DIR = Path(__file__).resolve().parent / "fonts"
FONT_PATH = FONT_DIR / "Inter.ttf"
# The headline faces, all SIL OFL (each licence beside its font in fonts/).
FACE_FONTS = {"serif": FONT_DIR / "Newsreader.ttf", "rounded": FONT_DIR / "Nunito.ttf", "sans": FONT_PATH}

MARGIN = 72
HEAD_MAX, HEAD_MIN = 64, 36
HEAD_WEIGHT = 700
LEADING = 1.12
SHOT_RADIUS = 20
MAX_UPSCALE = 2.0
TALL_RATIO = 1.25  # height / width at or above this is a phone-style screenshot
ELLIPSIS = "…"

RGB = tuple[int, int, int]


# ---------------------------------------------------------------- colour helpers

def _rgb(hex_: str | None, fallback: RGB = (17, 17, 17)) -> RGB:
    h = (hex_ or "").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)) if len(h) == 6 else fallback
    except ValueError:
        return fallback


def _mix(a: RGB, b: RGB, t: float) -> RGB:
    """t=0 gives a, t=1 gives b."""
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))  # type: ignore[return-value]


def _lum(c: RGB) -> float:
    def ch(v: int) -> float:
        s = v / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(v) for v in c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _on(c: RGB) -> RGB:
    """Readable text colour on top of c."""
    return (17, 17, 17) if _lum(c) > 0.45 else (255, 255, 255)


# ---------------------------------------------------------------- fonts and text

@lru_cache(maxsize=128)
def load_font(font_path: str, size: int, weight: int = 400) -> ImageFont.FreeTypeFont:
    """The font at a pixel size; a variable font gets its weight and optical size set."""
    font = ImageFont.truetype(font_path, max(1, int(size)))
    try:
        axes = font.get_variation_axes()
    except (OSError, AttributeError):
        return font
    values = []
    for axis in axes:
        name = axis.get("name", b"")
        name = name.decode("latin-1", "ignore") if isinstance(name, bytes) else str(name)
        lo, hi = axis.get("minimum", 0), axis.get("maximum", 0)
        if name.lower().startswith("weight") or name.lower() == "wght":
            values.append(min(max(weight, lo), hi))
        elif name.lower().startswith("optical") or name.lower() == "opsz":
            values.append(min(max(size, lo), hi))
        else:
            values.append(axis.get("default", lo))
    try:
        font.set_variation_by_axes(values)
    except (OSError, ValueError):
        pass
    return font


def _line_height(size: int) -> int:
    return max(1, round(size * LEADING))


def _wrap(words: list[str], font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    """Greedy wrap; a word wider than the box is broken by characters."""
    lines: list[str] = []
    cur = ""
    for word in words:
        trial = f"{cur} {word}" if cur else word
        if font.getlength(trial) <= width:
            cur = trial
            continue
        if cur:
            lines.append(cur)
            cur = ""
        while font.getlength(word) > width and len(word) > 1:
            cut = len(word) - 1
            while cut > 1 and font.getlength(word[:cut]) > width:
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        cur = word
    if cur:
        lines.append(cur)
    return lines


def _balance(words: list[str], lines: list[str], font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    """Same lines, broken as evenly as possible, never leaving the first line short."""
    if len(lines) < 2:
        return lines
    joined = " ".join(lines)

    def ok(cand: list[str]) -> bool:
        return len(cand) <= len(lines) and " ".join(cand) == joined

    lo, hi = 1, width
    while lo < hi:  # narrowest width that still gives the same text in as many lines
        mid = (lo + hi) // 2
        if ok(_wrap(words, font, mid)):
            hi = mid
        else:
            lo = mid + 1

    def score(cand: list[str]) -> float:
        widths = [font.getlength(line) for line in cand]
        return max(widths) + 0.5 * max(0.0, widths[-1] - widths[0])

    best, best_score = lines, score(lines)
    for i in range(17):
        cand = _wrap(words, font, lo + (width - lo) * i // 16)
        if ok(cand) and score(cand) < best_score:
            best, best_score = cand, score(cand)
    return best


def _ellipsize(line: str, font: ImageFont.FreeTypeFont, width: int) -> str:
    line = line.rstrip()
    while line and font.getlength(line + ELLIPSIS) > width:
        line = line[:-1].rstrip()
    return line + ELLIPSIS if font.getlength(line + ELLIPSIS) <= width else ""


def fit_text(
    text: str,
    font_path: str,
    box: tuple[int, int],
    max_size: int,
    min_size: int,
    weight: int,
    max_lines: int = 3,
) -> tuple[list[str], int]:
    """Largest size in [min_size, max_size] at which text wraps inside box.

    Lines are at most max_lines and each is no wider than the box. If the text does not fit
    at min_size, the last line that fits ends with an ellipsis. Always terminates.
    """
    width, height = max(1, int(box[0])), max(1, int(box[1]))
    hi, lo = max(1, int(max_size)), max(1, int(min_size))
    if lo > hi:
        lo, hi = hi, lo
    words = text.split()
    if not words:
        return [], hi
    for size in range(hi, lo - 1, -1):
        font = load_font(font_path, size, weight)
        allowed = min(max_lines, height // _line_height(size))
        if allowed < 1:
            continue
        lines = _wrap(words, font, width)
        if len(lines) <= allowed and all(font.getlength(line) <= width for line in lines):
            return _balance(words, lines, font, width), size
    font = load_font(font_path, lo, weight)
    allowed = max(1, min(max_lines, height // _line_height(lo)))
    wrapped = _wrap(words, font, width)
    lines = wrapped[:allowed - 1]
    last = _ellipsize(" ".join(wrapped[allowed - 1:]), font, width)
    lines = [line for line in lines + [last] if line and font.getlength(line) <= width]
    return lines, lo


def _cap_height(font: ImageFont.FreeTypeFont) -> int:
    box = font.getbbox("H", anchor="ls")
    return -box[1]


def _draw_lines(draw: ImageDraw.ImageDraw, lines: list[str], font, x: int, top: int,
                size: int, fill: RGB) -> int:
    """Draw lines so the first cap top sits at `top`; returns the last baseline + descent."""
    cap = _cap_height(font)
    lh = _line_height(size)
    base = top + cap
    for i, line in enumerate(lines):
        draw.text((x, base + i * lh), line, font=font, fill=fill, anchor="ls")
    return base + (len(lines) - 1) * lh + round(size * 0.24) if lines else top


def head_font_path(brand: BrandKit) -> Path:
    """The headline font for a brand: its face, or for a kit saved without one, the face of
    its font family. Inter when the face is unknown or its file is missing."""
    face = brand.font_face if brand.font_face in FACES else font_face(brand.font_family)
    path = FACE_FONTS.get(face, FONT_PATH)
    return path if path.is_file() else FONT_PATH


@lru_cache(maxsize=2048)
def _has_glyph(ch: str, font_path: str = str(FONT_PATH)) -> bool:
    """False for characters the font would draw as an empty box (emoji, mostly)."""
    if ord(ch) < 0x80 or ch.isspace():
        return True
    return _glyph_bitmap(ch, font_path) != _glyph_bitmap("\U000F0000", font_path)


@lru_cache(maxsize=2048)
def _glyph_bitmap(ch: str, font_path: str) -> bytes:
    font = load_font(font_path, 32, 400)
    im = Image.new("L", (64, 64), 0)
    ImageDraw.Draw(im).text((8, 8), ch, font=font, fill=255)
    return im.tobytes()


def _drop_missing(text: str, font_path: str = str(FONT_PATH)) -> str:
    """Drop characters the font cannot draw, so no empty boxes reach the image."""
    out = "".join(ch for ch in text if _has_glyph(ch, font_path))
    return re.sub(r"[ \t]{2,}", " ", out).strip() if out != text else text


# ---------------------------------------------------------------- images

OPEN_MAX_PIXELS = 40_000_000  # a screenshot or logo larger than this is never decoded (I3)


def _open_image(path: Path | str | None) -> Image.Image | None:
    if not path:
        return None
    try:
        with open(path, "rb") as handle:
            head = handle.read(4)
            if head == ICO_MAGIC and not ico_within(head + handle.read(), OPEN_MAX_PIXELS):
                return None  # an ICO frame is decoded on open: check its directory first (N1)
        im = Image.open(path)
        if im.size[0] * im.size[1] > OPEN_MAX_PIXELS:
            return None
        if im.format == "ICO" and hasattr(im, "ico"):
            sizes = list(im.ico.sizes())
            if sizes:
                im = im.ico.getimage(max(sizes, key=lambda s: s[0] * s[1]))
        if getattr(im, "n_frames", 1) > 1:
            im.seek(0)
        im.load()
        return ImageOps.exif_transpose(im)
    except Exception:  # undecodable, missing or truncated: the caller falls back
        return None


def _flatten(im: Image.Image, bg: RGB) -> Image.Image:
    if im.mode in ("RGBA", "LA", "P", "PA"):
        rgba = im.convert("RGBA")
        base = Image.new("RGB", rgba.size, bg)
        base.paste(rgba, mask=rgba.getchannel("A"))
        return base
    return im.convert("RGB")


def _trim_logo(im: Image.Image) -> Image.Image:
    rgba = im.convert("RGBA")
    box = rgba.getchannel("A").getbbox()
    return rgba.crop(box) if box else rgba


def _mask(size: tuple[int, int], radius: int, top_only: bool = False, ss: int = 4,
          outline: bool = False) -> Image.Image:
    """Anti-aliased rounded-rectangle mask (or its 1 px outline)."""
    w, h = size
    big = Image.new("L", (w * ss, h * ss), 0)
    d = ImageDraw.Draw(big)
    r = radius * ss
    rect = (0, 0, w * ss - 1, h * ss - 1 + (r if top_only else 0))
    if outline:
        d.rounded_rectangle(rect, r, outline=255, width=ss)
    else:
        d.rounded_rectangle(rect, r, fill=255)
    return big.resize((w, h), Image.Resampling.LANCZOS)


def _circle_mask(d: int, ss: int = 4) -> Image.Image:
    big = Image.new("L", (d * ss, d * ss), 0)
    ImageDraw.Draw(big).ellipse((0, 0, d * ss - 1, d * ss - 1), fill=255)
    return big.resize((d, d), Image.Resampling.LANCZOS)


def _scale(im: Image.Image, w: int, h: int) -> Image.Image:
    return im.resize((max(1, w), max(1, h)), Image.Resampling.LANCZOS)


def _place_shot(canvas: Image.Image, shot: Image.Image, x: int, y: int, w: int, h: int,
                visible_h: int, ink: RGB, dark: bool) -> None:
    """Paste the scaled screenshot with rounded corners, a soft shadow and a hairline."""
    W, H = canvas.size
    scaled = _scale(shot, w, h).crop((0, 0, w, visible_h))
    bleeds = visible_h < h
    mask = _mask((w, visible_h), SHOT_RADIUS, top_only=bleeds)

    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for dy, blur, alpha in ((22, 34, 70 if not dark else 140), (3, 5, 26 if not dark else 90)):
        layer = Image.new("L", (W, H), 0)
        layer.paste(mask.point(lambda v, a=alpha: v * a // 255), (x, y + dy))
        layer = layer.filter(ImageFilter.GaussianBlur(blur))
        tint = Image.new("RGBA", (W, H), (0, 0, 0, 255))
        tint.putalpha(layer)
        shadow = Image.alpha_composite(shadow, tint)
    base = canvas.convert("RGBA")
    base = Image.alpha_composite(base, shadow)
    base.paste(scaled, (x, y), mask)

    ring = _mask((w, visible_h), SHOT_RADIUS, top_only=bleeds, outline=True)
    hair = Image.new("RGBA", (w, visible_h), ink + (255,))
    hair.putalpha(ring.point(lambda v: v * (40 if dark else 22) // 255))
    base.alpha_composite(hair, (x, y))
    canvas.paste(base.convert("RGB"))


# ---------------------------------------------------------------- brand mark

def _display_name(brand: BrandKit) -> str:
    name = _drop_missing(" ".join(brand.name.split()))
    if name:
        return name
    host = urllib.parse.urlparse(brand.url or "").hostname or ""
    return host.removeprefix("www.") or "Launch"


def _host(brand: BrandKit) -> str:
    host = urllib.parse.urlparse(brand.url or "").hostname or ""
    return host.removeprefix("www.")


def _initial(name: str) -> str:
    for ch in name:
        if ch.isalnum():
            return ch.upper()
    return "•"


LIGHT_MARK = 0.5  # mean luminance of a logo's opaque pixels above which it needs a dark plate


def _mark_luminance(lg: Image.Image) -> float:
    """Luminance of the mean colour of the logo's opaque pixels."""
    alpha = lg.getchannel("A").point(lambda v: 255 if v > 127 else 0)
    if not alpha.getbbox():
        return 0.0
    mean = ImageStat.Stat(lg.convert("RGB"), mask=alpha).mean
    return _lum(tuple(round(v) for v in mean[:3]))  # type: ignore[arg-type]


def _plate(brand: BrandKit, lg: Image.Image, accent: RGB) -> RGB:
    """White under a dark or colourful mark; under a light one, the brand's dark
    background, else the accent (or near-black when the accent is light too)."""
    if _mark_luminance(lg) <= LIGHT_MARK:
        return (255, 255, 255)
    bg = _rgb(brand.bg, (250, 250, 250))
    if _lum(bg) < 0.25:
        return bg
    return accent if _lum(accent) < 0.45 else (17, 17, 17)


def _mark_tile(brand: BrandKit, logo: Image.Image | None, d: int, accent: RGB,
               circle: bool = False) -> Image.Image:
    """Square (or round) tile: the logo when it decodes, else the initial on the accent."""
    tile = Image.new("RGBA", (d, d), (0, 0, 0, 0))
    mask = _circle_mask(d) if circle else _mask((d, d), round(d * 0.24))
    if logo is not None and not logo.convert("RGBA").getchannel("A").getbbox():
        logo = None  # nothing visible in it: use the initial
    if logo is not None:
        lg = _trim_logo(logo)
        alpha = lg.getchannel("A")
        opaque_corners = all(alpha.getpixel(p) > 200 for p in
                             ((0, 0), (lg.width - 1, 0), (0, lg.height - 1), (lg.width - 1, lg.height - 1)))
        if opaque_corners:
            # A full-bleed icon (app icon style): cover the tile.
            fitted = ImageOps.fit(lg, (d, d), Image.Resampling.LANCZOS)
            tile.paste(fitted, (0, 0))
        else:
            # A transparent mark: centre it on a plate that it reads against.
            plate = Image.new("RGBA", (d, d), _plate(brand, lg, accent) + (255,))
            inner = round(d * 0.72)
            lg.thumbnail((inner, inner), Image.Resampling.LANCZOS)
            if max(lg.size) < inner:
                k = inner / max(lg.size)
                lg = _scale(lg, round(lg.width * k), round(lg.height * k))
            plate.alpha_composite(lg, ((d - lg.width) // 2, (d - lg.height) // 2))
            tile = plate
    else:
        tile = Image.new("RGBA", (d, d), accent + (255,))
        font = load_font(str(FONT_PATH), round(d * 0.5), 700)
        ImageDraw.Draw(tile).text((d / 2, d / 2), _initial(_display_name(brand)), font=font,
                                  fill=_on(accent), anchor="mm")
    out = Image.new("RGBA", (d, d), (0, 0, 0, 0))
    out.paste(tile, (0, 0), mask)
    return out


# ---------------------------------------------------------------- card

def _fit_shot(shot: Image.Image, max_w: int, max_h: int) -> tuple[int, int]:
    k = min(max_w / shot.width, max_h / shot.height, MAX_UPSCALE)
    return max(1, round(shot.width * k)), max(1, round(shot.height * k))


def _width_shot(shot: Image.Image, w: int) -> tuple[int, int]:
    """Scale to a column width, never past MAX_UPSCALE."""
    w = max(1, min(w, round(shot.width * MAX_UPSCALE)))
    return w, max(1, round(shot.height * w / shot.width))


class _Card:
    """One card being composed: brand colours, scale and the shared drawing steps."""

    def __init__(self, brand: BrandKit, size: tuple[int, int], logo: Image.Image | None) -> None:
        self.brand, self.logo = brand, logo
        self.W, self.H = size
        self.bg = _rgb(brand.bg, (250, 250, 250))
        self.ink = _rgb(brand.ink, _on(self.bg))
        self.accent = _rgb(brand.accent, (59, 91, 219))
        self.dark = _lum(self.bg) < 0.25
        self.muted = _mix(self.ink, self.bg, 0.42)
        # 1.0 on the 1200x675 card; the 1080 wide card scales type and furniture by 1.25.
        self.s = min(min(self.W, self.H) / 675, 1.25)
        self.m = MARGIN
        self.gap = round(48 * self.s)
        self.footer_h = round(56 * self.s)
        self.hmax, self.hmin = round(HEAD_MAX * self.s), round(HEAD_MIN * self.s)
        self.canvas = Image.new("RGB", (self.W, self.H), self.bg)
        self.draw = ImageDraw.Draw(self.canvas)
        self.font_path = str(FONT_PATH)
        self.head_path = str(head_font_path(brand))  # the headline only; smaller text stays Inter

    def px(self, v: float) -> int:
        return round(v * self.s)

    # -- type
    def headline(self, text: str, x: int, top: int, box: tuple[int, int], hmax: int | None = None,
                 max_lines: int = 3) -> int:
        lines, size = fit_text(text, self.head_path, box, hmax or self.hmax, self.hmin, HEAD_WEIGHT,
                               max_lines=max_lines)
        return _draw_lines(self.draw, lines, load_font(self.head_path, size, HEAD_WEIGHT), x, top,
                           size, self.ink)

    def tagline(self, text: str, x: int, top: int, box: tuple[int, int], max_lines: int,
                big: float = 28, small: float = 20) -> int:
        lines, size = fit_text(text, self.font_path, box, self.px(big), self.px(small), 420,
                               max_lines=max_lines)
        if not lines or box[1] < _line_height(size):
            return top
        return _draw_lines(self.draw, lines, load_font(self.font_path, size, 420), x, top, size,
                           self.muted)

    def accent_mark(self, x: int, y: int) -> int:
        w, h = self.px(44), self.px(8)
        self.draw.rounded_rectangle((x, y, x + w, y + h), h // 2, fill=self.accent)
        return y + h + self.px(30)

    # -- footer: brand mark, name, and the site host when it differs from the name
    def _footer_text(self, max_w: int) -> tuple[list[str], int, str]:
        tw = max(1, max_w - self.px(56 + 18))
        name = _display_name(self.brand)
        lines, size = fit_text(name, self.font_path, (tw, self.px(40)), self.px(26), self.px(18), 650,
                               max_lines=1)
        host = _host(self.brand)
        if host and host.lower() != name.lower():
            hf = load_font(self.font_path, self.px(20), 450)
            host = host if hf.getlength(host) <= tw else _ellipsize(host, hf, tw)
        else:
            host = ""
        return lines, size, host

    def footer_width(self, max_w: int) -> int:
        lines, size, host = self._footer_text(max_w)
        nf = load_font(self.font_path, size, 650)
        hf = load_font(self.font_path, self.px(20), 450)
        text_w = max(nf.getlength(lines[0]) if lines else 0, hf.getlength(host) if host else 0)
        return self.px(56 + 18) + round(text_w)

    def footer(self, max_w: int, x: int | None = None) -> None:
        x, bottom, d = self.m if x is None else x, self.H - self.m, self.px(56)
        tile = _mark_tile(self.brand, self.logo, d, self.accent)
        self.canvas.paste(tile, (x, bottom - d), tile)
        lines, size, host = self._footer_text(max_w)
        tx = x + d + self.px(18)
        nf = load_font(self.font_path, size, 650)
        if host:
            hf = load_font(self.font_path, self.px(20), 450)
            self.draw.text((tx, bottom - d / 2 - self.px(5)), lines[0] if lines else "", font=nf,
                           fill=self.ink, anchor="ls")
            self.draw.text((tx, bottom - d / 2 + self.px(7) + _cap_height(hf)), host, font=hf,
                           fill=self.muted, anchor="ls")
        elif lines:
            self.draw.text((tx, bottom - d / 2), lines[0], font=nf, fill=self.ink, anchor="lm")

    # -- a compact brand row: tile and name on one line
    def _brand_name(self, max_w: int, d: int) -> tuple[str, ImageFont.FreeTypeFont]:
        tw = max(1, max_w - d - self.px(14))
        lines, size = fit_text(_display_name(self.brand), self.font_path, (tw, self.px(40)),
                               self.px(24), self.px(16), 650, max_lines=1)
        return (lines[0] if lines else ""), load_font(self.font_path, size, 650)

    def brand_row_width(self, max_w: int, d: int | None = None) -> int:
        d = d or self.px(44)
        name, font = self._brand_name(max_w, d)
        return d + self.px(14) + round(font.getlength(name))

    def brand_row(self, x: int, y: int, max_w: int, d: int | None = None) -> None:
        d = d or self.px(44)
        tile = _mark_tile(self.brand, self.logo, d, self.accent)
        self.canvas.paste(tile, (x, y), tile)
        name, font = self._brand_name(max_w, d)
        self.draw.text((x + d + self.px(14), y + d / 2), name, font=font, fill=self.ink, anchor="lm")

    def label(self, text: str, x: int, y: int, w: int, font: ImageFont.FreeTypeFont) -> None:
        if text:
            shown = text if font.getlength(text) <= w else _ellipsize(text, font, w)
            self.draw.text((x, y), shown, font=font, fill=self.muted, anchor="lt")

    def shot(self, im: Image.Image, x: int, y: int, w: int, h: int, visible: int | None = None) -> None:
        _place_shot(self.canvas, im, x, y, w, h, min(h, visible if visible else h), self.ink, self.dark)


def _block_height(text: str, font_path: str, box: tuple[int, int], hmax: int, hmin: int,
                  weight: int, max_lines: int) -> int:
    lines, size = fit_text(text, font_path, box, hmax, hmin, weight, max_lines=max_lines)
    if not lines:
        return 0
    font = load_font(font_path, size, weight)
    return _cap_height(font) + (len(lines) - 1) * _line_height(size) + round(size * 0.24)


# ---------------------------------------------------------------- layouts

LAYOUTS = ("hero", "statement", "split")
SPLIT_TALLEST = 2.2  # height / width; a taller screenshot keeps its top part in a split


def _as_list(screenshots: Sequence[Path | str | None] | Path | str | None) -> list[Path | str]:
    if screenshots is None:
        return []
    if isinstance(screenshots, (str, Path)):
        return [screenshots] if str(screenshots) else []
    return [s for s in screenshots if s]


def _layout_for(usable: int) -> str:
    return "split" if usable >= 2 else "hero" if usable == 1 else "statement"


def choose_layout(screenshots: Sequence[Path | str], hook: str) -> str:
    """The card layout for these inputs. Same inputs, same answer; nothing is stored.

    Only usable screenshots count (a file that opens as an image):
      two or more -> "split": the newest two side by side, in the order they were attached;
      one         -> "hero": the headline with the screenshot;
      none        -> "statement": the headline (the card line) set large, with the brand.
    The statement card leaves screenshots out, so it is used only when the founder has none: a
    real screenshot always shows. The headline is part of the signature so a changed headline
    re-runs this rule.
    """
    return _layout_for(sum(1 for p in _as_list(screenshots) if _open_image(p) is not None))


def render_card(brand: BrandKit, screenshots: Sequence[Path | str | None] | Path | str | None,
                headline: str, size: tuple[int, int], out: Path, layout: str | None = None,
                labels: Sequence[str] | None = None) -> Path:
    """Compose the launch card at exactly `size` and save it as PNG to `out`.

    `screenshots` is a list (oldest first) or a single path. `layout` is one of LAYOUTS and
    defaults to what choose_layout() picks; a layout the screenshots cannot fill (split with
    one, hero with none) falls back to the one they can. `labels`, one per screenshot, are
    small captions for the split card; without them it has none.
    """
    paths = _as_list(screenshots)
    texts = list(labels or [])
    opened = [(im, texts[i] if i < len(texts) else "") for i, p in enumerate(paths)
              if (im := _open_image(p)) is not None][-2:]
    logo_raw = _open_image(brand.logo_path)
    card = _Card(brand, (int(size[0]), int(size[1])), logo_raw.convert("RGBA") if logo_raw else None)
    headline = " ".join(headline.split())
    if _drop_missing(headline, card.head_path) != _drop_missing(headline):
        card.head_path = str(FONT_PATH)  # the brand face lacks a letter Inter has (Cyrillic, Greek)
    headline = _drop_missing(headline, card.head_path) or _display_name(brand)
    shots = [_flatten(im, card.bg) for im, _ in opened]
    fits = _layout_for(len(shots))
    if layout not in LAYOUTS or (layout == "split" and len(shots) < 2) or (layout == "hero" and not shots):
        layout = fits
    if layout == "statement":
        _statement(card, headline)
    elif layout == "split":
        _split(card, shots, headline, [_drop_missing(" ".join(t.split())) for _, t in opened])
    else:
        tagline = _drop_missing(" ".join((brand.tagline or "").split()))
        if tagline.lower() == headline.lower():
            tagline = ""
        (_portrait if card.H > card.W else _landscape)(card, shots[-1], headline, tagline)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    card.canvas.save(out, "PNG", optimize=True)
    return out


# -- statement: the headline set large



def _glow(c: _Card) -> None:
    """A soft accent light rising from the bottom-right corner; the top-left stays the brand bg."""
    r = round(0.9 * (c.W ** 2 + c.H ** 2) ** 0.5)
    k = 4  # drawn at a quarter size, then smoothed up
    small = Image.radial_gradient("L").resize((2 * r // k, 2 * r // k), Image.Resampling.BILINEAR)
    peak = 0.17 if c.dark else 0.13
    # radial_gradient reaches 255 at its corners, so v / 255 * sqrt(2) is the distance in radii
    small = small.point(lambda v: round(255 * peak * max(0.0, 1 - v / 255 * 2 ** 0.5) ** 2.2))
    layer = Image.new("L", (c.W // k + 1, c.H // k + 1), 0)
    layer.paste(small, (c.W // k - r // k, c.H // k - r // k))
    layer = layer.resize((layer.width * k, layer.height * k), Image.Resampling.BILINEAR).crop((0, 0, c.W, c.H))
    c.canvas.paste(Image.new("RGB", (c.W, c.H), c.accent), (0, 0), layer)


STATEMENT_WEIGHT = 760


class _StatementGrid:
    """The statement card's fixed parts, so the hook gets exactly the room that is left."""

    def __init__(self, c: _Card) -> None:
        portrait = c.H > c.W
        self.rule_gap, self.rule_h, self.rule_w = c.px(44 if portrait else 34), c.px(6), c.px(72)
        self.top = c.m
        self.bottom = c.H - c.m - c.footer_h - c.gap  # above the brand
        fixed = self.rule_gap + self.rule_h
        self.box = (c.W - 2 * c.m - c.px(40 if portrait else 120), self.bottom - self.top - fixed)
        self.hmax = c.px(104 if portrait else 96)
        self.max_lines = 5 if portrait else 4


def _statement_text(c: _Card, headline: str) -> tuple[list[str], int]:
    """The lines and size the statement card sets the headline in."""
    g = _StatementGrid(c)
    return fit_text(headline, c.head_path, g.box, g.hmax, c.hmin, STATEMENT_WEIGHT, max_lines=g.max_lines)


def _statement(c: _Card, headline: str) -> None:
    """Centred: the headline set large, one accent rule, then the brand; a soft accent light rises
    from the bottom-right corner."""
    _glow(c)
    g = _StatementGrid(c)
    lines, size = _statement_text(c, headline)
    font = load_font(c.head_path, size, STATEMENT_WEIGHT)
    lh = _line_height(size)
    cap = _cap_height(font)
    group = cap + (len(lines) - 1) * lh + g.rule_gap + g.rule_h
    top = g.top + max(0, (g.bottom - g.top - group) // 2)
    base = top + cap
    for i, line in enumerate(lines):
        c.draw.text((c.W / 2, base + i * lh), line, font=font, fill=c.ink, anchor="ms")
    rule_y = base + (len(lines) - 1) * lh + g.rule_gap
    c.draw.rounded_rectangle(((c.W - g.rule_w) // 2, rule_y, (c.W + g.rule_w) // 2, rule_y + g.rule_h),
                             g.rule_h // 2, fill=c.accent)
    max_w = c.W - 2 * c.m
    c.footer(max_w, x=(c.W - c.footer_width(max_w)) // 2)


# -- split: two screenshots side by side

def _crop_tall(shot: Image.Image) -> Image.Image:
    """A full-page capture keeps its top, phone-screen shaped, so it stays readable."""
    if shot.height / shot.width <= SPLIT_TALLEST:
        return shot
    return shot.crop((0, 0, shot.width, round(shot.width * SPLIT_TALLEST)))


def _pair(shots: list[Image.Image], max_w: int, max_h: int, gap: int,
          stacked: bool) -> list[tuple[int, int]]:
    """Sizes for two screenshots at one height side by side (or one width stacked), as large
    as fits in max_w x max_h, never upscaled past MAX_UPSCALE."""
    if stacked:
        inv = [s.height / s.width for s in shots]
        w = min(max_w, (max_h - gap) / sum(inv), *(s.width * MAX_UPSCALE for s in shots))
        w = max(1, int(w))
        return [(w, max(1, int(w * r))) for r in inv]
    ratios = [s.width / s.height for s in shots]
    h = min(max_h, (max_w - gap) / sum(ratios), *(s.height * MAX_UPSCALE for s in shots))
    h = max(1, int(h))
    return [(max(1, int(h * r)), h) for r in ratios]


def _area(sizes: list[tuple[int, int]]) -> int:
    return sum(w * h for w, h in sizes)


def _place_pair(c: _Card, shots: list[Image.Image], sizes: list[tuple[int, int]], stacked: bool,
                stage: tuple[int, int, int, int], labels: list[str], gap: int) -> None:
    """Draw the brand-tinted stage and the two screenshots centred on it."""
    c.draw.rounded_rectangle(stage, c.px(28), fill=_mix(c.bg, c.accent, 0.13 if c.dark else 0.09))
    label_on = any(labels)
    lf = load_font(c.font_path, c.px(19), 600)
    label_h = c.px(34) if label_on else 0
    cx = (stage[0] + stage[2]) // 2
    if stacked:
        total = sum(h for _, h in sizes) + gap + 2 * label_h
        y = stage[1] + (stage[3] - stage[1] - total) // 2
        for shot, (w, h), label in zip(shots, sizes, labels):
            x = cx - w // 2
            if label_on:
                c.label(label, x, y, w, lf)
                y += label_h
            c.shot(shot, x, y, w, h)
            y += h + gap
        return
    x = cx - (sum(w for w, _ in sizes) + gap) // 2
    h = sizes[0][1]
    y = stage[1] + (stage[3] - stage[1] - h - label_h) // 2
    for shot, (w, _), label in zip(shots, sizes, labels):
        if label_on:
            c.label(label, x, y, w, lf)
        c.shot(shot, x, y + label_h, w, h)
        x += w + gap


def _split(c: _Card, shots: list[Image.Image], headline: str, labels: list[str]) -> None:
    """Two screenshots on a brand-tinted stage, at one height and top-aligned (on the tall card,
    stacked at one width when that shows them larger), with the headline and brand above."""
    W, H, m = c.W, c.H, c.m
    shots = [_crop_tall(s) for s in shots]
    inset, pad, gap = c.px(28), c.px(40), c.px(28)
    label_h = c.px(34) if any(labels) else 0
    max_w = W - 2 * inset - 2 * pad
    if H <= W:
        # Headline left and brand right on one row; the stage fills the rest.
        brand_w = c.brand_row_width(round(W * 0.3))
        text_w = W - 2 * m - brand_w - c.px(48)
        top = m - c.px(8)
        end = c.headline(headline, m, top, (text_w, 2 * _line_height(c.px(52)) + 4),
                         hmax=c.px(52), max_lines=2)
        cap = _cap_height(load_font(c.head_path, c.px(52), HEAD_WEIGHT))
        c.brand_row(W - m - brand_w, top + cap // 2 - c.px(22), brand_w, d=c.px(44))
        stage = (inset, end + c.px(44), W - inset, H - inset)
        sizes = _pair(shots, max_w, stage[3] - stage[1] - 2 * pad - label_h, gap, stacked=False)
        _place_pair(c, shots, sizes, False, stage, labels, gap)
        return
    # Tall card: brand row, then the headline, then a stage that hugs the screenshots;
    # what the screenshots do not need goes to the headline.
    max_h = round(H * 0.55) - 2 * pad - label_h
    side = _pair(shots, max_w, max_h, gap, stacked=False)
    stack = _pair(shots, max_w, max_h - label_h, gap, stacked=True)
    stacked = _area(stack) > _area(side)
    sizes = stack if stacked else side
    content_h = (sum(h for _, h in sizes) + gap + 2 * label_h) if stacked else sizes[0][1] + label_h
    stage = (inset, H - inset - content_h - 2 * pad, W - inset, H - inset)
    d = c.px(44)
    c.brand_row(m, m, W - 2 * m, d=d)
    head_top, head_bottom = m + d + c.px(48), stage[1] - c.px(48)
    hmax = round(c.hmax * 1.5)
    box = (W - 2 * m, head_bottom - head_top)
    block = _block_height(headline, c.head_path, box, hmax, c.hmin, HEAD_WEIGHT, 4)
    c.headline(headline, m, head_top + max(0, (box[1] - block) // 2), box, hmax=hmax, max_lines=4)
    _place_pair(c, shots, sizes, stacked, stage, labels, gap)


def _landscape(c: _Card, shot: Image.Image, headline: str, tagline: str) -> None:
    W, H, m = c.W, c.H, c.m
    ratio = shot.height / shot.width
    if ratio >= 0.8:
        # Side by side: the text column on the left, the screenshot as a column on the right.
        if ratio >= TALL_RATIO:  # a phone screenshot: keep it readable, run it off the bottom
            w, h = _width_shot(shot, round(W * 0.30))
        else:
            w, h = _fit_shot(shot, round(W * 0.40), H - 2 * m)
        x = W - m - w
        if h <= H - 2 * m:
            c.shot(shot, x, (H - h) // 2, w, h)
        else:
            c.shot(shot, x, m, w, h, visible=H - m)
        text_w = x - m - c.px(64)
        y = c.accent_mark(m, m)
        bottom_limit = H - m - c.footer_h - c.gap
        end = c.headline(headline, m, y, (text_w, bottom_limit - y))
        if tagline:
            c.tagline(tagline, m, end + c.px(24), (text_w, bottom_limit - end - c.px(24)), 3,
                      big=24, small=19)
        c.footer(text_w)
        return
    # Wide screenshot: headline across the top, screenshot bottom right, brand bottom left.
    y = c.accent_mark(m, m)
    end = c.headline(headline, m, y, (W - 2 * m, 2 * _line_height(c.hmax) + 4))
    fw = c.footer_width(round(W * 0.34))
    zone_x = min(max(m + fw + c.px(64), round(W * 0.28)), round(W * 0.38))
    zone_top = end + c.gap
    w, h = _width_shot(shot, W - m - zone_x)
    if zone_top + h <= H - m:
        c.shot(shot, W - m - w, H - m - h, w, h)
    elif H - zone_top >= 0.6 * h:  # most of it shows: let it run off the bottom edge
        c.shot(shot, W - m - w, zone_top, w, h, visible=H - zone_top)
    else:
        w, h = _fit_shot(shot, W - m - zone_x, H - m - zone_top)
        c.shot(shot, W - m - w, H - m - h, w, h)
    c.footer(zone_x - m - c.px(40))


def _portrait(c: _Card, shot: Image.Image, headline: str, tagline: str) -> None:
    W, H, m = c.W, c.H, c.m
    box_w = W - 2 * m
    y = c.accent_mark(m, m)
    # The tall card has room for a larger headline; long ones still shrink to hmin.
    hmax = round(c.hmax * 1.4)
    end = c.headline(headline, m, y, (box_w, 3 * _line_height(hmax) + 4), hmax=hmax)
    footer_top = H - m - c.footer_h
    if shot.height / shot.width >= TALL_RATIO:
        # Phone column on the right running off the bottom; tagline and brand on the left.
        w, h = _width_shot(shot, round(W * 0.46))
        x, top = W - m - w, end + c.gap
        c.shot(shot, x, top, w, h, visible=H - top if top + h > H - m else h)
        col_w = x - m - c.px(48)
        if tagline:
            c.tagline(tagline, m, top, (col_w, footer_top - c.gap - top), 5, big=28, small=20)
        c.footer(col_w)
        return
    tag_end = end
    if tagline:
        tag_end = c.tagline(tagline, m, end + c.px(24), (box_w, c.px(2 * 34)), 2, big=28, small=20)
    zone_top, zone_bottom = tag_end + c.gap, footer_top - c.gap
    w, h = _fit_shot(shot, box_w, zone_bottom - zone_top)
    c.shot(shot, (W - w) // 2, zone_top + (zone_bottom - zone_top - h) // 2, w, h)
    c.footer(box_w)


# ---------------------------------------------------------------- feed preview

FEED_W = 390
_SS = 3  # draw the preview at 3x and downsample, for smooth circles and text
_GREY_TEXT = (83, 100, 113)
_ICON = (139, 152, 165)
_LINE = (239, 243, 244)
_SKEL = (231, 233, 234)
_SKEL_SOFT = (241, 243, 244)
_LINK = (29, 155, 240)
_CAPTION_SIZE = 15
_CAPTION_LH = 20
_URL_RE = re.compile(r"^(https?://\S+|www\.\S+|[#@]\w+)", re.IGNORECASE)


def _handle(name: str) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "", name.lower())[:15]
    return "@" + (slug or "launch")


def _caption_lines(caption: str, font, width: int) -> list[list[tuple[str, bool]]]:
    """Wrap caption into lines of (word, is_link), keeping the founder's line breaks."""
    out: list[list[tuple[str, bool]]] = []
    for para in caption.replace("\r\n", "\n").split("\n"):
        words = para.split()
        if not words:
            out.append([])
            continue
        links = set()
        shown = []
        for w in words:
            if _URL_RE.match(w):
                w = re.sub(r"^https?://", "", w, flags=re.IGNORECASE)
                links.add(w)
            shown.append(w)
        for line in _wrap(shown, font, width):
            out.append([(w, w in links) for w in line.split(" ")])
    while out and not out[-1]:
        out.pop()
    return out


def _skeleton_post(draw: ImageDraw.ImageDraw, y: int, image: bool) -> int:
    S = _SS
    x0 = 68
    draw.ellipse((16 * S, (y + 12) * S, 56 * S, (y + 52) * S), fill=_SKEL)
    bars = [(x0, y + 17, 112, 10, _SKEL), (x0 + 120, y + 17, 64, 10, _SKEL_SOFT),
            (x0, y + 40, 300, 10, _SKEL_SOFT), (x0, y + 58, 284, 10, _SKEL_SOFT),
            (x0, y + 76, 176, 10, _SKEL_SOFT)]
    for bx, by, bw, bh, col in bars:
        draw.rounded_rectangle((bx * S, by * S, (bx + bw) * S, (by + bh) * S), bh * S // 2, fill=col)
    bottom = y + 86
    if image:
        draw.rounded_rectangle((x0 * S, (bottom + 12) * S, (FEED_W - 16) * S, (bottom + 12 + 150) * S),
                               16 * S, fill=_SKEL_SOFT)
        bottom += 12 + 150
    _actions(draw, x0, bottom + 12, _SKEL)
    return bottom + 12 + 18 + 12


def _actions(draw: ImageDraw.ImageDraw, x0: int, y: int, col: RGB) -> None:
    """Four faint action glyphs (reply, repost, like, views) and a share glyph, as shapes."""
    S = _SS
    lw = max(1, round(1.6 * S))
    span = FEED_W - 16 - x0
    xs = [x0 + i * (span - 18) / 4 for i in range(5)]
    cy = y + 9
    # reply: speech bubble
    x = xs[0]
    draw.rounded_rectangle(((x) * S, (cy - 7) * S, (x + 17) * S, (cy + 5) * S), 6 * S, outline=col, width=lw)
    draw.line(((x + 5) * S, (cy + 5) * S, (x + 3) * S, (cy + 9) * S), fill=col, width=lw)
    # repost: two offset arrows
    x = xs[1]
    draw.line(((x + 2) * S, (cy - 4) * S, (x + 14) * S, (cy - 4) * S), fill=col, width=lw)
    draw.line(((x + 11) * S, (cy - 7) * S, (x + 14) * S, (cy - 4) * S), fill=col, width=lw)
    draw.line(((x + 4) * S, (cy + 4) * S, (x + 16) * S, (cy + 4) * S), fill=col, width=lw)
    draw.line(((x + 4) * S, (cy + 4) * S, (x + 7) * S, (cy + 7) * S), fill=col, width=lw)
    # like: heart from two circles and a triangle outline
    x = xs[2]
    draw.arc(((x + 1) * S, (cy - 7) * S, (x + 10) * S, (cy + 2) * S), 135, 360, fill=col, width=lw)
    draw.arc(((x + 8) * S, (cy - 7) * S, (x + 17) * S, (cy + 2) * S), 180, 45, fill=col, width=lw)
    draw.line(((x + 2.3) * S, (cy + 0.5) * S, (x + 9) * S, (cy + 8) * S), fill=col, width=lw)
    draw.line(((x + 15.7) * S, (cy + 0.5) * S, (x + 9) * S, (cy + 8) * S), fill=col, width=lw)
    # views: three bars
    x = xs[3]
    for i, hgt in enumerate((7, 13, 10)):
        bx = x + 3 + i * 5
        draw.line((bx * S, (cy + 7) * S, bx * S, (cy + 7 - hgt) * S), fill=col, width=lw)
    # share: tray with an up arrow
    x = xs[4]
    draw.line(((x + 2) * S, (cy + 1) * S, (x + 2) * S, (cy + 7) * S), fill=col, width=lw)
    draw.line(((x + 2) * S, (cy + 7) * S, (x + 16) * S, (cy + 7) * S), fill=col, width=lw)
    draw.line(((x + 16) * S, (cy + 7) * S, (x + 16) * S, (cy + 1) * S), fill=col, width=lw)
    draw.line(((x + 9) * S, (cy + 3) * S, (x + 9) * S, (cy - 8) * S), fill=col, width=lw)
    draw.line(((x + 5) * S, (cy - 4) * S, (x + 9) * S, (cy - 8) * S, (x + 13) * S, (cy - 4) * S),
              fill=col, width=lw, joint="curve")


def render_feed_preview(brand: BrandKit, caption: str, card: Path, out: Path, scale: int = 1) -> Path:
    """The post as it would sit in a light, phone-width feed. 390 px wide, height as needed;
    scale 2 or 3 gives a sharper image for high-density screens, same layout."""
    S = _SS
    font_path = str(FONT_PATH)
    x0, text_w = 68, FEED_W - 68 - 16
    cap_font = load_font(font_path, _CAPTION_SIZE * S, 400)
    name_font = load_font(font_path, _CAPTION_SIZE * S, 700)
    meta_font = load_font(font_path, _CAPTION_SIZE * S, 400)
    lines = _caption_lines(_drop_missing(caption or ""), cap_font, text_w * S)

    raw = _open_image(card)
    card_im = _flatten(raw, (255, 255, 255)) if raw is not None else None
    card_h = round(text_w * card_im.height / card_im.width) if card_im is not None else 0
    card_h = min(card_h, round(text_w * 1.25))

    top_h = 12 + 86 + 12 + 18 + 12
    bot_h = top_h + 12 + 150
    post_h = 12 + 20 + len(lines) * _CAPTION_LH + (12 + card_h if card_h else 0) + 12 + 18 + 14
    total = top_h + 1 + post_h + 1 + bot_h

    im = Image.new("RGB", (FEED_W * S, total * S), (255, 255, 255))
    draw = ImageDraw.Draw(im)
    y = _skeleton_post(draw, 0, image=False)
    draw.rectangle((0, y * S, FEED_W * S, (y + 1) * S - 1), fill=_LINE)
    y += 1

    accent = _rgb(brand.accent, (59, 91, 219))
    logo_raw = _open_image(brand.logo_path)
    avatar = _mark_tile(brand, logo_raw.convert("RGBA") if logo_raw is not None else None,
                        40 * S, accent, circle=True)
    im.paste(avatar, (16 * S, (y + 12) * S), avatar)
    draw.ellipse((16 * S, (y + 12) * S, 56 * S - 1, (y + 52) * S - 1), outline=(230, 233, 235), width=S)

    name = _display_name(brand)
    base = (y + 12 + 15) * S
    name_w = text_w * S - meta_font.getlength(" " + _handle(name) + " · now")
    name_shown = name if name_font.getlength(name) <= max(name_w, 60 * S) else _ellipsize(
        name, name_font, max(round(name_w), 60 * S))
    draw.text((x0 * S, base), name_shown, font=name_font, fill=(15, 20, 25), anchor="ls")
    mx = x0 * S + name_font.getlength(name_shown) + 4 * S
    meta = _handle(name) + " · now"
    if mx + meta_font.getlength(meta) > (FEED_W - 16) * S:
        meta = _ellipsize(meta, meta_font, round((FEED_W - 16) * S - mx))
    draw.text((mx, base), meta, font=meta_font, fill=_GREY_TEXT, anchor="ls")

    ty = y + 12 + 20
    for i, words in enumerate(lines):
        bx = x0 * S
        bl = (ty + i * _CAPTION_LH + 15) * S
        cursor = ""
        for word, link in words:
            wx = bx + cap_font.getlength(cursor)
            draw.text((wx, bl), word, font=cap_font, fill=_LINK if link else (15, 20, 25), anchor="ls")
            cursor += word + " "
    cy = ty + len(lines) * _CAPTION_LH
    if card_im is not None:
        cy += 12
        k = text_w * S / card_im.width
        scaled = _scale(card_im, text_w * S, round(card_im.height * k)).crop((0, 0, text_w * S, card_h * S))
        mask = _mask((text_w * S, card_h * S), 16 * S, ss=1)
        im.paste(scaled, (x0 * S, cy * S), mask)
        draw.rounded_rectangle((x0 * S, cy * S, (x0 + text_w) * S - 1, (cy + card_h) * S - 1), 16 * S,
                               outline=(207, 217, 222), width=S)
        cy += card_h
    _actions(draw, x0, cy + 12, _ICON)
    y += post_h
    draw.rectangle((0, y * S, FEED_W * S, (y + 1) * S - 1), fill=_LINE)
    y += 1
    _skeleton_post(draw, y, image=True)

    k = min(max(int(scale), 1), S)
    final = im if k == S else im.resize((FEED_W * k, total * k), Image.Resampling.LANCZOS)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    final.save(out, "PNG", optimize=True)
    return out


# Public names for the helpers the film reuses (launch_desk/film.py).
drop_missing, open_image, rgb, mix, mark_tile = _drop_missing, _open_image, _rgb, _mix, _mark_tile
