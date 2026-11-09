"""/launch film [pack]: a 14-second silent launch film made from an approved pack.

The film is a HyperFrames project (HTML rendered to MP4), written in code from the pack:
<pack>/film/index.html with its assets, and <pack>/film-beats.md, the shot list. Motion is
CSS @keyframes only, timed in absolute seconds, with no script and nothing fetched: every
file it uses is inside film/. Text is sized here with Pillow against the same font file the
browser gets (render.fit_text), with some slack for browser metrics.

Rendering runs only what the user set up, first found wins: the plugin setting film_render
(a command with {project} and {out}), else a `hyperframes` executable on PATH. Nothing is
downloaded (never npx), and HyperFrames' telemetry and update checks are off for the run.
Without a renderer the reply says how to render it, or film it by hand from the shot list.

Recommendations for a longer film come from posts.design's playbooks: list_playbooks (free),
and get_playbook (1 credit) only when the account has it. Nothing is ever posted.
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import shutil
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

from . import followup
from .host import Host
from .models import BrandKit
from .render import (FONT_DIR, FONT_PATH, HEAD_WEIGHT, LEADING, _drop_missing, _mark_tile,
                     _mix, _open_image, _rgb, fit_text, head_font_path)

logger = logging.getLogger(__name__)

WIDTH, HEIGHT, FPS, SECONDS = 1920, 1080, 30, 14
# (id, start, end) in seconds; together they cover 0..SECONDS with no gap.
BEATS: tuple[tuple[str, float, float], ...] = (
    ("line", 0.0, 3.5),
    ("product", 3.5, 8.5),
    ("card", 8.5, 11.5),
    ("end", 11.5, 14.0),
)
PROJECT = "film"
MP4 = "film.mp4"
BEAT_SHEET = "film-beats.md"
CONFIG_KEY = "film_render"
RENDER_TIMEOUT_S = 600
OUTPUT_TAIL_LINES = 5
# For the render: no telemetry, no update check, no background install.
RENDER_ENV = {"HYPERFRAMES_NO_TELEMETRY": "1", "DO_NOT_TRACK": "1",
              "HYPERFRAMES_NO_UPDATE_CHECK": "1", "HYPERFRAMES_NO_AUTO_INSTALL": "1"}
SLACK = 0.92  # text is fitted to 92% of its box, for browser metrics
MARGIN_X = 160
SHOT_BOX = (WIDTH - 2 * MARGIN_X, HEIGHT - 2 * 110)
SHOT_MAX_SIDE = 2560
SHOT_UPSCALE = 2.0
SHOT_RADIUS = 28
ZOOM = 1.06
MARK_SIZE = 176
HEAD_FAMILY, TEXT_FAMILY = "LD Head", "LD Text"
OFL = {"Inter.ttf": "OFL.txt", "Newsreader.ttf": "Newsreader-OFL.txt", "Nunito.ttf": "Nunito-OFL.txt"}
# The posts.design playbook for each kind of launch; the first listed when it is missing.
PLAYBOOK_FOR = {"feature": "screen-led-short", "product": "founder-led-demo",
                "rebrand": "polished-launch", "partnership": "ship-week-update"}
PLAYBOOK_BEATS_SHOWN = 5

UNATTENDED = "/launch film makes a film only when you ask in a chat, so nothing was rendered."
NO_PACK = "There is no launch pack yet. Approve one with /launch first, then send /launch film."
RENDER_FAILED = "The render did not finish, so there is no MP4 this time."


# ------------------------------------------------------------------ what goes in the film

@dataclass
class Text:
    lines: list[str]
    size: int


@dataclass
class Plan:
    """Everything the film shows, read from one pack."""
    name: str
    host: str
    line1: str
    card_line: str
    what: str
    bg: str
    ink: str
    accent: str
    head_font: Path
    shot: Path | None = None
    logo: Path | None = None
    shipped: str = "feature"
    fitted: dict[str, Text] = field(default_factory=dict)


def _clean(text: Any) -> str:
    return " ".join(str(text or "").split())


def _host_of(url: Any) -> str:
    try:
        host = (urllib.parse.urlsplit(str(url or "").strip()).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _hex(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    return text.upper() if re.fullmatch(r"#[0-9A-Fa-f]{6}", text) else fallback


def _pack_file(pack: Path, name: Any) -> Path | None:
    """pack/name when name is a bare file name of a regular file in the pack (not a link)."""
    if not isinstance(name, str) or not name or "/" in name or "\\" in name or name in (".", ".."):
        return None
    path = pack / name
    return path if path.is_file() and not path.is_symlink() else None


def _post_text(pack: Path, data: dict) -> str:
    files = data.get("files") if isinstance(data.get("files"), dict) else {}
    path = _pack_file(pack, files.get("post") or "post-x.txt")
    if path is not None:
        try:
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return text
        except (OSError, UnicodeDecodeError):
            pass
    return str(data.get("caption") or "").strip()


def _first_line(text: str) -> str:
    return next((_clean(line) for line in text.splitlines() if line.strip()), "")


def _same_site(a: Any, b: Any) -> bool:
    return bool(_host_of(a)) and _host_of(a) == _host_of(b)


def _shot_in(pack: Path, data: dict) -> Path | None:
    """The pack's screenshot for beat 2: of those the card used, the widest one that opens."""
    files = data.get("files") if isinstance(data.get("files"), dict) else {}
    best, ratio = None, -1.0
    for name in files.get("screenshots") or []:
        path = _pack_file(pack, name)
        im = _open_image(path) if path else None
        if im is not None and im.width / max(1, im.height) > ratio:
            best, ratio = path, im.width / max(1, im.height)
    return best


