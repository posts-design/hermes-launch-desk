"""Check-ins after a launch: the scheduled jobs, and the two tools they and the founder use.

On the founder's Yes, /launch creates two one-off jobs on Hermes' own scheduler (a day and
three days after approve). Each job's agent calls launch_desk_followup and posts one message
to the chat the job came from: how many sign-ups did each channel bring, and one next post.
The message itself is composed here, from the pack's own fields, and kept inside the copy
checks; the agent only relays it. When the founder answers with numbers, the agent calls
launch_desk_record_results, which appends them to the pack's pack.json. Nothing here posts or
asks anything.

best_launch() closes the loop: the next /launch reads those results back, names the
founder's best launch so far and offers its post as reference 1.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .draft import founder_hook, same_line
from .models import PastLaunch
from .pack import JUDGE_BY_CONVERSION
from .references import short_brand
from .rules import _EMOJI_RE, _private_host, check

SKILL = "launch-desk:launch-desk"
CHECKIN_HOURS = (24, 72)
JOB_NAME = "Launch Desk check-in: {pack}"
# Fixed: only the pack name changes. Braces are only for {pack} (it is also the blueprint's
# prompt template).
PROMPT = (
    "Launch Desk check-in for the launch pack {pack}. "
    "Call the launch_desk_followup tool with pack set to {pack}, then reply with the message "
    "field of its result exactly, adding nothing. "
    "If the tool is missing or its result has ok false, reply with one line saying the check-in "
    "for {pack} could not run, and stop. Do not wait for an answer."
)
# The automation blueprint's check-in (Hermes Desktop "New cron job"): its pack slot may be the
# latest pack, a label from the suggestions, or a folder name; launch_desk_followup resolves it
# when the check-in runs (resolve_pack). The tool's own message covers every outcome, including
# "no pack yet", so the job's agent never improvises.
LATEST = "Your latest pack"
LABELS_MAX = 8
BLUEPRINT_PROMPT = (
    "Launch Desk check-in. Call the launch_desk_followup tool with pack set to \"{pack}\" exactly "
    "as written, then reply with the message field of its result exactly, adding nothing. "
    "If the tool is missing, reply with one line saying the Launch Desk check-in could not run, "
    "and stop. Do not wait for an answer."
)
NO_PACKS = "Launch Desk check-in: there is no launch pack yet, so there is nothing to check in on."
# no_mcp: Hermes' cron scheduler then attaches no MCP server to the job (else it adds every
# enabled one); a check-in needs only the plugin's own tools, never posts.design's.
TOOLSETS = ["launch_desk", "no_mcp"]

CHANNEL_MAX = 40
SIGNUPS_MAX = 1_000_000
SIGNUPS_DIGITS = 7
RESULTS_KEPT = 50
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}")
_DIGITS_RE = re.compile(r"[0-9]{1,%d}" % SIGNUPS_DIGITS)
# The copy checks a check-in message must pass (the rest are for X posts).
MESSAGE_RULES = frozenset({"em-dash", "emoji", "placeholder", "stock-phrase"})
QUESTION = "How many sign-ups did each channel bring? Reply with a number for each, like X 12."


def job_specs(pack: str) -> list[dict]:
    """The cronjob_manage create calls for one pack. No deliver target: Hermes posts to the
    chat the job was created from, or keeps the output local when there is none.
    attach_to_session puts the check-in in that chat's history, so a reply with numbers has it."""
    return [{
        "action": "create",
        "schedule": f"in {hours}h",
        "name": JOB_NAME.format(pack=pack),
        "prompt": PROMPT.format(pack=pack),
        "skills": [SKILL],
        "attach_to_session": True,
        "enabled_toolsets": list(TOOLSETS),
    } for hours in CHECKIN_HOURS]


# ------------------------------------------------------------------ reading a pack

class _Refused(Exception):
    pass


