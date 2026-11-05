"""/launch: from "I just shipped X" to an approved launch pack, in two phases.

Phase 1, `/launch <what> [-- <detail>]`: a few questions (asked before any network call,
so a session that cannot answer stops at once), the brand, the setup check, three
references, the draft, the hook, then the card and the feed preview. The reply shows
them, and the draft waits in state under "pending" (24 hours) with its files in
data_dir/pending/<id>/.

Phase 2, `/launch` alone: Approve, Change hook, Swap references or Discard. Approve
writes the pack. Nothing is ever posted.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
import time
import urllib.parse
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Callable

from .brand import NEUTRAL, PRESET_ACCENTS, fetch_brand, http_get
from .draft import DraftError, draft_post, facts_text, unsupported_details
from .hooks import pick_hook_shapes
from .host import Host, SourceUnavailable
from .models import BrandKit, Brief, Draft, Failure, Reference
from .pack import post_text, write_pack
from .references import CATEGORY, McpSource, SamplePackSource, build_search_args, rank_references
from .render import render_card, render_feed_preview
from .rules import check, relaxed_rules
from .setup_check import check_setup

logger = logging.getLogger(__name__)

X_CARD = (1200, 675)
HEADLINE_MAX = 70
PENDING_KEY = "pending"
PENDING_TTL_S = 24 * 60 * 60
VOICE_MAX = 3

EXAMPLE = "/launch https://yoursite.com dark mode for the editor -- people kept asking for it"
USAGE = (f"Usage: /launch <what you shipped> [-- <one real detail>], for example: {EXAMPLE}\n"
         "/launch help also shows brand and voice.")
HELP = "\n".join((
    "/launch <what you shipped> [-- <one real detail>]",
    f"Example: {EXAMPLE}",
    "Add your site's link the first time; after that it is remembered.",
    "The part after -- is one true detail, like who asked for it. The draft uses it as a fact.",
    "You get the draft, the card and a feed preview. Send /launch again to approve, change the hook, "
    "swap references or discard.",
    "/launch brand <url> reads your site's colours and logo again.",
    "/launch voice: <a past post of yours> keeps it as a voice sample, up to 3; drafts follow its style. "
    "/launch voice: clear forgets them.",
    "Nothing is ever posted. You get a pack to post yourself.",
))
UNATTENDED = "/launch needs you to answer a few questions; run it in a chat."
STOPPED = "Stopped: nothing was saved."
WAITING = "Stopped. Your draft is still waiting; send /launch to pick up where you left off."
DISCARDED = "Discarded. Nothing was posted."
NEXT_STEP = "Send /launch to approve, change the hook, swap references or discard."
REPLACED = "This draft replaces the one that was waiting."
LINKEDIN_LATER = "LinkedIn isn't ready yet, so this draft is for X."
VOICE_USAGE = "Usage: /launch voice: <a past post of yours>, or /launch voice: clear"
VOICE_NO_BRAND = ("There's no brand saved yet, and voice samples are kept with it. "
                  "Send /launch brand https://yoursite.com first.")
STYLE_WORDS = {"em-dash": "more than one em-dash", "emoji": "more than one emoji",
               "hashtags": "more than two hashtags"}
NO_MORE_REFS = "There are no other references to swap in, so these stay."
PACK_UNAVAILABLE = "posts.design isn't available, so these come from the sample pack of launch posts."
PACK_TOO_FEW = "Not enough close matches on posts.design; using the sample pack."
DRAFT_FAILED = "the model did not return a usable draft; run /launch again, or try a larger model"

SHIPPED = [
    {"value": "feature", "label": "A new feature"},
    {"value": "product", "label": "A new product"},
    {"value": "rebrand", "label": "A rebrand"},
    {"value": "partnership", "label": "A partnership"},
]
NETWORKS = [{"value": "x", "label": "X"}, {"value": "linkedin", "label": "LinkedIn (soon)"}]
ACCENT_NAMES = ("Blue", "Orange", "Green", "Pink", "Violet")
NEXT_CHOICES = [
    {"value": "approve", "label": "Approve"},
    {"value": "hook", "label": "Change hook"},
    {"value": "swap", "label": "Swap references"},
    {"value": "discard", "label": "Discard"},
]

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_DETAIL_SPLIT = re.compile(r"(?:^|\s)--(?:\s|$)")
_VOICE_RE = re.compile(r"voice\s*:(.*)", re.I | re.S)
_HELP_WORDS = {"help", "-h", "--help", "?"}


class _Stop(Exception):
    """The founder did not answer: end the run."""


def _now() -> float:
    return time.time()


# ------------------------------------------------------------------ arguments

@dataclass(frozen=True)
class Args:
    what: str
    detail: str
    url: str | None


def _tidy(text: str) -> str:
    text = re.sub(r"\s+([,.;:!?])", r"\1", " ".join(text.split()))
    return text.strip(" ,;:-–—")


def parse_args(raw: str) -> Args:
    """`<what> [-- <detail>]`; the first URL anywhere is the product URL and every URL is
    taken out of the text."""
    parts = _DETAIL_SPLIT.split(raw or "", maxsplit=1)
    found: list[str] = []

    def strip_urls(text: str) -> str:
        def repl(m: re.Match) -> str:
            found.append(m.group(0).rstrip(".,;:!?)]}'\""))
            return " "
        return _URL_RE.sub(repl, text)

    what = strip_urls(parts[0])
    detail = strip_urls(parts[1]) if len(parts) > 1 else ""
    return Args(_tidy(what), _tidy(detail), found[0] if found else None)


def _hostname(url: str) -> str:
    host = (urllib.parse.urlsplit(url.strip()).hostname or "").lower()
    return (host[4:] if host.startswith("www.") else host) or url


def _cut(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    head = text[: limit + 1]
    cut = head.rsplit(" ", 1)[0] if " " in head else text[:limit]
    return cut.rstrip(" ,;:-–—")


_CARD_PREFIX = re.compile(r"^check this out:\s*", re.IGNORECASE)


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def card_headline(hook: str, what: str) -> str:
    """The hook when it fits on the card (70 characters), else what shipped, cut at a word.
    A leading "Check this out:" is for the feed, so the card leaves it out."""
    hook = _CARD_PREFIX.sub("", " ".join(hook.split()))
    return _cap(hook if hook and len(hook) <= HEADLINE_MAX else _cut(what, HEADLINE_MAX))


# ------------------------------------------------------------------ host helpers

LOCAL_SURFACES = {"cli", "tui", "local", "desktop", "acp"}


def _tell(host: Host, notes: list[str], line: str) -> None:
    """A line the founder must see once: in the reply on a local client, where it arrives
    together with the progress lines; as a message on a chat app, where the reply comes later."""
    try:
        local = host.surface() in LOCAL_SURFACES
    except Exception:
        local = True
    if local:
        notes.append(line)
    else:
        host.say(line)


def _unattended(host: Host) -> bool:
    try:
        if host.unattended():
            return True
    except Exception:
        pass
    try:
        return host.surface() == "cron"
    except Exception:
        return False


def _ask(host: Host, question: str, choices: list[dict]) -> str:
    picked = host.choose(question, choices)
    if picked is None:
        raise _Stop()
    return picked


def _screenshot(value: Any) -> Path | None:
    if isinstance(value, str) and value:
        path = Path(value)
        if path.is_file():
            return path
    return None


# ------------------------------------------------------------------ brand

def _kit_from(data: Any) -> BrandKit | None:
    if not isinstance(data, dict):
        return None
    try:
        kit = BrandKit(
            name=str(data["name"]), url=str(data["url"]), tagline=str(data.get("tagline") or ""),
            bg=str(data["bg"]), ink=str(data["ink"]), accent=str(data["accent"]),
            font_family=data.get("font_family"), logo_path=data.get("logo_path"),
            voice_samples=tuple(str(s) for s in data.get("voice_samples") or ()),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if not kit.url:
        return None
    if kit.logo_path and not Path(kit.logo_path).is_file():
        kit = replace(kit, logo_path=None)
    return kit


def _kit_dict(kit: BrandKit) -> dict:
    data = asdict(kit)
    data["voice_samples"] = list(kit.voice_samples)
    return data


def _store_brand(host: Host, kit: BrandKit) -> BrandKit:
    """Keep the brand for later runs; its logo moves into data_dir/brand/."""
    brand_dir = host.data_dir() / "brand"
    brand_dir.mkdir(parents=True, exist_ok=True)
    src = Path(kit.logo_path) if kit.logo_path else None
    keep = brand_dir / src.name if src is not None and src.is_file() else None
    for old in brand_dir.glob("logo.*"):
        if keep is None or old.resolve() != src.resolve():
            old.unlink(missing_ok=True)
    if keep is not None:
        if src.resolve() != keep.resolve():
            shutil.copyfile(src, keep)
        kit = replace(kit, logo_path=str(keep))
    else:
        kit = replace(kit, logo_path=None)
    host.state_set("brand", _kit_dict(kit))
    return kit


def _read_brand(host: Host, url: str, get: Callable[[str], bytes], out: Path) -> BrandKit:
    """Read the site, then let the founder keep its accent or pick a preset."""
    site = _hostname(url)
    host.say(f"Reading {site} for its colours and logo.")
    kit = fetch_brand(url, get, out)
    presets = [{"value": hex_, "label": f"{name} {hex_}"} for name, hex_ in zip(ACCENT_NAMES, PRESET_ACCENTS)]
    if kit.accent != NEUTRAL.accent:
        question = (f"Colours from {site}: background {kit.bg}, text {kit.ink}, accent {kit.accent}.\n"
                    "Which accent should the card use?")
        choices = [{"value": "keep", "label": f"Keep {kit.accent}"}] + [
            c for c in presets if c["value"] != kit.accent]
    else:
        question = (f"I couldn't find a brand colour on {site}, so the card uses plain colours.\n"
                    "Pick an accent for it.")
        choices = presets
    picked = _ask(host, question, choices)
    if picked != "keep":
        kit = replace(kit, accent=picked)
    return kit


# ------------------------------------------------------------------ references

def _search(host: Host, brief: Brief, offline: bool) -> tuple[list[dict], str]:
    """The candidate items for this draft, and a note when they come from the sample pack."""
    args = build_search_args(brief)
    note = PACK_UNAVAILABLE
    if not offline:
        try:
            items = McpSource(host).search(args)
            if len(rank_references(items, brief)) >= 3:
                return items, ""
            note = PACK_TOO_FEW
        except SourceUnavailable:
            pass
    return SamplePackSource().search(args), note


def _refs_block(refs: list[Reference], used_pack: bool) -> str:
    head = "Three launch posts that worked" + (" (from the sample pack)" if used_pack else "") + ":"
    lines = [head]
    for n, r in enumerate(refs, 1):
        lines.append(f"{n}. {r.brand or 'A brand'}: {r.why}. {r.url}")
    return "\n".join(lines)


def _pick_hook(host: Host, hooks: list[str], caption: str, refs: list[Reference], used_pack: bool) -> str:
    body = "\n".join(caption.strip().splitlines()[1:]).strip()
    question = _refs_block(refs, used_pack) + "\n\nPick the hook, the first line of your post."
    if body:
        question += "\nThe rest of the post:\n\n" + body
    choices = [{"value": str(n), "label": h} for n, h in enumerate(hooks, 1)]
    picked = _ask(host, question, choices)
    return hooks[int(picked) - 1]


def _flagged(text: str, brief: Brief, brand: BrandKit, refs: list[Reference]) -> list[Failure]:
    """Scroll-test failures of the post as it will be sent (chosen hook in place)."""
    found = check(text, network=brief.network, reference_captions=[r.caption for r in refs],
                  voice_samples=brand.voice_samples)
    return found + unsupported_details(text, facts_text(brief, brand))


# ------------------------------------------------------------------ the pending draft

class _Pending:
    """The draft waiting for /launch: plain data in state, files in data_dir/pending/<id>/."""

    def __init__(self, data: dict):
        self.data = json.loads(json.dumps(data))  # a copy: state changes only on state_set
        self.dir = Path(data["dir"])
        self.brief = Brief(**data["brief"])
        self.brand = _kit_from(data["brand"]) or NEUTRAL
        self.refs = [Reference(**r) for r in data["refs"]]

    @property
    def text(self) -> str:
        return post_text(self.draft, self.data["hook"])

    @property
    def draft(self) -> Draft:
        return Draft(tuple(self.data["hooks"]), self.data["caption"], (), False)

    def items(self) -> list[dict]:
        try:
            return json.loads((self.dir / "items.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def set_refs(self, refs: list[Reference]) -> None:
        self.refs = refs
        self.data["refs"] = [asdict(r) for r in refs]
        self.data["shown"] = sorted(set(self.data["shown"]) | {r.id for r in refs})

    def set_draft(self, draft: Draft) -> None:
        self.data["hooks"], self.data["caption"] = list(draft.hooks), draft.caption

    def render(self, host: Host) -> None:
        """Draw the card and the preview into a new round folder; drop the previous one."""
        host.say("Drawing the card.")
        n = int(self.data.get("round") or 0) + 1
        out = self.dir / f"r{n}"
        shot = _screenshot(self.data.get("screenshot"))
        headline = card_headline(self.data["hook"], self.brief.what)
        card = render_card(self.brand, shot, headline, X_CARD, out / "card-x.png")
        preview = render_feed_preview(self.brand, self.text, card, out / "preview-x.png")
        old = self.dir / f"r{n - 1}"
        if n > 1 and old.is_dir():
            shutil.rmtree(old, ignore_errors=True)
        self.data.update(round=n, card=str(card), preview=str(preview))

    def reply(self, host: Host, notes: list[str]) -> str:
        lines = [*notes, ""] if notes else []
        text = self.text
        lines += ["Your draft for X:", "", text, ""]
        flagged = _flagged(text, self.brief, self.brand, self.refs)
        if flagged:
            lines.append("Still flagged: " + " ".join(f.message for f in flagged))
        lines += [f"MEDIA:{host.media_path(Path(self.data['preview']), 'preview')}",
                  f"MEDIA:{host.media_path(Path(self.data['card']), 'card')}", NEXT_STEP]
        return "\n".join(lines)


def _pending_root(host: Host) -> Path:
    root = host.data_dir() / "pending"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _remove_dir(host: Host, path: Any) -> None:
    """Delete a pending folder, and only ever one under data_dir/pending/."""
    if not isinstance(path, str) or not path:
        return
    target = Path(path)
    try:
        inside = target.resolve().parent == _pending_root(host).resolve()
    except OSError:
        return
    if inside:
        shutil.rmtree(target, ignore_errors=True)


def _forget_screenshot(host: Host, used: Any) -> None:
    """Clear the attached-screenshot state, unless a newer one has replaced the one a draft used."""
    if used and host.state_get("screenshot") == used:
        host.state_set("screenshot", None)


def _drop_pending(host: Host, data: Any) -> None:
    if isinstance(data, dict):
        _remove_dir(host, data.get("dir"))
    host.state_set(PENDING_KEY, None)


def _load_pending(host: Host) -> _Pending | None:
    """The waiting draft; an expired or unreadable one is deleted and counts as absent."""
    data = host.state_get(PENDING_KEY)
    if data is None:
        return None
    try:
        fresh = _now() - float(data["created_at"]) < PENDING_TTL_S
        pending = _Pending(data) if fresh and Path(data["dir"]).is_dir() else None
    except (KeyError, TypeError, ValueError, AttributeError):
        pending = None
    if pending is None:
        _drop_pending(host, data)
        if isinstance(data, dict):
            _forget_screenshot(host, data.get("screenshot"))
    return pending


# ------------------------------------------------------------------ phase 1

def _phase1(host: Host, args: Args, stored: BrandKit | None, get: Callable[[str], bytes],
            folder: Path, notes: list[str]) -> str:
    shipped = _ask(host, "What kind of launch is this?", SHIPPED)
    network = _ask(host, "Where will you post it?", NETWORKS)
    if network != "x":
        _tell(host, notes, LINKEDIN_LATER)

    brand_new = bool(args.url) and (stored is None or _hostname(args.url) != _hostname(stored.url))
    brand = _read_brand(host, args.url, get, folder / "brand") if brand_new else stored
    brief = Brief(shipped, "x", args.what, args.url or brand.url, detail=args.detail)

    issues = [i for i in check_setup(lambda: host.mcp("get_my_access", {})) if i.code != "unreachable"]
    if issues:
        _tell(host, notes, f"{issues[0].message}\n{issues[0].command}")

    shot = _screenshot(host.state_get("screenshot"))
    line = f"Finding launch posts like yours for {brand.name or _hostname(brand.url)}."
    if shot is None:
        line += " Tip: send a screenshot and ask me to use it for the launch; the card is better with one."
    else:
        used = f"Using the screenshot you attached: {shot.name}"
        line += f" {used}."
        notes.append(f"{used}.")
    host.say(line)
    items, pack_note = _search(host, brief, offline=bool(issues))
    if pack_note:
        host.say(pack_note)
        if not any(i.get("category") == CATEGORY[brief.shipped] for i in items):
            notes.append(f"There are no {brief.shipped} posts in the sample pack, so these are other launch posts.")
    refs = rank_references(items, brief)

    host.say("Drafting the post.")
    kept = [STYLE_WORDS[r] for r in sorted(relaxed_rules(brand.voice_samples)) if r in STYLE_WORDS]
    if kept:
        notes.append(f"Kept your style: your past posts use {' and '.join(kept)}, so the draft may too.")
    draft = draft_post(host, brief, brand, refs, pick_hook_shapes(brief.shipped))
    hook = _pick_hook(host, list(draft.hooks), draft.caption, refs, bool(pack_note))

    (folder / "items.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    pending = _Pending({
        "id": folder.name, "dir": str(folder), "created_at": _now(),
        "brief": asdict(brief), "brand": _kit_dict(brand), "brand_new": brand_new,
        "refs": [asdict(r) for r in refs], "shown": sorted(r.id for r in refs),
        "used_pack": bool(pack_note), "hooks": list(draft.hooks), "hook": hook,
        "caption": draft.caption, "screenshot": str(shot) if shot else None, "round": 0,
    })
    pending.render(host)
    host.state_set(PENDING_KEY, pending.data)
    return pending.reply(host, notes)


def _start(host: Host, args: Args, stored: BrandKit | None, get: Callable[[str], bytes]) -> str:
    """Phase 1 in its own folder; the folder goes if the run does not finish, and a draft
    already waiting is replaced only when the new one is ready."""
    old = _load_pending(host)
    folder = _pending_root(host) / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    folder.mkdir()
    notes = [REPLACED] if old is not None else []
    done = False
    try:
        reply = _phase1(host, args, stored, get, folder, notes)
        done = True
    finally:
        if not done:
            shutil.rmtree(folder, ignore_errors=True)
    if old is not None:
        _remove_dir(host, old.data.get("dir"))
    return reply


# ------------------------------------------------------------------ phase 2

def _approve(host: Host, p: _Pending) -> str:
    pack = write_pack(host.data_dir() / "packs", p.brief, p.brand, p.draft, p.data["hook"],
                      {"x": Path(p.data["card"])}, p.refs,
                      has_screenshot=_screenshot(p.data.get("screenshot")) is not None,
                      preview=Path(p.data["preview"]),
                      headline=card_headline(p.data["hook"], p.brief.what))
    if p.data.get("brand_new"):
        _store_brand(host, p.brand)
    _drop_pending(host, p.data)
    _forget_screenshot(host, p.data.get("screenshot"))
    data = json.loads((pack / "pack.json").read_text(encoding="utf-8"))
    text = (pack / data["files"]["post"]).read_text(encoding="utf-8").strip()
    return "\n".join((
        "Your launch pack is ready. Nothing was posted.",
        "",
        text,
        "",
        f"Post it on X with this text filled in: {data['intent_url']}",
        f"Pack folder: {pack}",
        "Read it once and change anything that isn't true.",
        "To edit the text, change it in X's compose box or in post-x.txt.",
        f"MEDIA:{host.media_path(pack / data['files']['card_x'], 'card')}",
        f"MEDIA:{host.media_path(pack / data['files']['preview'], 'preview')}",
    ))


def _resume(host: Host, p: _Pending) -> str:
    question = f"Your draft for X is waiting. It starts: {p.data['hook']}\nWhat next?"
    picked = _ask(host, question, NEXT_CHOICES)
    if picked == "approve":
        return _approve(host, p)
    if picked == "discard":
        _drop_pending(host, p.data)
        _forget_screenshot(host, p.data.get("screenshot"))
        return DISCARDED
    notes: list[str] = []
    if picked == "swap":
        fresh = rank_references(p.items(), p.brief, exclude=set(p.data["shown"]))
        if fresh:
            host.say("Drafting again with new references.")
            draft = draft_post(host, p.brief, p.brand, fresh, pick_hook_shapes(p.brief.shipped))
            hook = _pick_hook(host, list(draft.hooks), draft.caption, fresh, p.data["used_pack"])
            p.set_refs(fresh)
            p.set_draft(draft)
            p.data["hook"] = hook
        else:
            notes.append(NO_MORE_REFS)
    else:
        p.data["hook"] = _pick_hook(host, p.data["hooks"], p.data["caption"], p.refs, p.data["used_pack"])
    if not notes:
        p.render(host)
    host.state_set(PENDING_KEY, p.data)
    return p.reply(host, notes)


# ------------------------------------------------------------------ the command

def _brand_command(host: Host, url: str, get: Callable[[str], bytes]) -> str:
    if not url:
        return "Usage: /launch brand https://yoursite.com"
    stored = _kit_from(host.state_get("brand"))
    root = host.data_dir() / "work"
    root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="brand-", dir=root))
    try:
        kit = _read_brand(host, url, get, work)
        if stored is not None and _hostname(stored.url) == _hostname(url):
            kit = replace(kit, voice_samples=stored.voice_samples)
        kit = _store_brand(host, kit)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return (f"Saved the brand for {kit.name or _hostname(url)}: accent {kit.accent}. "
            "Next: /launch <what you shipped>.")


def _voice_command(host: Host, text: str) -> str:
    """Keep a past post as a voice sample (the last three), or forget them all."""
    if not text:
        return VOICE_USAGE
    stored = _kit_from(host.state_get("brand"))
    if stored is None:
        return VOICE_NO_BRAND
    if text.lower() == "clear":
        _store_brand(host, replace(stored, voice_samples=()))
        return "Voice samples cleared."
    kept = [s for s in stored.voice_samples if s != text] + [text]
    kit = _store_brand(host, replace(stored, voice_samples=tuple(kept[-VOICE_MAX:])))
    return (f"Kept as a voice sample ({len(kit.voice_samples)} of {VOICE_MAX}). "
            "Your next drafts follow its style.")


def _went_wrong(where: str, line: str) -> str:
    return f"Something went wrong while {where}: {line}. Nothing was posted or saved."


def _guarded(fn: Callable[[], str], on_stop: str, after: str = "") -> str:
    """Run one phase; a stop or any error becomes one plain reply, never a raise."""
    try:
        return fn()
    except _Stop:
        return on_stop
    except DraftError:
        logger.exception("launch-desk: drafting failed")
        return _went_wrong("drafting", DRAFT_FAILED) + after
    except Exception:
        logger.exception("launch-desk: /launch failed")
        return _went_wrong("making your pack", "an unexpected error, with details in the Hermes log") + after


def _run(host: Host, raw: str, get: Callable[[str], bytes]) -> str:
    if _unattended(host):
        return UNATTENDED
    text = raw.strip()
    if text.lower() in _HELP_WORDS:
        return HELP
    first, _, rest = text.partition(" ")
    if first.lower() == "brand" and (not rest.strip() or _URL_RE.fullmatch(rest.strip())):
        return _guarded(lambda: _brand_command(host, rest.strip(), get), STOPPED)
    voice = _VOICE_RE.match(text)
    if voice:
        return _guarded(lambda: _voice_command(host, voice.group(1).strip()), STOPPED)
    if not text:
        pending = _load_pending(host)
        if pending is None:
            return USAGE
        return _guarded(lambda: _resume(host, pending), WAITING,
                        after=" Your draft is still waiting; send /launch to pick it up.")
    args = parse_args(text)
    if not args.what:
        return USAGE
    stored = _kit_from(host.state_get("brand"))
    if stored is None and not args.url:
        return USAGE
    return _guarded(lambda: _start(host, args, stored, get), STOPPED)


def run_launch(host: Host, raw_args: str = "", get: Callable[[str], bytes] = http_get) -> str:
    """The /launch command: the chat reply, images as MEDIA:<path> lines. Never raises."""
    try:
        return _run(host, raw_args or "", get)
    except Exception:
        logger.exception("launch-desk: /launch failed")
        return _went_wrong("making your pack", "an unexpected error, with details in the Hermes log")