def plan_for(pack: Path, data: dict, stored_brand: Any = None) -> Plan:
    """What the film shows, from the pack. The face and the logo come from the pack; a pack
    written before they were kept takes them from the stored brand when it is the same site."""
    brand = data.get("brand") if isinstance(data.get("brand"), dict) else {}
    brief = data.get("brief") if isinstance(data.get("brief"), dict) else {}
    files = data.get("files") if isinstance(data.get("files"), dict) else {}
    stored = stored_brand if isinstance(stored_brand, dict) and _same_site(stored_brand.get("url"), brand.get("url")) else {}
    face = str(brand.get("font_face") or stored.get("font_face") or "")
    family = brand.get("font_family") or stored.get("font_family")
    kit = BrandKit(name=_clean(brand.get("name")), url=str(brand.get("url") or ""), tagline="",
                   bg=_hex(brand.get("bg"), "#FAFAFA"), ink=_hex(brand.get("ink"), "#111111"),
                   accent=_hex(brand.get("accent"), "#3B5BDB"), font_family=family, logo_path=None,
                   font_face=face)
    logo = _pack_file(pack, files.get("logo"))
    if logo is None and stored.get("logo_path") and Path(str(stored["logo_path"])).is_file():
        logo = Path(str(stored["logo_path"]))
    site = _host_of(brand.get("url")) or _host_of(brief.get("product_url"))
    what = _clean(brief.get("what"))
    head = head_font_path(kit)
    name = _drop_missing(kit.name, str(head)) or site or "Launch"
    line1 = _drop_missing(_first_line(_post_text(pack, data)), str(head)) or name
    card = _drop_missing(_clean(data.get("card_line")), str(head))
    if not card:  # a pack written before card lines: what shipped, as a sentence
        card = _drop_missing(what, str(head))
        card = card[:1].upper() + card[1:] if card else name
    if site and site.lower() == name.lower():
        site = ""  # the end card shows it once
    shipped = str(brief.get("shipped") or "feature")
    return Plan(name=name, host=site, line1=line1, card_line=card, what=what, bg=kit.bg, ink=kit.ink,
                accent=kit.accent, head_font=head, shot=_shot_in(pack, data), logo=logo,
                shipped=shipped if shipped in PLAYBOOK_FOR else "feature")


def fit(text: str, font: Path, box: tuple[int, int], hi: int, lo: int, max_lines: int,
        weight: int = HEAD_WEIGHT) -> Text:
    """Lines and a pixel size at which the text sits inside the box, measured with Pillow
    against the browser's font file, at SLACK of the box width."""
    lines, size = fit_text(text, str(font), (round(box[0] * SLACK), box[1]), hi, lo, weight, max_lines=max_lines)
    return Text(lines, size)