def _pack_file(root: Path, name: Any) -> Path:
    """data_dir/packs/<name>/pack.json, refusing any name that leaves the packs folder."""
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name) or ".." in name:
        raise _Refused("Unknown pack name.")
    base = Path(root).resolve()
    folder = (base / name).resolve()
    path = folder / "pack.json"
    if folder.parent != base or path.is_symlink() or not path.is_file():
        raise _Refused("Unknown pack name.")
    return path


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise _Refused("The pack could not be read.") from exc
    if not isinstance(data, dict):
        raise _Refused("The pack could not be read.")
    return data


def _strings(value: Any) -> list[str]:
    return [v for v in value if isinstance(v, str) and v.strip()] if isinstance(value, list) else []


def _dicts(value: Any) -> list[dict]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _suggestions(detail: str, unused_hooks: list[str], more_refs: list[dict]) -> list[dict]:
    """Up to three next posts, each taken from the pack as it is: a reply that carries the
    founder's detail, an unused hook as a follow-up post, the shape of an unused reference."""
    out: list[dict] = []
    if detail:
        out.append({"kind": "reply-with-detail", "detail": detail})
    if unused_hooks:
        out.append({"kind": "unused-hook", "hook": unused_hooks[0]})
    for ref in more_refs:
        if isinstance(ref.get("shape"), str) and ref["shape"].strip():
            out.append({"kind": "reference-shape", "brand": ref.get("brand"), "url": ref.get("url"),
                        "shape": ref["shape"], "views": ref.get("views")})
            break
    return out[:3]


# ------------------------------------------------------------------ the message

def _passes(text: str) -> bool:
    return not any(f.rule_id in MESSAGE_RULES for f in check(text, network="x"))


def _plain(text: Any) -> str:
    """Pack text for one line of the message: one line, no em-dash, emoji or brackets."""
    text = _EMOJI_RE.sub("", str(text or ""))
    text = re.sub(r"\s*[\u2014\u2013]\s*", ", ", text)
    text = re.sub(r"[\[\]{}]", "", text)
    return " ".join(text.split()).strip(" ,")


def _idea(s: dict) -> str:
    if s["kind"] == "reply-with-detail":
        return f"Reply under your launch post with the detail: {_plain(s['detail']).rstrip('.')}."
    if s["kind"] == "unused-hook":
        return f"Post a follow-up that opens with: {_plain(s['hook'])}"
    brand = _plain(short_brand(str(s.get("brand") or ""))) or "a brand"
    return f"Try the shape of {brand}'s post ({_plain(s['shape'])}): {s.get('url') or ''}".rstrip(": ")


def compose_message(pack: str, suggestions: list[dict]) -> str:
    """The whole check-in, from pack fields only: the question and up to two next-post
    ideas. An idea that would trip a copy check is left out."""
    head = [f"Launch Desk check-in for {pack}.", QUESTION]
    ideas: list[str] = []
    for s in suggestions:
        line = _idea(s)
        if line and _passes("\n".join(head + ideas + [line])):
            ideas.append(line)
        if len(ideas) == 2:
            break
    if ideas:
        head += ["", "Ideas for your next post:"] + [f"- {i}" for i in ideas]
    return "\n".join(head)


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def pack_label(name: str, data: dict) -> str:
    """A friendly name for a pack: "pawr.link · 7 Nov 02:06" (its brand, then when it was made,
    UTC); without created_at, the date in its folder name ("pawr.link · 5 Nov")."""
    brand = data.get("brand") if isinstance(data.get("brand"), dict) else {}
    who = " ".join(str(brand.get("name") or "").split()) or _pack_site(data) or name
    when = ""
    created = data.get("created_at")
    try:
        stamp = datetime.fromisoformat(created) if isinstance(created, str) and created.strip() else None
    except ValueError:
        stamp = None
    if stamp is not None:
        stamp = stamp.astimezone(timezone.utc) if stamp.tzinfo else stamp
        when = f"{stamp.day} {_MONTHS[stamp.month - 1]} {stamp:%H:%M}"
    else:
        m = _DATE_RE.match(name)
        if m:
            year, month, day = (int(x) for x in m.group(0).split("-"))
            when = f"{day} {_MONTHS[month - 1]}" if 1 <= month <= 12 else ""
    return f"{who} \u00b7 {when}" if when else who


