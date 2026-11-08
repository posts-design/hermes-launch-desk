"""Render the catalog screenshots (and, with --banner, the banner) in assets/ with the plugin's own code.

Usage: python tools/render_assets.py INPUT_DIR [OUT_DIR] [--banner]

INPUT_DIR holds files fetched once by hand (not committed):
  pawr-og.png     pawr.link's og:image, used as the card's screenshot
  pawr-page.png   a pawr.link profile page (a phone-shaped crop of pawr.link's own
                  landing-screenshot-max.webp), the split card's second screenshot
  pawr-logo.png   the logo fetch_brand() saved for https://pawr.link
  posts-logo.png  the logo fetch_brand() saved for https://posts.design
  cli.ansi        `tmux capture-pane -p -e` of a live `hermes chat` run of /launch
OUT_DIR defaults to the repo's assets/. The three cards (hero, statement, split) and the feed
preview carry the post from a live /launch run on 2026-11-06, the run cli.ansi captures. The
banner keeps the card and post of the 2026-11-05 run. The brand colours are what fetch_brand()
read from each site on 2026-11-05.
"""
from __future__ import annotations

import re
import sys
import unicodedata
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from launch_desk.draft import card_line, facts_text, unsupported_details  # noqa: E402
from launch_desk.models import BrandKit, Brief  # noqa: E402
from launch_desk.render import FONT_PATH, choose_layout, load_font, render_card, render_feed_preview  # noqa: E402
from launch_desk.rules import check, x_length  # noqa: E402

X = (1200, 675)
INTER = str(FONT_PATH)

# ------------------------------------------------------------------ the live runs

PAWR_BRIEF = Brief("feature", "x", "custom themes for pet profile pages", "https://pawr.link",
                   detail="people kept asking to match their pet page to their own site")
# The 2026-11-06 run (cli.ansi): the cards and the feed preview.
RUN_HOOK = "Your pet page should match your own site."
RUN_CAPTION = (
    "Your pet page should match your own site.\n\n"
    "Custom themes are live for pet profile pages on pawr.link.\n\n"
    "One page for everything you are, do, and care about.\n\n"
    "https://pawr.link"
)
# The 2026-11-05 run: the banner's card and its scroll test.
PAWR_HOOK = "Check this out: pet pages can now use custom themes."
PAWR_CAPTION = (
    "Check this out: pet pages can now use custom themes.\n\n"
    "People kept asking to match their pet page to their own site. So we built themes.\n\n"
    "One change, but it means your pet's page can sit next to your work without looking out of place.\n\n"
    "https://pawr.link"
)


def pawr_kit(inp: Path) -> BrandKit:
    return BrandKit(name="pawr.link", url="https://pawr.link",
                    tagline="one pawrful page for everything you are, do, and care about.",
                    bg="#FAFAFA", ink="#111111", accent="#6A3CFF", font_family="Inter",
                    logo_path=str(inp / "pawr-logo.png"))


def posts_kit(inp: Path) -> BrandKit:
    return BrandKit(name="posts.design", url="https://posts.design",
                    tagline="Social media post design inspiration: 4,400+ real posts from 1,060+ brands",
                    bg="#F5F5F5", ink="#111111", accent="#FF9602", font_family="Inter",
                    logo_path=str(inp / "posts-logo.png"))


# ------------------------------------------------------------------ helpers

def rounded(size: tuple[int, int], r: int, ss: int = 4) -> Image.Image:
    w, h = size
    big = Image.new("L", (w * ss, h * ss), 0)
    ImageDraw.Draw(big).rounded_rectangle((0, 0, w * ss - 1, h * ss - 1), r * ss, fill=255)
    return big.resize(size, Image.Resampling.LANCZOS)


