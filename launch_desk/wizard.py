"""/launch: from "I just shipped X" to an approved launch pack.

The flow asks before it works: what kind of launch and where (so a session that cannot
answer stops before any network call), then the brand, the setup check, three
references, the draft, the hook, the card, and an approve loop of at most three rounds.
Nothing is saved until the founder approves; nothing is ever posted.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
import urllib.parse
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable

from .brand import NEUTRAL, PRESET_ACCENTS, fetch_brand, http_get
from .draft import DraftError, draft_post, facts_text, unsupported_details
from .hooks import pick_hook_shapes
from .host import Host, SourceUnavailable
from .models import BrandKit, Brief, Draft, Failure, Reference
from .pack import post_text, write_pack
from .references import McpSource, SamplePackSource, build_search_args, rank_references
from .render import render_card, render_feed_preview
from .rules import check
from .setup_check import check_setup

logger = logging.getLogger(__name__)

X_CARD = (1200, 675)
HEADLINE_MAX = 70
MAX_ROUNDS = 3

EXAMPLE = "/launch https://yoursite.com dark mode for the editor -- people kept asking for it"
USAGE = f"Usage: /launch <what you shipped> [-- <one real detail>], for example: {EXAMPLE}"
HELP = "\n".join((
    "/launch <what you shipped> [-- <one real detail>]",
    f"Example: {EXAMPLE}",
    "Add your site's link the first time; after that it is remembered.",
    "The part after -- is one true detail, like who asked for it. The draft uses it as a fact.",
    "/launch brand <url> reads your site's colours and logo again.",
    "Nothing is ever posted. You get a pack to post yourself.",
))
UNATTENDED = "/launch needs you to answer a few questions; run it in a chat."
STOPPED = "Stopped: nothing was saved."
DISCARDED = "Discarded: nothing was saved."

SHIPPED = [
    {"value": "feature", "label": "A new feature"},
    {"value": "product", "label": "A new product"},
    {"value": "rebrand", "label": "A rebrand"},
    {"value": "partnership", "label": "A partnership"},
]
NETWORKS = [{"value": "x", "label": "X"}, {"value": "linkedin", "label": "LinkedIn (soon)"}]
ACCENT_NAMES = ("Blue", "Orange", "Green", "Pink", "Violet")
APPROVE = {"value": "approve", "label": "Approve"}
CHANGE_HOOK = {"value": "hook", "label": "Change hook"}
SWAP = {"value": "swap", "label": "Swap references"}
DISCARD = {"value": "discard", "label": "Discard"}

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_DETAIL_SPLIT = re.compile(r"(?:^|\s)--(?:\s|$)")
_HELP_WORDS = {"help", "-h", "--help", "?"}


class _Stop(Exception):
    """The founder did not answer, or chose to stop: end the run, save nothing."""


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


def _site_key(url: str) -> tuple[str, str]:
    p = urllib.parse.urlsplit(url.strip())
    host = (p.hostname or "").lower()
    return (host[4:] if host.startswith("www.") else host), p.path.rstrip("/")


def _hostname(url: str) -> str:
    return _site_key(url)[0] or url


def _cut(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    head = text[: limit + 1]
    cut = head.rsplit(" ", 1)[0] if " " in head else text[:limit]
    return cut.rstrip(" ,;:-–—")


def card_headline(hook: str, what: str) -> str:
    """The hook when it fits on the card (70 characters), else what shipped, cut at a word."""
    hook = " ".join(hook.split())
    return hook if hook and len(hook) <= HEADLINE_MAX else _cut(what, HEADLINE_MAX)


# ------------------------------------------------------------------ host helpers

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


def _screenshot(host: Host) -> Path | None:
    value = host.state_get("screenshot")
    if isinstance(value, str) and value:
        path = Path(value)
        if path.is_file():
            return path
    return None


# ------------------------------------------------------------------ brand

def _load_brand(host: Host) -> BrandKit | None:
    data = host.state_get("brand")
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
    data = asdict(kit)
    data["voice_samples"] = list(kit.voice_samples)
    host.state_set("brand", data)
    return kit


def _read_brand(host: Host, url: str, get: Callable[[str], bytes], work: Path) -> BrandKit:
    """Read the site, then let the founder keep its accent or pick a preset."""
    site = _hostname(url)
    host.say(f"Reading {site} for its colours and logo.")
    kit = fetch_brand(url, get, work / "brand")
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

class _Pool:
    """One search per run; swaps re-rank the same items without repeats."""

    def __init__(self, host: Host, brief: Brief, offline: bool):
        self.brief = brief
        self.shown: set[str] = set()
        self.items: list[dict] | None = None
        args = build_search_args(brief)
        if not offline:
            try:
                items = McpSource(host).search(args)
                if len(rank_references(items, brief)) >= 3:
                    self.items = items
            except SourceUnavailable:
                pass
        self.used_pack = self.items is None
        if self.items is None:
            self.items = SamplePackSource().search(args)

    def pick(self) -> list[Reference]:
        refs = rank_references(self.items, self.brief, exclude=self.shown)
        self.shown |= {r.id for r in refs}
        return refs


def _refs_block(refs: list[Reference], used_pack: bool) -> str:
    head = "Three launch posts that worked" + (" (from the sample pack)" if used_pack else "") + ":"
    lines = [head]
    for n, r in enumerate(refs, 1):
        lines.append(f"{n}. {r.brand or 'A brand'}: {r.why}. {r.url}")
    return "\n".join(lines)


# ------------------------------------------------------------------ hook, card, approve

def _pick_hook(host: Host, draft: Draft, refs: list[Reference], used_pack: bool) -> str:
    body = "\n".join(draft.caption.strip().splitlines()[1:]).strip()
    question = _refs_block(refs, used_pack) + "\n\nPick the hook, the first line of your post."
    if body:
        question += "\nThe rest of the post:\n\n" + body
    choices = [{"value": str(n), "label": h} for n, h in enumerate(draft.hooks, 1)]
    picked = _ask(host, question, choices)
    return draft.hooks[int(picked) - 1]


def _flagged(text: str, brief: Brief, brand: BrandKit, refs: list[Reference]) -> list[Failure]:
    """Scroll-test failures of the post as it will be sent (chosen hook in place)."""
    found = check(text, network=brief.network, reference_captions=[r.caption for r in refs],
                  voice_samples=brand.voice_samples)
    return found + unsupported_details(text, facts_text(brief, brand))


def _render(host: Host, brand: BrandKit, shot: Path | None, hook: str, text: str, brief: Brief,
            out: Path) -> tuple[Path, Path]:
    host.say("Drawing the card.")
    card = render_card(brand, shot, card_headline(hook, brief.what), X_CARD, out / "card-x.png")
    preview = render_feed_preview(brand, text, card, out / "preview-x.png")
    return card, preview


def _approve_question(text: str, headline: str, flagged: list[Failure]) -> str:
    lines = ["Your post for X:", "", text, ""]
    if headline != text.splitlines()[0].strip():
        lines.append(f"Card headline: {headline}")
    if flagged:
        lines.append("Still flagged: " + " ".join(f.message for f in flagged))
    lines += ["", "Approve this pack?"]
    return "\n".join(lines)


def _reply(pack: Path, has_preview: bool) -> str:
    data = json.loads((pack / "pack.json").read_text(encoding="utf-8"))
    text = (pack / data["files"]["post"]).read_text(encoding="utf-8").strip()
    lines = [
        "Your launch pack is ready. Nothing was posted.",
        "",
        text,
        "",
        f"Post it on X with this text filled in: {data['intent_url']}",
        f"Pack folder: {pack}",
        "Read it once and change anything that isn't true.",
        "To edit the text, change it in X's compose box or in post-x.txt.",
        f"MEDIA:{pack / data['files']['card_x']}",
    ]
    if has_preview:
        lines.append(f"MEDIA:{pack / data['files']['preview']}")
    return "\n".join(lines)


# ------------------------------------------------------------------ the run

def _launch(host: Host, args: Args, stored: BrandKit | None, get: Callable[[str], bytes],
            work: Path) -> str:
    shipped = _ask(host, "What kind of launch is this?", SHIPPED)
    network = _ask(host, "Where will you post it?", NETWORKS)
    if network != "x":
        host.say("LinkedIn isn't ready yet, so this pack is for X.")

    new_brand = None
    if args.url and (stored is None or _site_key(args.url)[0] != _site_key(stored.url)[0]):
        brand = new_brand = _read_brand(host, args.url, get, work)
    else:
        brand = stored
    brief = Brief(shipped, "x", args.what, args.url or brand.url, detail=args.detail)

    issues = [i for i in check_setup(lambda: host.mcp("get_my_access", {})) if i.code != "unreachable"]
    if issues:
        host.say(f"{issues[0].message}\n{issues[0].command}")

    shot = _screenshot(host)
    line = f"Finding launch posts like yours for {brand.name or _hostname(brand.url)}."
    if shot is None:
        line += " Tip: send a screenshot and ask me to use it for the launch; the card is better with one."
    host.say(line)
    pool = _Pool(host, brief, offline=bool(issues))
    if pool.used_pack:
        host.say("posts.design isn't available, so these come from the sample pack of launch posts.")
    refs = pool.pick()

    shapes = pick_hook_shapes(brief.shipped)
    host.say("Drafting the post.")
    draft = draft_post(host, brief, brand, refs, shapes)
    hook = _pick_hook(host, draft, refs, pool.used_pack)

    for round_no in range(1, MAX_ROUNDS + 1):
        text = post_text(draft, hook)
        card, preview = _render(host, brand, shot, hook, text, brief, work / f"round-{round_no}")
        last = round_no == MAX_ROUNDS
        choices = [APPROVE, DISCARD] if last else [APPROVE, CHANGE_HOOK, SWAP, DISCARD]
        question = _approve_question(text, card_headline(hook, brief.what), _flagged(text, brief, brand, refs))
        picked = _ask(host, question, choices)
        if picked == "discard":
            return DISCARDED
        if picked == "approve":
            if new_brand is not None:
                _store_brand(host, new_brand)
            pack = write_pack(host.data_dir() / "packs", brief, brand, draft, hook, {"x": card}, refs,
                              has_screenshot=shot is not None, preview=preview,
                              headline=card_headline(hook, brief.what))
            return _reply(pack, has_preview=True)
        if picked == "swap":
            fresh = pool.pick()
            if fresh:
                refs = fresh
                host.say("Drafting again with these references.")
                draft = draft_post(host, brief, brand, refs, shapes)
            else:
                host.say("There are no other references to swap in, so these stay.")
        hook = _pick_hook(host, draft, refs, pool.used_pack)
    return STOPPED  # not reached: the last round offers only Approve and Discard


def _brand_command(host: Host, url: str, get: Callable[[str], bytes]) -> str:
    if not url:
        return "Usage: /launch brand https://yoursite.com"
    work = Path(tempfile.mkdtemp(prefix="run-", dir=_work_root(host)))
    try:
        kit = _store_brand(host, _read_brand(host, url, get, work))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return (f"Saved the brand for {kit.name or _hostname(url)}: accent {kit.accent}. "
            "Next: /launch <what you shipped>.")


def _work_root(host: Host) -> Path:
    root = host.data_dir() / "work"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _run(host: Host, raw: str, get: Callable[[str], bytes]) -> str:
    if _unattended(host):
        return UNATTENDED
    text = raw.strip()
    if text.lower() in _HELP_WORDS:
        return HELP
    first, _, rest = text.partition(" ")
    if first.lower() == "brand" and (not rest.strip() or _URL_RE.fullmatch(rest.strip())):
        return _brand_command(host, rest.strip(), get)
    args = parse_args(text)
    if not args.what:
        return USAGE
    stored = _load_brand(host)
    if stored is None and not args.url:
        return USAGE
    work = Path(tempfile.mkdtemp(prefix="run-", dir=_work_root(host)))
    try:
        return _launch(host, args, stored, get, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _went_wrong(line: str) -> str:
    return f"Something went wrong while drafting: {line}. Nothing was posted or saved."


def run_launch(host: Host, raw_args: str = "", get: Callable[[str], bytes] = http_get) -> str:
    """The /launch command: the chat reply, with the card and preview as MEDIA:<path> lines.
    Never raises."""
    try:
        return _run(host, raw_args or "", get)
    except _Stop:
        return STOPPED
    except DraftError:
        logger.exception("launch-desk: drafting failed")
        return _went_wrong("the model did not return a usable draft; run /launch again, or try a larger model")
    except Exception:
        logger.exception("launch-desk: /launch failed")
        return _went_wrong("an unexpected error, with details in the Hermes log")
