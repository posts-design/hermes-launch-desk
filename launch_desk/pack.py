"""The launch pack: the folder the founder takes away. Nothing is posted from here."""
from __future__ import annotations

import json
import re
import shutil
import unicodedata
import urllib.parse
from datetime import date, datetime, timezone
from pathlib import Path

from .models import BrandKit, Brief, Draft, Reference
from .references import shape_note

ALT_MAX = 1000
SLUG_WHAT_MAX = 40

# Reworded from the posts.design playbook checks (feed-test, first-frame, muted-check,
# judge-by-conversion) for an image post, plus a read-through line.
CHECKLIST: tuple[str, ...] = (
    "Put the first line in a real feed. Would you stop scrolling for it?",
    "Choose the card for the people you want: their tool, their screen. Does the card carry the hook on its own?",
    "Cover the caption and look at the card alone. Is the point still clear?",
    "Read the post once, start to finish, and change anything that is not true. The draft can carry a detail you never gave it.",
    "Judge it by sign-ups or downloads, with a tracked link, and by who comes back. Not by views.",
)
JUDGE_BY_CONVERSION = CHECKLIST[4]  # the after-post check; check-ins repeat it


def x_intent_url(text: str) -> str:
    return "https://x.com/intent/post?text=" + urllib.parse.quote(text, safe="")


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


CARD_SHOTS = {"statement": 0, "hero": 1, "split": 2}


def card_contents(layout: str | None, has_screenshot: bool) -> dict:
    """What the card shows: its layout and how many screenshots are on it. `layout` is the one
    the card was drawn with; without it (or an unknown one) it follows has_screenshot."""
    if layout not in CARD_SHOTS:
        layout = "hero" if has_screenshot else "statement"
    return {"layout": layout, "screenshots": CARD_SHOTS[layout]}


def alt_text(brief: Brief, brand: BrandKit, has_screenshot: bool, headline: str | None = None,
             layout: str | None = None) -> str:
    name = " ".join(brand.name.split()) or "the product"
    what = _clip(headline or brief.what, 400).rstrip(".")
    shots = card_contents(layout, has_screenshot)["screenshots"]
    parts = [f"Launch card for {name}.", f"Headline: {what}."]
    if shots == 2:
        parts.append(f"It shows two screenshots of {name} side by side.")
    elif shots == 1:
        parts.append(f"It shows a screenshot of {name}.")
    else:
        parts.append(f"A text card: the headline set large, with {name} below it.")
    if brand.tagline:
        parts.append(_clip(brand.tagline, 200))
    return _clip(" ".join(parts), ALT_MAX)


def _slug(text: str, limit: int | None = None) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if limit:
        slug = slug[:limit].strip("-")
    return slug or "launch"


def _new_folder(base: Path, name: str) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    n = 1
    while True:
        path = base / (name if n == 1 else f"{name}-{n}")
        try:
            path.mkdir()
            return path
        except FileExistsError:
            n += 1


def post_text(draft: Draft, hook: str) -> str:
    """The post as it will be sent: the caption with its first line replaced by the chosen hook."""
    lines = draft.caption.strip().splitlines()
    if lines and lines[0].strip() != hook.strip():
        lines[0] = hook.strip()
    elif not lines:
        lines = [hook.strip()]
    return "\n".join(lines).strip()


def _fmt_views(views: int | None) -> str:
    return f"{views:,} views" if views is not None else "views not shown"


def write_pack(out_dir: Path, brief: Brief, brand: BrandKit, draft: Draft, hook: str,
               cards: dict[str, Path], refs: list[Reference],
               has_screenshot: bool = False, preview: Path | None = None,
               headline: str | None = None, more_refs: list[Reference] = (),
               layout: str | None = None, hook_source: str = "model",
               checks: list[dict] = ()) -> Path:
    """The pack folder. `checks` is the post's checklist ({"rules", "ok", "text"} per line);
    `hook_source` says whose words the hook is: "founder" or "model"."""
    name = f"{date.today().isoformat()}-{_slug(brand.name, SLUG_WHAT_MAX)}-{_slug(brief.what, SLUG_WHAT_MAX)}"
    pack = _new_folder(Path(out_dir), name)

    text = post_text(draft, hook)
    intent = x_intent_url(text)
    files = {"post": "post-x.txt", "alt_text": "alt-text.txt", "checklist": "checklist.md",
             "references": "references.md"}
    (pack / "post-x.txt").write_text(text + "\n", encoding="utf-8")

    for key, src in cards.items():
        dest = f"card-{re.sub(r'[^a-z0-9-]', '', str(key).lower()) or 'x'}.png"
        shutil.copyfile(src, pack / dest)
        files[f"card_{key}"] = dest
    if preview is not None:
        shutil.copyfile(preview, pack / "preview-x.png")
        files["preview"] = "preview-x.png"
    (pack / "alt-text.txt").write_text(alt_text(brief, brand, has_screenshot, headline, layout) + "\n", encoding="utf-8")

    check = ["# Before you post", "", f"Open this link to post on X with the text filled in: {intent}", "",
             "Nothing was posted. You send it.", ""]
    check += [f"- [ ] {line}" for line in CHECKLIST]
    if checks:
        check += ["", "## Checked in code", ""]
        check += [f"- {'✓' if c['ok'] else '✗'} {c['text']}" for c in checks]
    (pack / "checklist.md").write_text("\n".join(check) + "\n", encoding="utf-8")

    ref_lines = ["# References", "", "Posts that did well and shaped the draft.", ""]
    for r in refs:
        if r.signups is not None:  # the founder's own launch: its pack, not a link
            ref_lines.append(f"- {r.why}: pack {r.url}")
        else:
            ref_lines.append(f"- {r.brand}, {_fmt_views(r.views)}: {r.url}")
    (pack / "references.md").write_text("\n".join(ref_lines) + "\n", encoding="utf-8")

    files["pack"] = "pack.json"
    data = {
        "brief": {"shipped": brief.shipped, "network": brief.network, "what": brief.what,
                  "detail": brief.detail, "product_url": brief.product_url},
        "brand": {"name": brand.name, "url": brand.url, "bg": brand.bg, "ink": brand.ink,
                  "accent": brand.accent},
        "hook": hook,
        "hook_source": hook_source,
        "card_line": headline or "",
        "hooks": list(draft.hooks),
        "caption": text,
        "card": card_contents(layout, has_screenshot),
        "references": [{"id": r.id, "brand": r.brand, "url": r.url, "views": r.views,
                        **({"signups": r.signups} if r.signups is not None else {})} for r in refs],
        # References the founder was not shown, as their shape (not their words), for check-ins.
        "more_references": [{"id": r.id, "brand": r.brand, "url": r.url, "views": r.views,
                             "shape": shape_note(r.caption)} for r in more_refs],
        "intent_url": intent,
        "checks": [dict(c) for c in checks],
        "files": files,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (pack / "pack.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return pack
