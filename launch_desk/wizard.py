"""/launch: from "I just shipped X" to an approved launch pack, in two phases.

Phase 1, `/launch <what> [-- <detail>]`: a few questions (asked before any network call,
so a session that cannot answer stops at once), the brand, the setup check, three
references, the draft, the hook, then the card and the feed preview. The reply shows
them, and the draft waits in state under "pending" (24 hours) with its files in
data_dir/pending/<id>/.

Phase 2, `/launch` alone: Approve, Change hook, Swap references or Discard. Approve
writes the pack, then asks whether to check in a day and three days later; only a Yes
creates those two jobs on Hermes' scheduler (see followup.py). Nothing is ever posted.
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

from . import followup
from .brand import NEUTRAL, PRESET_ACCENTS, fetch_brand, font_face, http_get
from .draft import UNSUPPORTED, DraftError, checklist, draft_post, hook_options, post_failures, quote_phrases
from .hooks import pick_hook_shapes
from .host import Host, SourceUnavailable
from .models import BrandKit, Brief, CheckLine, Draft, HookOption, Reference
from .pack import post_text, write_pack
from .references import (CATEGORY, McpSource, SamplePackSource, build_search_args, is_own,
                         rank_references, reference_line, reference_where, with_own)
from .render import choose_layout, render_card, render_feed_preview
from .rules import relaxed_rules
from .setup_check import check_setup

logger = logging.getLogger(__name__)

X_CARD = (1200, 675)
HEADLINE_MAX = 70
PENDING_KEY = "pending"
PENDING_TTL_S = 24 * 60 * 60
VOICE_MAX = 3
SCREENSHOTS_MAX = 2

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
CHECKIN_QUESTION = "Your launch pack is saved.\nCheck in with you tomorrow and in 3 days?"
CHECKIN_CHOICES = [{"value": "yes", "label": "Yes"}, {"value": "no", "label": "No"}]
CHECKIN_WHEN = ("tomorrow", "in 3 days")
YOUR_WORDS = "(your words)"
HAS_ISSUES = "(has issues)"
CHECKED = "Checked in code:"
PASS_MARK, FAIL_MARK = "\u2713", "\u2717"
# What the scroll test caught, as the founder reads it in "Caught in the first draft: ...".
CAUGHT = {
    "first-line-short": "a first line over 12 words",
    "stock-phrase": "a stock phrase",
    "template-opener": "the \"Your X should\" opener",
    "em-dash": "too many em-dashes",
    "emoji": "too many emoji",
    "hashtags": "too many hashtags",
    "placeholder": "a placeholder",
    "link-first-line": "a link in the first line",
    "private-link": "a private link",
    "length": "a post too long for X",
    "copied-run": "five words copied from a reference",
    "repeats-first-line": "a line repeating the first",
    "unsupported-detail": "details you didn't give",
}
_QUOTE = {"stock-phrase", "template-opener", "placeholder", "copied-run"}
FOUND_MAX = 40
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


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def card_headline(hook: str, what: str) -> str:
    """The hook when it fits on the card (70 characters), else what shipped, cut at a word."""
    hook = " ".join(hook.split())
    return _cap(hook if hook and len(hook) <= HEADLINE_MAX else _cut(what, HEADLINE_MAX))


# ------------------------------------------------------------------ host helpers

LOCAL_SURFACES = {"cli", "tui", "local", "desktop", "acp"}


def _local(host: Host) -> bool:
    """A terminal or desktop client, where long links and paths can be clicked or copied."""
    try:
        return host.surface() in LOCAL_SURFACES
    except Exception:
        return True


def _tell(host: Host, notes: list[str], line: str) -> None:
    """A line the founder must see once: in the reply on a local client, where it arrives
    together with the progress lines; as a message on a chat app, where the reply comes later."""
    if _local(host):
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


def screenshot_paths(value: Any) -> list[str]:
    """The attached-screenshot paths in state, oldest first. Older versions kept one path as a
    string; this version keeps a list of up to SCREENSHOTS_MAX."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, str) and v]