def shadow(canvas: Image.Image, mask: Image.Image, xy: tuple[int, int], layers) -> None:
    """Soft drop shadow under a shape: layers of (dy, blur, alpha)."""
    W, H = canvas.size
    for dy, blur, alpha in layers:
        a = Image.new("L", (W, H), 0)
        a.paste(mask.point(lambda v, k=alpha: v * k // 255), (xy[0], xy[1] + dy))
        a = a.filter(ImageFilter.GaussianBlur(blur))
        tint = Image.new("RGBA", (W, H), (0, 0, 0, 255))
        tint.putalpha(a)
        canvas.alpha_composite(tint)


def paste_round(canvas: Image.Image, im: Image.Image, xy: tuple[int, int], r: int,
                ring: tuple[int, int, int, int] | None = None, shade=None) -> None:
    mask = rounded(im.size, r)
    if shade:
        shadow(canvas, mask, xy, shade)
    canvas.paste(im.convert("RGB"), xy, mask)
    if ring:
        ss = 4
        big = Image.new("L", (im.width * ss, im.height * ss), 0)
        ImageDraw.Draw(big).rounded_rectangle((0, 0, im.width * ss - 1, im.height * ss - 1), r * ss,
                                              outline=255, width=ss)
        line = big.resize(im.size, Image.Resampling.LANCZOS)
        layer = Image.new("RGBA", im.size, ring[:3] + (255,))
        layer.putalpha(line.point(lambda v: v * ring[3] // 255))
        canvas.alpha_composite(layer, xy)


def tick(canvas: Image.Image, cx: float, cy: float, r: float, fill, mark) -> None:
    """A check mark in a filled circle, drawn at 4x and scaled down for smooth edges."""
    ss, size = 4, int(2 * r + 2)
    big = Image.new("RGBA", (size * ss, size * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    c, R = size * ss / 2, r * ss
    d.ellipse((c - R, c - R, c + R, c + R), fill=fill)
    d.line([(c - R * 0.45, c + R * 0.02), (c - R * 0.12, c + R * 0.34), (c + R * 0.48, c - R * 0.32)],
           fill=mark, width=max(2, round(R * 0.26)), joint="curve")
    small = big.resize((size, size), Image.Resampling.LANCZOS)
    canvas.alpha_composite(small, (round(cx - size / 2), round(cy - size / 2)))


def wrap(text: str, font, width: float) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        cand = f"{cur} {word}".strip()
        if cur and font.getlength(cand) > width:
            lines.append(cur)
            cur = word
        else:
            cur = cand
    return lines + ([cur] if cur else [])


def save(im: Image.Image, path: Path, colors: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    im = im.convert("RGB")
    if colors:
        im = im.quantize(colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    im.save(path, "PNG", optimize=True)
    return path


# ------------------------------------------------------------------ scroll test (real values)

def scroll_test(kit: BrandKit, hook: str = PAWR_HOOK, caption: str = PAWR_CAPTION) -> list[str]:
    """The checks the plugin runs on the post, as banner rows. Fails loudly if the post fails."""
    failures = check(caption, network="x") + unsupported_details(caption, facts_text(PAWR_BRIEF, kit))
    assert not failures, [f.rule_id for f in failures]
    words = len(hook.split())
    return [f"First line: {words} words", "No stock launch phrases", "No details you didn’t give",
            f"{x_length(caption)} of 280 characters"]


# ------------------------------------------------------------------ feed preview frame

FEED_LINE = (239, 243, 244)


def frame_preview(path: Path) -> Path:
    """Keep the post and a little of the feed around it, fade the end, and set it on a soft
    background so the white page reads as a phone screen."""
    im = Image.open(path).convert("RGB")
    w, h = im.size
    seps = [y for y in range(h) if im.getpixel((0, y)) == FEED_LINE and im.getpixel((w - 1, y)) == FEED_LINE
            and im.getpixel((w // 2, y)) == FEED_LINE]
    runs = [y for i, y in enumerate(seps) if i == 0 or y != seps[i - 1] + 1]
    bottom = min(h, runs[1] + 230) if len(runs) > 1 else h
    im = im.crop((0, 0, w, bottom))
    fade = 160
    white = Image.new("RGB", im.size, (255, 255, 255))
    ramp = Image.new("L", im.size, 0)
    rd = ImageDraw.Draw(ramp)
    for i in range(fade):
        rd.line([(0, bottom - fade + i), (w, bottom - fade + i)], fill=round(255 * (i / fade) ** 1.6))
    im = Image.composite(white, im, ramp)

    m = 56
    canvas = Image.new("RGBA", (w + 2 * m, im.height + 2 * m), (236, 238, 242, 255))
    paste_round(canvas, im, (m, m), 36, ring=(0, 0, 0, 22), shade=((16, 30, 60), (2, 4, 30)))
    return save(canvas, path)


# ------------------------------------------------------------------ banner

BG = (14, 15, 18)
ORANGE = (255, 150, 2)


def banner(card: Path, kit: BrandKit, posts: BrandKit, out: Path) -> Path:
    W, H = 1200, 600
    im = Image.new("RGBA", (W, H), BG + (255,))

    # a faint light behind the card, for depth
    glow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(glow).ellipse((560, 20, 1220, 600), fill=255)
    glow = glow.filter(ImageFilter.GaussianBlur(140)).point(lambda v: v * 34 // 255)
    lift = Image.new("RGBA", (W, H), (120, 128, 150, 255))
    lift.putalpha(glow)
    im.alpha_composite(lift)

    # the card the plugin composed, and the scroll test it passed
    cw = 576
    c = Image.open(card).convert("RGB").resize((cw, round(cw * X[1] / X[0])), Image.Resampling.LANCZOS)
    cx, cy = W - 64 - cw, 92
    paste_round(im, c, (cx, cy), 14, ring=(255, 255, 255, 30), shade=((24, 40, 160), (4, 8, 90)))

    rows = scroll_test(kit)
    pw, row_h = 300, 33
    ph = 62 + row_h * len(rows) + 12
    px, py = W - 36 - pw, cy + c.height - 84
    panel = Image.new("RGB", (pw, ph), (25, 27, 32))
    paste_round(im, panel, (px, py), 16, ring=(255, 255, 255, 36), shade=((18, 28, 170), (3, 6, 110)))
    d = ImageDraw.Draw(im)
    head = load_font(INTER, 19, 700)
    d.text((px + 22, py + 37), "Scroll test", font=head, fill=(245, 245, 245), anchor="ls")
    chip = load_font(INTER, 14, 650)
    green = (52, 199, 120)
    lw = chip.getlength("passed")
    d.rounded_rectangle((px + pw - 20 - lw - 22, py + 20, px + pw - 20, py + 44), 12, fill=(26, 64, 44))
    d.text((px + pw - 20 - lw / 2 - 11, py + 32), "passed", font=chip, fill=green, anchor="mm")
    rf = load_font(INTER, 16, 450)
    ry = py + 60
    for row in rows:
        tick(im, px + 31, ry + row_h / 2, 9, green + (255,), (25, 27, 32, 255))
        d.text((px + 50, ry + row_h / 2), row, font=rf, fill=(206, 209, 215), anchor="lm")
        ry += row_h

    # the words
    x = 64
    eyebrow = load_font(INTER, 16, 650)
    d.text((x, 186), "FOR HERMES AGENT", font=eyebrow, fill=ORANGE, anchor="ls")
    title = load_font(INTER, 68, 800)
    d.text((x - 3, 258), "Launch Desk", font=title, fill=(250, 250, 250), anchor="ls")
    body = load_font(INTER, 25, 420)
    y = 312
    for line in wrap("Launch posts from real launches, checked in code, in your brand.", body, 400):
        d.text((x, y), line, font=body, fill=(168, 173, 182), anchor="ls")
        y += 36

    # by posts.design
    tile = 40
    logo = Image.open(posts.logo_path).convert("RGBA")
    plate = Image.new("RGBA", (tile, tile), (255, 255, 255, 255))
    lg = logo.copy()
    lg.thumbnail((tile - 10, tile - 10), Image.Resampling.LANCZOS)
    plate.alpha_composite(lg, ((tile - lg.width) // 2, (tile - lg.height) // 2))
    ty = H - 60 - tile
    paste_round(im, plate, (x, ty), 10)
    d = ImageDraw.Draw(im)
    by = load_font(INTER, 19, 450)
    name = load_font(INTER, 19, 700)
    d.text((x + tile + 13, ty + tile / 2), "by", font=by, fill=(150, 155, 164), anchor="lm")
    d.text((x + tile + 13 + by.getlength("by "), ty + tile / 2), "posts.design", font=name,
           fill=(240, 240, 240), anchor="lm")
    return save(im, out)


# ------------------------------------------------------------------ terminal screenshot

MONO_DIR = Path("/usr/share/fonts/truetype/dejavu")
TERM_BG = (15, 17, 21)
TERM_FG = (214, 216, 220)
_SGR = re.compile(r"\x1b\[([0-9;]*)m")
_OTHER_ESC = re.compile(r"\x1b(?:\[[0-9;?]*[A-Za-ln-z]|\][^\x07]*\x07|[()][A-Z0-9])")


def _xterm(n: int) -> tuple[int, int, int]:
    base = [(0, 0, 0), (205, 49, 49), (13, 188, 121), (229, 229, 16), (36, 114, 200), (188, 63, 188),
            (17, 168, 205), (229, 229, 229), (102, 102, 102), (241, 76, 76), (35, 209, 139),
            (245, 245, 67), (59, 142, 234), (214, 112, 214), (41, 184, 219), (255, 255, 255)]
    if n < 16:
        return base[n]
    if n < 232:
        n -= 16
        steps = (0, 95, 135, 175, 215, 255)
        return steps[n // 36], steps[(n // 6) % 6], steps[n % 6]
    v = 8 + (n - 232) * 10
    return v, v, v


@lru_cache(maxsize=None)
def _mono(style: str, size: int) -> ImageFont.FreeTypeFont:
    names = {"": "DejaVuSansMono.ttf", "b": "DejaVuSansMono-Bold.ttf", "i": "DejaVuSansMono-Oblique.ttf",
             "bi": "DejaVuSansMono-BoldOblique.ttf"}
    path = MONO_DIR / names[style]
    return ImageFont.truetype(str(path), size) if path.exists() else load_font(INTER, size, 700 if "b" in style else 400)


@lru_cache(maxsize=None)
def _fallback(size: int) -> ImageFont.FreeTypeFont:
    path = MONO_DIR / "DejaVuSans.ttf"
    return ImageFont.truetype(str(path), size) if path.exists() else load_font(INTER, size, 400)


@lru_cache(maxsize=None)
def _bitmap(font: ImageFont.FreeTypeFont, ch: str) -> bytes:
    im = Image.new("L", (48, 48), 0)
    ImageDraw.Draw(im).text((8, 8), ch, font=font, fill=255)
    return im.tobytes()


def _font_for(ch: str, style: str, size: int):
    """The mono font, else DejaVu Sans, else None when neither can draw the character."""
    for font in (_mono(style, size), _fallback(size)):
        if ord(ch) < 0x2000 or _bitmap(font, ch) != _bitmap(font, "\U000F0000"):
            return font
    return None


def _width(ch: str) -> int:
    if unicodedata.combining(ch) or 0xFE00 <= ord(ch) <= 0xFE0F or ch == "\u200d":
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def parse_ansi(text: str) -> list[list[tuple[str, tuple, str]]]:
    """Lines of (char, colour, style) cells; a wide character takes two cells."""
    rows = []
    fg, bold, dim, italic = None, False, False, False
    for raw in text.splitlines():
        raw = _OTHER_ESC.sub("", raw)
        cells: list[tuple[str, tuple, str]] = []
        pos = 0
        for m in list(_SGR.finditer(raw)) + [None]:
            chunk = raw[pos: m.start() if m else len(raw)]
            for ch in chunk:
                w = _width(ch)
                if w == 0:
                    continue
                col = fg or TERM_FG
                if dim:
                    col = tuple(round(a + (b - a) * 0.45) for a, b in zip(col, TERM_BG))
                style = ("b" if bold else "") + ("i" if italic else "")
                cells.append((ch, col, style))
                if w == 2:
                    cells.append(("", col, style))
            if m is None:
                break
            pos = m.end()
            params = [int(p) if p else 0 for p in m.group(1).split(";")] if m.group(1) else [0]
            i = 0
            while i < len(params):
                p = params[i]
                if p == 0:
                    fg, bold, dim, italic = None, False, False, False
                elif p == 1:
                    bold = True
                elif p == 2:
                    dim = True
                elif p == 3:
                    italic = True
                elif p == 22:
                    bold = dim = False
                elif p == 23:
                    italic = False
                elif p == 39:
                    fg = None
                elif 30 <= p <= 37:
                    fg = _xterm(p - 30)
                elif 90 <= p <= 97:
                    fg = _xterm(p - 90 + 8)
                elif p == 38 and i + 1 < len(params):
                    if params[i + 1] == 5 and i + 2 < len(params):
                        fg = _xterm(params[i + 2])
                        i += 2
                    elif params[i + 1] == 2 and i + 4 < len(params):
                        fg = tuple(params[i + 2:i + 5])
                        i += 4
                i += 1
        rows.append(cells)
    return rows


def _drop_undrawable(cells, size: int, box_edge: int | None = None):
    """Take out wide characters no bundled font can draw (emoji) with the space before them,
    and pad before the line's last box border so the border stays where it was."""
    out = list(cells)
    i = 0
    removed = 0
    while i < len(out):
        ch, col, style = out[i]
        if ch and _width(ch) == 2 and _font_for(ch, style, size) is None:
            start = i - 1 if i > 0 and out[i - 1][0] == " " else i
            n = (i + 2) - start
            del out[start:i + 2]
            removed += n
            i = start
            continue
        i += 1
    if removed:
        last = max((k for k, c in enumerate(out) if c[0] == "│"), default=None)
        if last is not None:
            out[last:last] = [(" ", TERM_FG, "")] * removed
            # the terminal drew the emoji one cell wider than the box allowed for: put the
            # border back on the box edge when only spaces stand in the way
            last += removed
            while box_edge is not None and last > box_edge and out[last - 1][0] == " ":
                del out[last - 1]
                last -= 1
    return out


BOX = "─│╭╮╰╯"


def box_char(d: ImageDraw.ImageDraw, ch: str, x: float, y: float, cw: float, lh: float, col) -> None:
    cx, cy = x + cw / 2, y + lh / 2
    r = cw / 2
    if ch == "─":
        d.line([(x, cy), (x + cw, cy)], fill=col, width=1)
    elif ch == "│":
        d.line([(cx, y), (cx, y + lh)], fill=col, width=1)
    else:
        right = ch in "╭╰"  # the horizontal arm goes right
        down = ch in "╭╮"   # the vertical arm goes down
        hx = (cx + r, x + cw) if right else (x, cx - r)
        d.line([(hx[0], cy), (hx[1], cy)], fill=col, width=1)
        vy = (cy + r, y + lh) if down else (y, cy - r)
        d.line([(cx, vy[0]), (cx, vy[1])], fill=col, width=1)
        bx = cx if right else cx - 2 * r
        by = cy if down else cy - 2 * r
        start = {"╭": 180, "╮": 270, "╯": 0, "╰": 90}[ch]
        d.arc((bx, by, bx + 2 * r, by + 2 * r), start, start + 90, fill=col, width=1)


def _cell_index(cells, ch: str) -> int | None:
    return next((k for k, c in enumerate(cells) if c[0] == ch), None)


def _break_at(text, room: int) -> int:
    """After the last '-' or '/' that fits, in the second half of the row, unless that leaves
    a scrap of under 8 characters for the next row (a date's day, say)."""
    spots = [k + 1 for k in range(room) if text[k][0] in "-/" and k + 1 > room // 2]
    good = [k for k in spots if len(text) - k >= 8]
    return (good or spots or [room])[-1]


def _wrap_overruns(rows):
    """Hermes' question box keeps a fixed width, so a long link pushes that row's right
    border past the box edge. Re-wrap such rows inside the box, breaking the long text
    after a '-' or '/' where it can, so every border sits on the box edge."""
    out, left, edge = [], None, None
    for cells in rows:
        top = _cell_index(cells, "╭")
        if top is not None:
            left, edge = top, _cell_index(cells, "╮")
        elif _cell_index(cells, "╰") is not None:
            left = edge = None
        last = max((k for k, c in enumerate(cells) if c[0] == "│"), default=None)
        if edge is None or last is None or last <= edge or not cells or cells[left][0] != "│":
            out.append(cells)
            continue
        body = cells[left + 1:last]
        while body and body[-1][0] in (" ", ""):
            body.pop()
        indent = 0
        while indent < len(body) and body[indent][0] == " ":
            indent += 1
        text, room = body[indent:], edge - 1 - (left + 1 + indent)
        border, blank = cells[left], (" ", TERM_FG, "")
        while text:
            cut = len(text) if len(text) <= room else _break_at(text, room)
            piece, text = text[:cut], text[cut:]
            line = [border] + [blank] * indent + piece
            line += [blank] * (edge - len(line)) + [cells[last]]
            out.append(line)
    return out


def terminal(ansi: Path, out: Path, start: str, end: str, size: int = 17) -> Path:
    rows = parse_ansi(ansi.read_text(encoding="utf-8"))
    plain = ["".join(c[0] for c in r) for r in rows]
    a = next(i for i, t in enumerate(plain) if start in t)
    b = next(i for i, t in enumerate(plain) if i > a and end in t)
    rows = rows[a:b + 1]
    edge = None
    for i, r in enumerate(rows):
        line = "".join(c[0] for c in r)
        if "╮" in line:
            edge = line.index("╮")
        rows[i] = _drop_undrawable(r, size, edge)
    rows = _wrap_overruns(rows)
    while rows and not "".join(c[0] for c in rows[-1]).strip():
        rows.pop()
    for r in rows:
        while r and r[-1][0] in (" ", ""):
            r.pop()

    cw = _mono("", size).getlength("M")
    lh = round(size * 1.5)
    cols = max(len(r) for r in rows)
    pad, bar = 30, 44
    win_w = round(cols * cw) + 2 * pad
    win_h = bar + 18 + lh * len(rows) + pad
    margin = 36
    W, H = win_w + 2 * margin, win_h + 2 * margin
    im = Image.new("RGBA", (W, H), (226, 228, 232, 255))
    win = Image.new("RGB", (win_w, win_h), TERM_BG)
    wd = ImageDraw.Draw(win)
    wd.rectangle((0, 0, win_w, bar), fill=(28, 31, 37))
    for k, col in enumerate(((255, 95, 87), (254, 188, 46), (40, 200, 64))):
        cx = 22 + k * 22
        wd.ellipse((cx - 6, bar / 2 - 6, cx + 6, bar / 2 + 6), fill=col)
    tf = load_font(INTER, 14, 500)
    wd.text((win_w / 2, bar / 2), "hermes chat", font=tf, fill=(150, 155, 164), anchor="mm")

    y = bar + 18
    for r in rows:
        for k, (ch, col, style) in enumerate(r):
            if not ch or ch == " ":
                continue
            font = _font_for(ch, style, size)
            if font is None:
                continue
            x = pad + k * cw
            if ch in BOX:  # drawn as lines, so borders join across the taller rows
                box_char(wd, ch, x, y, cw, lh, col)
            else:
                wd.text((x, y + lh / 2), ch, font=font, fill=col, anchor="lm")
        y += lh
    paste_round(im, win, (margin, margin), 12, ring=(0, 0, 0, 40), shade=((10, 18, 70), (2, 3, 40)))
    return save(im, out)


# ------------------------------------------------------------------ main

def main(argv: list[str]) -> int:
    with_banner = "--banner" in argv
    argv = [a for a in argv if a != "--banner"]
    if len(argv) < 2:
        print(__doc__)
        return 2
    inp = Path(argv[1])
    out = Path(argv[2]) if len(argv) > 2 else ROOT / "assets"
    shots = out / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    pawr, posts = pawr_kit(inp), posts_kit(inp)

    scroll_test(pawr, RUN_HOOK, RUN_CAPTION)  # the post on the cards passes the plugin's checks
    headline = card_line("", RUN_CAPTION, PAWR_BRIEF, pawr)  # the card line /launch draws
    og, page = inp / "pawr-og.png", inp / "pawr-page.png"
    # Each layout as the wizard picks it: one screenshot, none, two.
    for name, used in (("card-x.png", [og]), ("card-statement.png", []), ("card-split.png", [og, page])):
        layout = choose_layout(used, headline)
        render_card(pawr, used, headline, X, shots / name, layout=layout)
    preview = render_feed_preview(pawr, RUN_CAPTION, shots / "card-x.png", shots / "feed-preview.png", scale=2)
    frame_preview(preview)
    if with_banner:
        card = render_card(pawr, og, card_line("", PAWR_CAPTION, PAWR_BRIEF, pawr), X, out / "_banner-card.png")
        banner(card, pawr, posts, out / "banner.png")
        card.unlink()
    if (inp / "cli.ansi").exists():
        terminal(inp / "cli.ansi", shots / "cli-flow.png", start="/launch https", end="to select")
    for p in sorted(out.rglob("*.png")):
        with Image.open(p) as im:
            print(f"{p.relative_to(out)}  {im.size[0]}x{im.size[1]}  {p.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