def layout(plan: Plan) -> dict[str, Text]:
    """The text of each beat, fitted. Long text shrinks to its minimum, then wraps."""
    width = WIDTH - 2 * MARGIN_X
    out = {
        "line": fit(plan.line1, plan.head_font, (width, 640), 128, 56, 4),
        "card": fit(plan.card_line, plan.head_font, (width, 560), 112, 52, 3),
        "end_name": fit(plan.name, plan.head_font, (1500, 280), 120, 48, 2),
        "end_host": fit(plan.host, FONT_PATH, (1500, 64), 44, 24, 1, weight=450),
    }
    if plan.shot is None:
        out["product"] = fit(plan.name, plan.head_font, (width, 520), 230, 80, 2)
    plan.fitted = out
    return out


# ------------------------------------------------------------------ the HyperFrames project

def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def _pct(seconds: float) -> str:
    return f"{100 * seconds / SECONDS:.3f}%"


def _beat_keyframes(beat: str, start: float, end: float) -> str:
    """Opacity over the whole film for one beat: in over 0.35 s at its start (the first beat is
    there from frame 0), out over 0.3 s before its end (the last one holds to the end)."""
    stops = []
    if start <= 0:
        stops.append("0% { opacity: 1; }")
    else:
        stops += [f"0%, {_pct(start)} {{ opacity: 0; }}", f"{_pct(min(end, start + 0.35))} {{ opacity: 1; }}"]
    if end >= SECONDS:
        stops.append("100% { opacity: 1; }")
    else:
        stops += [f"{_pct(end - 0.3)} {{ opacity: 1; }}", f"{_pct(end)}, 100% {{ opacity: 0; }}"]
    return f"@keyframes ld-beat-{beat} {{ {' '.join(stops)} }}"


def _anim(name: str, duration: float, delay: float, easing: str = "cubic-bezier(0.2, 0.7, 0.2, 1)") -> str:
    return (f"animation: {name} {duration:.2f}s {easing} {delay:.2f}s 1 both;")


def _lines_html(text: Text, cls: str) -> str:
    return "".join(f'<div class="{cls}">{_esc(line)}</div>' for line in text.lines)


def _words_html(text: Text) -> str:
    """Line 1, each word rising in one after another, the lines broken where Pillow broke them."""
    words = [w for line in text.lines for w in line.split()]
    step = min(0.18, 1.9 / max(1, len(words)))
    out, n = [], 0
    for line in text.lines:
        spans = []
        for word in line.split():
            spans.append(f'<span class="word" style="animation-delay: {0.2 + n * step:.2f}s">{_esc(word)}</span>')
            n += 1
        out.append(f'<div class="line">{" ".join(spans)}</div>')
    return "".join(out)


def _shot_size(path: Path) -> tuple[int, int] | None:
    im = _open_image(path)
    if im is None:
        return None
    k = min(SHOT_BOX[0] / im.width, SHOT_BOX[1] / im.height, SHOT_UPSCALE)
    return max(1, round(im.width * k)), max(1, round(im.height * k))


def _save_shot(src: Path, dest: Path) -> bool:
    """The screenshot as a PNG, its longest side at most SHOT_MAX_SIDE."""
    im = _open_image(src)
    if im is None:
        return False
    im = im.convert("RGBA") if im.mode in ("RGBA", "LA", "P") else im.convert("RGB")
    if max(im.size) > SHOT_MAX_SIDE:
        im.thumbnail((SHOT_MAX_SIDE, SHOT_MAX_SIDE), Image.Resampling.LANCZOS)
    im.save(dest, "PNG", optimize=True)
    return True


def _font_face(family: str, font: Path, weights: str) -> str:
    return (f'@font-face {{ font-family: "{family}"; src: url("assets/fonts/{font.name}") format("truetype"); '
            f"font-weight: {weights}; font-style: normal; font-display: block; }}")


_WEIGHTS = {"Inter.ttf": "100 900", "Newsreader.ttf": "200 800", "Nunito.ttf": "200 1000"}


def _copy_fonts(plan: Plan, fonts: Path) -> None:
    fonts.mkdir(parents=True, exist_ok=True)
    for font in {plan.head_font, FONT_PATH}:
        shutil.copyfile(font, fonts / font.name)
        licence = FONT_DIR / OFL.get(font.name, "OFL.txt")
        if licence.is_file():
            shutil.copyfile(licence, fonts / licence.name)