def _screenshots(value: Any) -> list[str]:
    """The attached screenshots that still exist, at most the newest SCREENSHOTS_MAX."""
    return [p for p in screenshot_paths(value) if Path(p).is_file()][-SCREENSHOTS_MAX:]


def _names(paths: list[str]) -> str:
    return " and ".join(Path(p).name for p in paths)


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
            font_face=str(data.get("font_face") or ""),
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


def _refresh_brand(host: Host, stored: BrandKit, get: Callable[[str], bytes], out: Path) -> BrandKit:
    """A kit saved before Task 20 (no face, and maybe a light background for a dark site):
    read the site once more and keep the result, with the founder's voice samples. If the
    site cannot be read, keep the stored colours and fill in the face from its font family,
    so it is not read again."""
    host.say(f"Reading {_hostname(stored.url)} again for its colours, dark or light, and its type.")
    fresh = fetch_brand(stored.url, get, out)
    if not fresh.font_face:  # the read failed: fetch_brand fell back to neutral colours
        kit = replace(stored, font_face=font_face(stored.font_family))
    else:
        kit = replace(_read_brand(host, stored.url, get, out, kit=fresh), voice_samples=stored.voice_samples)
    return _store_brand(host, kit)


def _read_brand(host: Host, url: str, get: Callable[[str], bytes], out: Path,
                kit: BrandKit | None = None) -> BrandKit:
    """Read the site (unless `kit` is that read), then let the founder keep its accent or
    pick a preset."""
    site = _hostname(url)
    if kit is None:
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

_SECRET_RE = re.compile(
    r"(?i)\b(bearer|basic|token|access_token|api[_-]?key|key|secret|password|authorization)(\s*[:=]\s*|\s+)\S+")
_LONG_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-.~+/=]{24,}")
REASON_MAX = 200


def _redact(text: str) -> str:
    """An error message for the log: credentials and long opaque tokens out, one line, short."""
    text = _SECRET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]", " ".join(str(text).split()))
    text = _LONG_TOKEN_RE.sub("[redacted]", text)
    return text[:REASON_MAX]


def _search(host: Host, brief: Brief, offline: bool, setup_issue: str = "") -> tuple[list[dict], str]:
    """The candidate items for this draft, and a note when they come from the sample pack.
    A fallback is logged with its reason (R39): the setup issue, the source error's kind and
    message (secrets removed), or how few distinct-brand matches came back."""
    args = build_search_args(brief)
    note = PACK_UNAVAILABLE
    if offline:
        reason = f"setup issue ({setup_issue or 'unknown'})"
    else:
        try:
            items = McpSource(host).search(args)
            found = len(rank_references(items, brief))
            if found >= 3:
                return items, ""
            note = PACK_TOO_FEW
            reason = f"fewer than 3 distinct-brand matches ({found} from {len(items)} items)"
        except SourceUnavailable as exc:
            reason = f"posts.design unavailable ({getattr(exc, 'kind', 'error')}: {_redact(str(exc)) or 'no message'})"
    logger.warning("launch-desk: sample pack used: %s", reason)
    return SamplePackSource().search(args), note


def _refs_block(refs: list[Reference], used_pack: bool) -> str:
    head = "Three launch posts that worked" + (" (from the sample pack)" if used_pack else "") + ":"
    lines = [head]
    for n, r in enumerate(refs, 1):
        lines.append(f"{n}. {reference_line(r)} {reference_where(r)}")
    if refs:
        lines.append("The draft borrows the shape of 1, not its words.")
    return "\n".join(lines)