def _packs_newest_first(root: Path) -> list[tuple[str, dict]]:
    """(folder name, pack.json) for every readable pack, newest first (created_at, else the date
    in the name; then the name). Never raises."""
    found = []
    try:
        folders = [p for p in Path(root).iterdir() if p.is_dir()]
    except OSError:
        return []
    for folder in folders:
        try:
            path = _pack_file(root, folder.name)
            if path.stat().st_size > PACK_READ_MAX:
                continue
            data = _load(path)
        except (_Refused, OSError):
            continue
        found.append((_made(data, folder.name), folder.name, data))
    found.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [(name, data) for _made_at, name, data in found]


def _labelled(root: Path) -> list[tuple[str, str]]:
    """(label, folder name) for every pack, newest first; a repeated label gets " (2)", " (3)"."""
    out, seen = [], {}
    for name, data in _packs_newest_first(root):
        label = pack_label(name, data)
        seen[label] = seen.get(label, 0) + 1
        out.append((label if seen[label] == 1 else f"{label} ({seen[label]})", name))
    return out


def recent_labels(root: Path, limit: int = LABELS_MAX) -> list[str]:
    """The newest packs' labels, newest first: the blueprint's suggestions."""
    return [label for label, _name in _labelled(root)[:limit]]


def resolve_pack(root: Path, value: Any) -> str | None:
    """The pack a check-in means: blank or LATEST is the newest pack, else a folder name or a
    label from recent_labels(). None when there is no pack at all; an unknown value comes back
    as it was, so followup() reports it."""
    if value is None or (isinstance(value, str) and value.strip().lower() in ("", LATEST.lower())):
        packs = _packs_newest_first(root)
        return packs[0][0] if packs else None
    if not isinstance(value, str):
        return value
    if _NAME_RE.fullmatch(value) and ".." not in value and (Path(root) / value / "pack.json").is_file():
        return value  # a folder name, exactly as written
    text = " ".join(value.split())
    return next((name for label, name in _labelled(root) if label == text), value)


def _failed(pack: Any, error: str) -> dict:
    name = pack if isinstance(pack, str) and _NAME_RE.fullmatch(pack) and ".." not in pack else "that pack"
    return {"ok": False, "error": error,
            "message": f"The Launch Desk check-in for {name} could not run: {error.lower().rstrip('.')}."}


def followup(root: Path, pack: Any) -> dict:
    """What a check-in needs, with the message ready to post. `pack` may be a folder name, a label
    or blank (the newest pack, resolve_pack). Never raises."""
    try:
        pack = resolve_pack(root, pack)
    except Exception:
        pack = None
    if pack is None:
        return {"ok": False, "error": "No launch pack yet.", "message": NO_PACKS}
    try:
        data = _load(_pack_file(root, pack))
    except _Refused as exc:
        return _failed(pack, str(exc))
    brief = data.get("brief") if isinstance(data.get("brief"), dict) else {}
    hook = data.get("hook") if isinstance(data.get("hook"), str) else ""
    detail = brief.get("detail") if isinstance(brief.get("detail"), str) else ""
    # The founder's words are hook 1 of every pack with a detail: never offer them again as a
    # hook, and when they lead the post, a reply with them would repeat its first line.
    words = founder_hook(detail)
    unused = [h for h in _strings(data.get("hooks"))
              if not same_line(h, hook) and not (words and same_line(h, words))]
    led = data.get("hook_source") == "founder" or bool(words and same_line(hook, words))
    more = _dicts(data.get("more_references"))
    suggestions = _suggestions("" if led else detail.strip(), unused, more)
    return {
        "ok": True,
        "pack": pack,
        "post": data.get("caption") or "",
        "intent_url": data.get("intent_url") or "",
        "unused_hooks": unused,
        "more_references": more,
        "after_post_check": {"id": "judge-by-conversion", "text": JUDGE_BY_CONVERSION},
        "results": _dicts(data.get("results")),
        "suggestions": suggestions,
        "message": compose_message(pack, suggestions),
    }