def build_html(plan: Plan, shot: tuple[int, int] | None) -> str:
    """index.html: the root composition, its four beats and their keyframes."""
    fitted = plan.fitted or layout(plan)
    bg, ink, accent = plan.bg, plan.ink, plan.accent
    muted = "#%02X%02X%02X" % _mix(_rgb(ink), _rgb(bg), 0.42)
    edge = "#%02X%02X%02X" % _mix(_rgb(bg), _rgb(ink), 0.14)  # the screenshot's edge, seen on dark grounds too
    line, card, name, site = fitted["line"], fitted["card"], fitted["end_name"], fitted["end_host"]
    css = [
        _font_face(HEAD_FAMILY, plan.head_font, _WEIGHTS.get(plan.head_font.name, "100 900")),
        _font_face(TEXT_FAMILY, FONT_PATH, _WEIGHTS["Inter.ttf"]),
        f"html, body {{ margin: 0; padding: 0; width: {WIDTH}px; height: {HEIGHT}px; overflow: hidden; background: {bg}; }}",
        f"#root {{ position: relative; width: {WIDTH}px; height: {HEIGHT}px; overflow: hidden; background: {bg}; "
        f'color: {ink}; font-family: "{HEAD_FAMILY}", serif; font-weight: {HEAD_WEIGHT}; '
        "font-optical-sizing: auto; -webkit-font-smoothing: antialiased; }",
        f"#bg {{ position: absolute; inset: 0; background: {bg}; }}",
        "#root .beat { position: absolute; inset: 0; display: flex; flex-direction: column; justify-content: center; "
        f"padding: 0 {MARGIN_X}px; box-sizing: border-box; opacity: 0; }}",
        f"#root .line, #root .lines > div {{ white-space: nowrap; line-height: {LEADING}; }}",
        "#root .word { display: inline-block; " + _anim("ld-rise", 0.55, 0.2) + " }",
        "@keyframes ld-rise { from { opacity: 0; transform: translateY(0.42em); } to { opacity: 1; transform: translateY(0); } }",
        f"#root .rule {{ width: 200px; height: 14px; border-radius: 7px; background: {accent}; transform-origin: left center; }}",
        "@keyframes ld-draw { from { transform: scaleX(0); } to { transform: scaleX(1); } }",
        "@keyframes ld-up { from { opacity: 0; transform: translateY(90px); } to { opacity: 1; transform: translateY(0); } }",
        f"@keyframes ld-zoom {{ from {{ transform: scale(1); }} to {{ transform: scale({ZOOM}); }} }}",
        "@keyframes ld-pop { from { opacity: 0; transform: scale(0.9); } to { opacity: 1; transform: scale(1); } }",
        "@keyframes ld-fade { from { opacity: 0; transform: translateY(24px); } to { opacity: 1; transform: translateY(0); } }",
    ]
    for beat, start, end in BEATS:
        css.append(_beat_keyframes(beat, start, end))
        css.append(f"#beat-{beat} {{ animation: ld-beat-{beat} {SECONDS}s linear 0s 1 both; }}")
    css.append(f"#beat-line .lines {{ font-size: {line.size}px; }}")
    css.append(f"#beat-card .lines {{ font-size: {card.size}px; }}")
    css.append(f"#beat-card .rule {{ margin-top: {round(card.size * 0.45)}px; " + _anim("ld-draw", 0.7, 9.1) + " }")
    if shot is not None:
        w, h = shot
        css += [
            "#beat-product { align-items: center; padding: 0; }",
            f"#shot-frame {{ width: {w}px; height: {h}px; border-radius: {SHOT_RADIUS}px; overflow: hidden; "
            f"box-shadow: 0 0 0 1px {edge}, 0 28px 80px rgba(0, 0, 0, 0.28), 0 4px 14px rgba(0, 0, 0, 0.12); "
            + _anim("ld-up", 0.9, 3.5) + " }",
            f"#shot-frame img {{ display: block; width: {w}px; height: {h}px; transform-origin: 50% 40%; "
            + _anim("ld-zoom", 5.0, 3.5, "linear") + " }",
        ]
    else:
        big = fitted["product"]
        css += [
            f"#beat-product .lines {{ font-size: {big.size}px; " + _anim("ld-fade", 0.7, 3.55) + " }",
            f"#beat-product .rule {{ margin-top: {round(big.size * 0.3)}px; " + _anim("ld-draw", 0.7, 4.2) + " }",
        ]
    css += [
        "#beat-end { align-items: center; text-align: center; }",
        f"#end-mark {{ width: {MARK_SIZE}px; height: {MARK_SIZE}px; margin-bottom: 44px; " + _anim("ld-pop", 0.6, 11.6) + " }",
        f"#end-name {{ font-size: {name.size}px; " + _anim("ld-fade", 0.6, 11.75) + " }",
        f'#end-host {{ font-family: "{TEXT_FAMILY}", sans-serif; font-weight: 450; font-size: {site.size}px; '
        f"color: {muted}; margin-top: {round(site.size * 0.7)}px; " + _anim("ld-fade", 0.6, 11.9) + " }",
    ]
    if shot is not None:
        product = ('<div id="shot-frame"><img src="assets/shot.png" alt="'
                   + _esc(f"Screenshot of {plan.name}") + f'" width="{shot[0]}" height="{shot[1]}"></div>')
    else:
        product = f'<div class="lines">{_lines_html(fitted["product"], "l")}</div><div class="rule"></div>'
    end = ['<img id="end-mark" src="assets/mark.png" alt="" width="%d" height="%d">' % (MARK_SIZE, MARK_SIZE),
           f'<div id="end-name" class="lines">{_lines_html(name, "l")}</div>']
    if site.lines:
        end.append(f'<div id="end-host" class="lines">{_lines_html(site, "l")}</div>')
    body = [
        f'<div id="root" data-composition-id="main" data-start="0" data-duration="{SECONDS}" '
        f'data-width="{WIDTH}" data-height="{HEIGHT}" data-no-timeline>',
        '<div id="bg"></div>',
        f'<section id="beat-line" class="beat"><div class="lines">{_words_html(line)}</div></section>',
        f'<section id="beat-product" class="beat">{product}</section>',
        f'<section id="beat-card" class="beat"><div class="lines">{_lines_html(card, "l")}</div>'
        '<div class="rule"></div></section>',
        f'<section id="beat-end" class="beat">{"".join(end)}</section>',
        "</div>",
    ]
    return "\n".join([
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        f'<meta name="viewport" content="width={WIDTH}, height={HEIGHT}">',
        f"<title>{_esc('Launch film: ' + plan.name)}</title>",
        "<style>",
        *css,
        "</style>",
        "</head>",
        "<body>",
        *body,
        "</body>",
        "</html>",
        "",
    ])