def _pick_hook(host: Host, options: list[HookOption], caption: str, refs: list[Reference],
               used_pack: bool, lead: str = "") -> int:
    """Ask which hook leads; the index of the picked option. The founder's words are marked
    as theirs (the mark is not part of the post), and a body line one option leaves out is named.
    `lead`, if any, opens the question."""
    body = "\n".join(caption.strip().splitlines()[1:]).strip()
    question = (lead + "\n\n" if lead else "") + _refs_block(refs, used_pack)
    question += "\n\nPick the hook, the first line of your post."
    if body:
        question += "\nThe rest of the post:\n\n" + body
    for n, o in enumerate(options, 1):
        for line in o.dropped:
            question += f"\n\nWith hook {n}, this line comes out, since it repeats those words: {line}"
    def label(o: HookOption) -> str:
        mark = YOUR_WORDS if o.source == "founder" else HAS_ISSUES if o.flagged else ""
        return f"{o.text} {mark}" if mark else o.text

    choices = [{"value": str(n), "label": label(o)} for n, o in enumerate(options, 1)]
    picked = _ask(host, question, choices)
    return int(picked) - 1


def caught_line(draft: Draft) -> str:
    """One line naming what the redraft fixed, quoting the phrase where the rule knows it; "" when
    there was no redraft or it fixed nothing."""
    if not draft.redrafted:
        return ""
    still = {f.rule_id for f in draft.failures}
    groups: dict[str, list[str]] = {}
    for f in draft.first_failures:
        base = f.rule_id.split(":", 1)[-1]
        if f.rule_id in still or base not in CAUGHT:
            continue
        found = groups.setdefault(base, [])
        if f.found:
            cut = replace(f, found=_cut(f.found, FOUND_MAX))  # cut before quoting: no quote left open
            shown = quote_phrases(cut) if base in _QUOTE or base == UNSUPPORTED else cut.found
            if shown.lower() not in (x.lower() for x in found):
                found.append(shown)
    if not groups:
        return ""
    parts = [CAUGHT[b] + (f" ({', '.join(found)})" if found else "") for b, found in groups.items()]
    joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return f"Caught in the first draft: {joined}. Redrafted."


def _caught(host: Host, draft: Draft, notes: list[str]) -> str:
    """Show the caught line before the hook question. A chat app gets it as a message now. A
    terminal or desktop client shows no progress messages, so there it opens the hook question
    (returned) and goes in the reply's notes."""
    line = caught_line(draft)
    if not line:
        return ""
    if _local(host):
        notes.append(line)
        return line
    host.say(line)
    return ""


def _check_lines(checks: list[CheckLine]) -> list[str]:
    return [CHECKED] + [f"{PASS_MARK if c.ok else FAIL_MARK} {c.text}" for c in checks]


# ------------------------------------------------------------------ the pending draft