# ------------------------------------------------------------------ recording results

def _channel(value: Any) -> str:
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        raise _Refused("channel must be a short name, like X or LinkedIn.")
    text = " ".join(value.split())
    if not text or len(text) > CHANNEL_MAX:
        raise _Refused(f"channel must be 1 to {CHANNEL_MAX} characters.")
    return text


def _signups(value: Any) -> int:
    if isinstance(value, str):
        text = value.strip()
        if not _DIGITS_RE.fullmatch(text):
            raise _Refused(f"signups must be a whole number from 0 to {SIGNUPS_MAX:,}.")
        try:
            value = int(text)
        except ValueError as exc:
            raise _Refused("signups must be a whole number.") from exc
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= SIGNUPS_MAX:
        raise _Refused(f"signups must be a whole number from 0 to {SIGNUPS_MAX:,}.")
    return value


def _write_json(path: Path, data: dict) -> None:
    """Write next to the file, then replace it, so a crash never leaves half a pack.json."""
    fd, tmp = tempfile.mkstemp(prefix=".pack-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def record_results(root: Path, pack: Any, channel: Any, signups: Any) -> dict:
    """Append {channel, signups, recorded_at} to the pack's results. Never raises."""
    try:
        entry = {"channel": _channel(channel), "signups": _signups(signups)}
        path = _pack_file(root, pack)
        data = _load(path)
    except _Refused as exc:
        return {"ok": False, "error": str(exc)}
    entry["recorded_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    data["results"] = (_dicts(data.get("results")) + [entry])[-RESULTS_KEPT:]
    try:
        _write_json(path, data)
    except OSError:
        return {"ok": False, "error": "The pack could not be saved."}
    return {"ok": True, "pack": pack, "results": data["results"]}


# ------------------------------------------------------------------ past launches

FIRST_LINE_MAX = 90
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def channel_totals(results: Any) -> list[tuple[str, int]]:
    """(channel, sign-ups) from a pack's results, most first. A later answer for a channel
    (any case) replaces the earlier one, since each check-in asks for the count so far.
    Entries that record_results would not have written are skipped."""
    latest: dict[str, tuple[str, int]] = {}
    for entry in results if isinstance(results, list) else []:
        if not isinstance(entry, dict):
            continue
        channel, signups = entry.get("channel"), entry.get("signups")
        if not (isinstance(channel, str) and channel.strip() and len(channel) <= CHANNEL_MAX):
            continue
        if isinstance(signups, bool) or not isinstance(signups, int) or not 0 <= signups <= SIGNUPS_MAX:
            continue
        name = " ".join(channel.split())
        latest[name.lower()] = (name, signups)
    return sorted(latest.values(), key=lambda pair: -pair[1])


def _first_line(caption: str) -> str:
    line = next((ln.strip() for ln in caption.splitlines() if ln.strip()), "")
    if len(line) <= FIRST_LINE_MAX:
        return line
    cut = line[: FIRST_LINE_MAX - 1].rsplit(" ", 1)[0].rstrip(" ,;:.")
    return cut + "\u2026"


def _made(data: dict, name: str) -> str:
    """When the pack was made, as sortable text: its created_at, else the date in its name."""
    created = data.get("created_at")
    if isinstance(created, str) and created.strip():
        return created.strip()
    m = _DATE_RE.match(name)
    return m.group(0) if m else ""


PACK_READ_MAX = 1_000_000  # bytes; a pack.json the plugin writes is a few KB


def _site(url: Any) -> str:
    """The host of a URL, lower case, without www; with its port when the host is local or
    private (sites staged on one machine differ only by port). "" when it has no host."""
    if not isinstance(url, str):
        return ""
    try:
        parts = urllib.parse.urlsplit(url.strip())
        host, port = (parts.hostname or "").lower(), parts.port
    except ValueError:
        return ""
    host = host[4:] if host.startswith("www.") else host
    return f"{host}:{port}" if host and port and _private_host(host) else host


def _pack_site(data: dict) -> str:
    brand = data.get("brand") if isinstance(data.get("brand"), dict) else {}
    brief = data.get("brief") if isinstance(data.get("brief"), dict) else {}
    return _site(brand.get("url")) or _site(brief.get("product_url"))


def best_launch(root: Path, site: str | None = None) -> PastLaunch | None:
    """The founder's approved pack with the most recorded sign-ups (more than none); on a tie
    the most recent. With `site`, only packs for that site (same host) count, so another
    product's launch never speaks for this one; a `site` without a host matches nothing.
    `latest` is True only when no pack of the site with a post is more recent, whatever its
    results. None when no pack has results. Never raises."""
    want = _site(site) if site else ""
    if site and not want:
        return None
    found: list[tuple[int, str, str, PastLaunch]] = []
    newest: tuple[str, str] | None = None
    try:
        folders = sorted(p for p in Path(root).iterdir() if p.is_dir())
    except OSError:
        return None
    for folder in folders:
        try:
            path = _pack_file(root, folder.name)
            if path.stat().st_size > PACK_READ_MAX:
                continue
            data = _load(path)
        except (_Refused, OSError):
            continue
        if want and _pack_site(data) != want:
            continue
        caption = data.get("caption")
        if not (isinstance(caption, str) and caption.strip()):
            continue
        made = (_made(data, folder.name), folder.name)
        newest = max(newest, made) if newest else made
        channels = channel_totals(data.get("results"))
        total = sum(n for _c, n in channels)
        if total <= 0:
            continue
        brief = data.get("brief") if isinstance(data.get("brief"), dict) else {}
        shipped = brief.get("shipped") if isinstance(brief.get("shipped"), str) else ""
        launch = PastLaunch(pack=folder.name, caption=caption.strip(), first_line=_first_line(caption),
                            signups=total, channels=tuple(channels), shipped=shipped)
        found.append((total, _made(data, folder.name), folder.name, launch))
    if not found:
        return None
    best = max(found, key=lambda f: (f[0], f[1], f[2]))
    return PastLaunch(**{**best[3].__dict__, "latest": (best[1], best[2]) == newest})


def _count(n: int) -> str:
    return f"{n:,} sign-up" + ("" if n == 1 else "s")


def _curly(text: str) -> str:
    """Straight double quotes as curly ones, so a quoted line never nests straight quotes."""
    out, opening = [], True
    for ch in text:
        if ch == '"':
            ch, opening = ("\u201c" if opening else "\u201d"), not opening
        out.append(ch)
    return "".join(out)


def best_launch_line(launch: PastLaunch) -> str:
    """One line on the founder's best launch so far, composed from its recorded results."""
    who = "Your last launch" if launch.latest else "Your best launch so far"
    shown = [(c, n) for c, n in launch.channels if n > 0]
    if len(shown) == 1:
        brought = f"{_count(launch.signups)} on {shown[0][0]}"
    else:
        brought = f"{_count(launch.signups)} ({', '.join(f'{n:,} on {c}' for c, n in shown)})"
    first = _curly(_plain(launch.first_line))
    end = "" if first.endswith((".", "!", "?", "\u2026")) else "."
    return f'{who} brought {brought} with "{first}"{end}'