def beat_rows(plan: Plan) -> list[dict]:
    """The four beats as data: id, start, end, what is on screen, the text."""
    product_text = "" if plan.shot is not None else plan.name
    rows = [
        {"id": "line", "show": "The first line of your post, large, one word at a time.", "text": plan.line1},
        {"id": "product",
         "show": ("Your screenshot in a rounded frame. It slides up, then zooms in slowly."
                  if plan.shot is not None else "The product name, very large, with a short line in your accent colour."),
         "text": product_text,
         "by_hand": f"A screen recording of {plan.what or plan.name}, about 5 seconds."},
        {"id": "card", "show": "What shipped, with a short line in your accent colour drawing in under it.",
         "text": plan.card_line},
        {"id": "end", "show": "Your logo, the product name and your site.",
         "text": ", ".join(x for x in (plan.name, plan.host) if x)},
    ]
    for row, (beat, start, end) in zip(rows, BEATS):
        row.update(start=start, end=end)
    return rows


def _secs(value: float) -> str:
    return f"{value:g}"


TITLES = {"line": "the first line", "product": "the product", "card": "what shipped", "end": "the end card"}


def beat_sheet(plan: Plan, playbook_md: list[str] = ()) -> str:
    lines = [f"# Launch film for {plan.name}", "",
             f"{SECONDS} seconds, silent, {WIDTH}x{HEIGHT} at {FPS} fps, in your brand colours. "
             "The project is in the film folder. Render it with HyperFrames, or film it yourself from this sheet.", ""]
    for n, row in enumerate(beat_rows(plan), 1):
        lines.append(f"## {n}. {_secs(row['start'])} to {_secs(row['end'])} s: {TITLES[row['id']]}")
        lines.append("")
        lines.append(f"On screen: {row['show']}")
        if row["text"]:
            lines.append(f"Text: {row['text']}")
        if row.get("by_hand"):
            lines.append(f"To film it yourself: {row['by_hand']}")
        lines.append("")
    if playbook_md:
        lines += list(playbook_md) + [""]
    return "\n".join(lines)