class _Pending:
    """The draft waiting for /launch: plain data in state, files in data_dir/pending/<id>/."""

    def __init__(self, data: dict):
        self.data = json.loads(json.dumps(data))  # a copy: state changes only on state_set
        self.dir = Path(data["dir"])
        self.brief = Brief(**data["brief"])
        self.brand = _kit_from(data["brand"]) or NEUTRAL
        self.refs = [Reference(**r) for r in data["refs"]]

    def options(self) -> list[HookOption]:
        """The hooks offered, each with its post. A draft saved before hook options were kept
        has only hooks: each is a model hook in place of the caption's first line."""
        hooks, caption = self.data["hooks"], self.data["caption"]
        posts = self.data.get("posts")
        if not (isinstance(posts, list) and len(posts) == len(hooks)):
            plain = Draft(tuple(hooks), caption, (), False)
            return [HookOption(h, "model", post_text(plain, h)) for h in hooks]
        sources = self.data.get("sources") or ["model"] * len(hooks)
        dropped = self.data.get("dropped") or [[] for _ in hooks]
        flagged = self.data.get("flagged") or [False] * len(hooks)
        return [HookOption(h, s if s == "founder" else "model", p, tuple(d), bool(f))
                for h, s, p, d, f in zip(hooks, sources, posts, dropped, flagged)]

    def set_options(self, options: list[HookOption], draft: Draft) -> None:
        self.data.update(hooks=[o.text for o in options], sources=[o.source for o in options],
                         posts=[o.post for o in options], dropped=[list(o.dropped) for o in options],
                         flagged=[o.flagged for o in options], caption=draft.caption,
                         use_case=draft.use_case)

    @property
    def option(self) -> HookOption:
        """The picked hook's option (the first, if the hook is not among them)."""
        options = self.options()
        return next((o for o in options if o.text == self.data["hook"]), options[0])

    @property
    def text(self) -> str:
        return self.option.post

    @property
    def draft(self) -> Draft:
        """The draft as it will be posted: its caption is the picked option's post."""
        return Draft(tuple(self.data["hooks"]), self.text, (), False)

    def checks(self) -> list[CheckLine]:
        text = self.text
        return checklist(text, post_failures(text, self.brief, self.brand, self.refs), self.brief.network,
                         use_case=str(self.data.get("use_case") or "") if self.option.source == "founder" else "")

    def items(self) -> list[dict]:
        try:
            return json.loads((self.dir / "items.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def set_refs(self, refs: list[Reference]) -> None:
        self.refs = refs
        self.data["refs"] = [asdict(r) for r in refs]
        self.data["shown"] = sorted(set(self.data["shown"]) | {r.id for r in refs})

    def render(self, host: Host) -> None:
        """Draw the card and the preview into a new round folder; drop the previous one."""
        host.say("Drawing the card.")
        n = int(self.data.get("round") or 0) + 1
        out = self.dir / f"r{n}"
        shots = [Path(p) for p in _screenshots(_used_screenshots(self.data))]
        headline = card_headline(self.data["hook"], self.brief.what)
        layout = choose_layout(shots, headline)  # again on each render: a new hook can change it
        card = render_card(self.brand, shots, headline, X_CARD, out / "card-x.png", layout=layout)
        preview = render_feed_preview(self.brand, self.text, card, out / "preview-x.png")
        old = self.dir / f"r{n - 1}"
        if n > 1 and old.is_dir():
            shutil.rmtree(old, ignore_errors=True)
        self.data.update(round=n, card=str(card), preview=str(preview), layout=layout)

    def reply(self, host: Host, notes: list[str]) -> str:
        lines = [*notes, ""] if notes else []
        text = self.text
        lines += ["Your draft for X:", "", text, "", *_check_lines(self.checks()), ""]
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


def _used_screenshots(data: dict) -> list[str]:
    """The screenshot paths a draft was made with (older drafts kept one, as "screenshot")."""
    return screenshot_paths(data.get("screenshots", data.get("screenshot")))


def _forget_screenshot(host: Host, data: Any) -> None:
    """Take the screenshots a draft used out of the attached-screenshot state; any attached
    after the draft stay for the next /launch."""
    used = set(_used_screenshots(data)) if isinstance(data, dict) else set()
    current = screenshot_paths(host.state_get("screenshot"))
    kept = [p for p in current if p not in used]
    if kept != current:
        host.state_set("screenshot", kept or None)


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
        _forget_screenshot(host, data)
    return pending


# ------------------------------------------------------------------ phase 1

def _phase1(host: Host, args: Args, stored: BrandKit | None, get: Callable[[str], bytes],
            folder: Path, notes: list[str]) -> str:
    shipped = _ask(host, "What kind of launch is this?", SHIPPED)
    network = _ask(host, "Where will you post it?", NETWORKS)
    if network != "x":
        _tell(host, notes, LINKEDIN_LATER)

    brand_new = bool(args.url) and (stored is None or _hostname(args.url) != _hostname(stored.url))
    if brand_new:
        brand = _read_brand(host, args.url, get, folder / "brand")
    elif not stored.font_face:  # saved before dark sites and faces were read: read it again, once
        brand = _refresh_brand(host, stored, get, folder / "brand")
    else:
        brand = stored
    brief = Brief(shipped, "x", args.what, args.url or brand.url, detail=args.detail)

    issues = [i for i in check_setup(lambda: host.mcp("get_my_access", {})) if i.code != "unreachable"]
    if issues:
        _tell(host, notes, f"{issues[0].message}\n{issues[0].command}")
    best = _best_launch(host, brand.url)
    loop_line = followup.best_launch_line(best) if best else ""
    if loop_line:
        _tell(host, notes, loop_line)

    shots = _screenshots(host.state_get("screenshot"))
    line = f"Finding launch posts like yours for {brand.name or _hostname(brand.url)}."
    if not shots:
        line += " Tip: send a screenshot and ask me to use it for the launch; the card is better with one."
    else:
        used = f"Using the screenshot{'s' if len(shots) > 1 else ''} you attached: {_names(shots)}"
        line += f" {used}."
        notes.append(f"{used}.")
    host.say(line)
    items, pack_note = _search(host, brief, offline=bool(issues), setup_issue=issues[0].code if issues else "")
    if pack_note:
        host.say(pack_note)
        if not any(i.get("category") == CATEGORY[brief.shipped] for i in items):
            notes.append(f"There are no {brief.shipped} posts in the sample pack, so these are other launch posts.")
    refs = with_own(best, rank_references(items, brief))

    host.say("Drafting the post.")
    kept = [STYLE_WORDS[r] for r in sorted(relaxed_rules(brand.voice_samples)) if r in STYLE_WORDS]
    if kept:
        notes.append(f"Kept your style: your past posts use {' and '.join(kept)}, so the draft may too.")
    draft = draft_post(host, brief, brand, refs, pick_hook_shapes(brief.shipped, brief.detail))
    lead = _caught(host, draft, notes)
    if loop_line and _local(host):  # a terminal shows no progress messages: open the question with it
        lead = "\n".join(x for x in (loop_line, lead) if x)
    options = hook_options(draft, brief, brand, refs)
    picked = _pick_hook(host, options, draft.caption, refs, bool(pack_note), lead)

    (folder / "items.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    pending = _Pending({
        "id": folder.name, "dir": str(folder), "created_at": _now(),
        "brief": asdict(brief), "brand": _kit_dict(brand), "brand_new": brand_new,
        "refs": [asdict(r) for r in refs], "shown": sorted(r.id for r in refs),
        "used_pack": bool(pack_note), "hook": options[picked].text,
        "screenshots": shots, "round": 0,
    })
    pending.set_options(options, draft)
    pending.render(host)
    host.state_set(PENDING_KEY, pending.data)
    return pending.reply(host, notes)


def _best_launch(host: Host, site: str):
    """The founder's best past launch for this site with recorded sign-ups, if any. Never raises."""
    try:
        return followup.best_launch(host.data_dir() / "packs", site=site)
    except Exception:
        logger.warning("launch-desk: past results skipped", exc_info=True)
        return None


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

def _checkin_lines(made: list[dict], local: bool) -> list[str]:
    if not made:
        return ["The check-ins could not be scheduled, so there are none."]
    when = " and ".join(CHECKIN_WHEN[: len(made)])
    lines = [f"I'll check in {when} to ask how many sign-ups each channel brought."]
    if len(made) < len(CHECKIN_WHEN):
        lines.append("The later check-in could not be scheduled.")
    if any(str(m.get("deliver") or "") == "local" for m in made):
        lines.append("This chat can't receive them, so Hermes keeps their messages: hermes cron list shows them.")
    ids = [str(m["job_id"]) for m in made]
    if local:
        lines.append("To cancel: " + ", ".join(f"/cron remove {i}" for i in ids))
    else:  # /cron is a CLI command; a chat app cancels through the agent or a terminal
        lines.append(f"To cancel, ask me to remove check-in {' and '.join(ids)}, "
                     "or run hermes cron remove <id> where Hermes runs.")
    return lines


def _offer_checkins(host: Host, pack_name: str) -> list[str]:
    """Ask once, after the pack is written. Only a Yes creates the jobs; No, no answer or an
    unattended run creates nothing. Never raises: the pack is already saved."""
    if _unattended(host):
        return []
    try:
        if host.choose(CHECKIN_QUESTION, CHECKIN_CHOICES) != "yes":
            return []
    except Exception:
        logger.exception("launch-desk: check-in question failed")
        return []
    made: list[dict] = []
    try:
        for spec in followup.job_specs(pack_name):
            reply = host.schedule(spec)
            if not (isinstance(reply, dict) and reply.get("success") and reply.get("job_id")):
                logger.warning("launch-desk: check-in not scheduled: %s", repr(reply)[:1000])
                break
            made.append(reply)
    except Exception:
        logger.exception("launch-desk: scheduling a check-in failed")
    return _checkin_lines(made, _local(host))


def _unshown_refs(p: _Pending) -> list[Reference]:
    """The references the founder never saw, for check-ins; none if the items can't be read."""
    try:
        return rank_references(p.items(), p.brief, exclude=set(p.data["shown"]))
    except Exception:
        logger.warning("launch-desk: unshown references skipped", exc_info=True)
        return []


def _approve(host: Host, p: _Pending) -> str:
    more = _unshown_refs(p)
    pack = write_pack(host.data_dir() / "packs", p.brief, p.brand, p.draft, p.data["hook"],
                      {"x": Path(p.data["card"])}, p.refs,
                      has_screenshot=bool(_screenshots(_used_screenshots(p.data))),
                      preview=Path(p.data["preview"]),
                      headline=card_headline(p.data["hook"], p.brief.what), more_refs=more,
                      layout=p.data.get("layout"), hook_source=p.option.source,
                      checks=[asdict(c) for c in p.checks()])
    if p.data.get("brand_new"):
        _store_brand(host, p.brand)
    _drop_pending(host, p.data)
    _forget_screenshot(host, p.data)
    data = json.loads((pack / "pack.json").read_text(encoding="utf-8"))
    text = (pack / data["files"]["post"]).read_text(encoding="utf-8").strip()
    checkins = _offer_checkins(host, pack.name)
    if _local(host):
        post_it = f"Post it on X with this text filled in: {data['intent_url']}"
        saved = f"Pack folder: {pack}"
    else:  # chat apps show a Markdown link as a short label, and the server path means nothing there
        post_it = f"[Open X with this post filled in]({data['intent_url']})"
        saved = f"Saved as {pack.name}"
    return "\n".join((
        "Your launch pack is ready. Nothing was posted.",
        "",
        text,
        "",
        post_it,
        saved,
        "Read it once and change anything that isn't true.",
        "To edit the text, change it in X's compose box or in post-x.txt.",
        *checkins,
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
        _forget_screenshot(host, p.data)
        return DISCARDED
    notes: list[str] = []
    caught: list[str] = []
    if picked == "swap":
        fresh = rank_references(p.items(), p.brief, exclude=set(p.data["shown"]))
        own = next((r for r in p.refs if is_own(r)), None)
        if fresh and own is not None:  # the founder's own launch stays reference 1
            fresh = [own] + fresh[:2]
        if fresh:
            host.say("Drafting again with new references.")
            draft = draft_post(host, p.brief, p.brand, fresh, pick_hook_shapes(p.brief.shipped, p.brief.detail))
            lead = _caught(host, draft, caught)
            options = hook_options(draft, p.brief, p.brand, fresh)
            picked = _pick_hook(host, options, draft.caption, fresh, p.data["used_pack"], lead)
            p.set_refs(fresh)
            p.set_options(options, draft)
            p.data["hook"] = options[picked].text
        else:
            notes.append(NO_MORE_REFS)
    else:
        options = p.options()
        p.data["hook"] = options[_pick_hook(host, options, p.data["caption"], p.refs, p.data["used_pack"])].text
    if not notes:
        p.render(host)
    host.state_set(PENDING_KEY, p.data)
    return p.reply(host, caught + notes)


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