def write_project(pack: Path, plan: Plan) -> Path:
    """Write <pack>/film/ afresh (index.html, assets) and <pack>/film-beats.md. The project path."""
    project = pack / PROJECT
    if project.is_symlink():
        project.unlink()
    elif project.is_dir():
        shutil.rmtree(project)
    assets = project / "assets"
    assets.mkdir(parents=True)
    _copy_fonts(plan, assets / "fonts")
    shot = None
    if plan.shot is not None:
        size = _shot_size(plan.shot)
        if size is not None and _save_shot(plan.shot, assets / "shot.png"):
            shot = size
        else:
            plan.shot = None
    layout(plan)
    logo = _open_image(plan.logo) if plan.logo else None
    kit = BrandKit(name=plan.name, url="", tagline="", bg=plan.bg, ink=plan.ink, accent=plan.accent,
                   font_family=None, logo_path=None)
    _mark_tile(kit, logo, MARK_SIZE * 2, _rgb(plan.accent)).save(assets / "mark.png", "PNG")
    (project / "index.html").write_text(build_html(plan, shot), encoding="utf-8")
    return project


# ------------------------------------------------------------------ rendering

def renderer(config: Any, which) -> tuple[list[str], str] | None:
    """The render command template and where it came from: the film_render setting (a list of
    strings that names {project} and {out}), else `hyperframes` on PATH; None when neither."""
    if isinstance(config, (list, tuple)) and config and all(isinstance(a, str) and a for a in config):
        joined = " ".join(config)
        if "{project}" in joined and "{out}" in joined:
            return [str(a) for a in config], "config"
        logger.warning("launch-desk: film_render must name {project} and {out}; ignored")
    elif config not in (None, "", [], ()):
        logger.warning("launch-desk: film_render must be a list of strings; ignored")
    try:
        found = which("hyperframes")
    except Exception:
        found = None
    if found:
        return [str(found), "render", "{project}", "-o", "{out}"], "path"
    return None


def command(template: list[str], project: Path, out: Path) -> list[str]:
    return [a.replace("{project}", str(project)).replace("{out}", str(out)) for a in template]


def render_env() -> dict:
    """The environment the renderer gets: Hermes' own, plus the telemetry and update opt-outs."""
    return {**os.environ, **RENDER_ENV}


def _tail(output: Any) -> str:
    from .wizard import _redact  # noqa: PLC0415 (wizard imports this module)
    lines = [ln for ln in str(output or "").splitlines() if ln.strip()][-OUTPUT_TAIL_LINES:]
    return " | ".join(_redact(ln) for ln in lines)


def render(host: Host, template: list[str], project: Path, pack: Path) -> bool:
    """Run the renderer into a new file beside the pack, then move it to film.mp4. True on exit 0
    and a non-empty MP4. Never raises; the last lines of its output go to the plugin log."""
    out = pack / f".film-{uuid.uuid4().hex[:8]}.mp4"
    argv = command(template, project, out)
    started = time.monotonic()
    try:
        code, output = host.run(argv, cwd=project, env=render_env(), timeout=RENDER_TIMEOUT_S)
    except TimeoutError:
        logger.warning("launch-desk: film render timed out after %ss", RENDER_TIMEOUT_S)
        out.unlink(missing_ok=True)
        return False
    except Exception as exc:
        logger.warning("launch-desk: film render could not start: %s", type(exc).__name__)
        out.unlink(missing_ok=True)
        return False
    ok = code == 0 and out.is_file() and out.stat().st_size > 0
    level = logging.INFO if ok else logging.WARNING
    logger.log(level, "launch-desk: film render exit %s in %.1f s: %s", code, time.monotonic() - started, _tail(output))
    if not ok:
        out.unlink(missing_ok=True)
        return False
    os.replace(out, pack / MP4)
    return True


# ------------------------------------------------------------------ recommendations

def _beat_line(beat: dict, target: int) -> str | None:
    label, show, say = _clean(beat.get("label")), _clean(beat.get("show")), _clean(beat.get("say"))
    share = beat.get("share")
    if not label or not isinstance(share, (int, float)) or isinstance(share, bool):
        return None
    secs = max(1, round(share * target))
    parts = [f"{label}, {secs} s:"]
    if show:
        parts.append(f"show {show[0].lower() + show[1:]}")
    if say:
        parts.append(f"Say something shaped like: {say}")
    return " ".join(parts)


def recommend(host: Host, shipped: str) -> tuple[list[str], list[str], str | None]:
    """Lines for the reply (at most 6), lines for the shot list, and the playbook's name.
    list_playbooks is free; get_playbook (1 credit) only when get_my_access lists it. Any
    posts.design failure leaves this part out, with one log line."""
    try:
        listing = host.mcp("list_playbooks", {})
        books = [b for b in listing.get("playbooks") or [] if isinstance(b, dict) and b.get("name")]
    except Exception as exc:
        logger.info("launch-desk: film playbooks skipped (%s)", type(exc).__name__)
        return [], [], None
    if not books:
        return [], [], None
    want = PLAYBOOK_FOR.get(shipped, PLAYBOOK_FOR["feature"])
    book = next((b for b in books if b.get("name") == want), books[0])
    title = _clean(book.get("title")) or str(book["name"])
    runtime = book.get("runtime") if isinstance(book.get("runtime"), dict) else {}
    target = runtime.get("targetSeconds") if isinstance(runtime.get("targetSeconds"), int) else 0
    when = next((_clean(u) for u in book.get("useWhen") or [] if _clean(u)), "").rstrip(".")
    about = f", about {target} s" if target else ""
    use = f", for {when[:1].lower() + when[1:]}" if when else ""
    head = f"For a longer film, posts.design suggests its {title} playbook{about}{use}."
    beats: list[str] = []
    try:
        access = host.mcp("get_my_access", {})
        owned = "get_playbook" in (access.get("tools") or [])
    except Exception as exc:
        logger.info("launch-desk: film access check skipped (%s)", type(exc).__name__)
        owned = False
    if owned:
        try:
            full = host.mcp("get_playbook", {"name": book["name"]})
            full_target = (full.get("runtime") or {}).get("targetSeconds") or target or SECONDS
            beats = [ln for ln in (_beat_line(b, int(full_target)) for b in full.get("beats") or []
                                   if isinstance(b, dict)) if ln]
        except Exception as exc:
            logger.info("launch-desk: film playbook beats skipped (%s)", type(exc).__name__)
            beats = []
    reply = [head]
    md = [f"## For a longer film: {title}{about}", ""]
    if when:
        md.append(f"Use it for: {when}.")
    if beats:
        reply[0] = head + " Its beats, read for 1 posts.design credit:"
        shown = beats if len(beats) <= PLAYBOOK_BEATS_SHOWN else beats[: PLAYBOOK_BEATS_SHOWN - 1]
        reply += [f"- {b}" for b in shown]
        if len(beats) > len(shown):
            reply.append(f"- {len(beats) - len(shown)} more beats in {BEAT_SHEET}.")
        md += ["", "Its beats. What to say is a shape to fill with your own words, not a script:", ""]
        md += [f"- {b}" for b in beats]
    return reply, md, str(book["name"])


# ------------------------------------------------------------------ the command

def _local(host: Host) -> bool:
    from .wizard import _local as local  # noqa: PLC0415
    return local(host)


def _surface(host: Host) -> str:
    from .wizard import _surface as surface  # noqa: PLC0415
    return surface(host)


def resolve(root: Path, value: str) -> str | None:
    """The pack /launch film means: blank is the newest, else a label or a folder name."""
    try:
        name = followup.resolve_pack(root, value)
    except Exception:
        return None
    if isinstance(name, str) and followup._NAME_RE.fullmatch(name) and ".." not in name \
            and (root / name / "pack.json").is_file() and not (root / name).is_symlink():
        return name
    return None


def _load(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _film_field(pack: Path, previous: Any, renderer_kind: str | None, rendered: bool, playbook: str | None) -> dict:
    prev = previous if isinstance(previous, dict) else {}
    has_mp4 = (pack / MP4).is_file() and (pack / MP4).stat().st_size > 0
    if rendered:
        when, kind = datetime.now(timezone.utc).isoformat(timespec="seconds"), renderer_kind
    elif has_mp4:  # an earlier render of the same pack is still there
        when, kind = prev.get("rendered_at"), prev.get("renderer")
    else:
        when, kind = None, None
    return {"project": PROJECT, "mp4": MP4 if has_mp4 else None, "rendered_at": when,
            "renderer": kind if kind in ("config", "path") else None, "playbook": playbook}


def render_line(project: Path | str, out: Path | str) -> str:
    return (f"To render it yourself with Node 22 or later: HYPERFRAMES_NO_TELEMETRY=1 "
            f"npx hyperframes render {project} -o {out}")


def _beats_short(plan: Plan) -> list[str]:
    """The shot list in four lines, for filming by hand: beat 2 is a screen recording."""
    out = []
    for n, row in enumerate(beat_rows(plan), 1):
        body = row["by_hand"].rstrip(".") if row["id"] == "product" else row["text"]
        out.append(f"{n}. {_secs(row['start'])} to {_secs(row['end'])} s: {body}")
    return out


def make_film(host: Host, value: str) -> str:
    root = host.data_dir() / "packs"
    name = resolve(root, value)
    if name is None:
        if not (value or "").strip():
            return NO_PACK
        labels = followup.recent_labels(root, 3) if root.is_dir() else []
        known = f" Recent packs: {'; '.join(labels)}." if labels else ""
        return (f"There is no pack called {_clean(value)}. Send /launch film for your latest pack, "
                f"or name one.{known}")
    pack = root / name
    data = _load(pack / "pack.json")
    if data is None:
        return "That pack could not be read, so there is no film."
    plan = plan_for(pack, data, host.state_get("brand"))
    project = write_project(pack, plan)
    rec, md, playbook = recommend(host, plan.shipped)
    (pack / BEAT_SHEET).write_text(beat_sheet(plan, md), encoding="utf-8")

    try:
        setting = host.config(CONFIG_KEY)
    except Exception:
        setting = None
    found = renderer(setting, host.which)
    rendered = False
    if found is not None:
        host.say("Rendering the film. This takes a minute or two.")
        rendered = render(host, found[0], project, pack)
    data["film"] = _film_field(pack, data.get("film"), found[1] if found else None, rendered, playbook)
    try:
        followup._write_json(pack / "pack.json", data)
    except OSError:
        logger.warning("launch-desk: pack.json film field not saved", exc_info=True)

    local = _local(host)
    desktop = local and _surface(host) in ("desktop", "acp")
    where = pack if local and not desktop else None
    lines: list[str] = []
    if rendered:
        lines.append(f"Your launch film is ready: {SECONDS} seconds, silent, in your brand colours. Nothing was posted.")
        if where is not None:
            lines += [f"Film: {pack / MP4}", f"Shot list: {pack / BEAT_SHEET}"]
        elif desktop:
            lines.append(f"Saved in {name}. The Launch Desk page in Hermes Desktop shows it.")
        else:
            lines.append(f"Saved in {name}, with its shot list.")
    else:
        if found is not None:
            lines.append(RENDER_FAILED)
        if where is not None:
            lines.append(f"Your film project is ready: {project} ({SECONDS} seconds, silent, in your brand colours). "
                         "Nothing was posted.")
            lines.append(render_line(project, pack / MP4))
            lines.append(f"Or film it yourself from the shot list ({pack / BEAT_SHEET}):")
        else:
            lines.append(f"Your film project is ready in {name} ({SECONDS} seconds, silent, in your brand colours). "
                         "Nothing was posted.")
            lines.append(render_line(PROJECT, MP4) + f", in the {name} folder where Hermes runs.")
            lines.append("Or film it yourself from the shot list:")
        lines += _beats_short(plan)
    if rec:
        lines += [""] + rec
    if rendered:
        lines.append(f"MEDIA:{host.media_path(pack / MP4, 'film')}")
    return "\n".join(lines)


def run_film(host: Host, value: str = "") -> str:
    """/launch film [pack]: the chat reply. Never raises; unattended, renders nothing."""
    from .wizard import _unattended  # noqa: PLC0415
    if _unattended(host):
        return UNATTENDED
    try:
        return make_film(host, value or "")
    except Exception:
        logger.exception("launch-desk: /launch film failed")
        return "Something went wrong while making the film, with details in the Hermes log. Nothing was posted."
