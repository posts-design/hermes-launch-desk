"""Check-ins after a launch: the scheduled jobs, and the two tools they and the founder use.

On the founder's Yes, /launch creates two one-off jobs on Hermes' own scheduler (a day and
three days after approve). Each job's agent calls launch_desk_followup and posts one message
to the chat the job came from: how many sign-ups did each channel bring, and one next post.
When the founder answers with numbers, the agent calls launch_desk_record_results, which
appends them to the pack's pack.json. Nothing here posts or asks anything.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .pack import JUDGE_BY_CONVERSION

SKILL = "launch-desk:launch-desk"
CHECKIN_HOURS = (24, 72)
JOB_NAME = "Launch Desk check-in: {pack}"
# Fixed: only the pack name changes. Braces are only for {pack} (it is also the blueprint's
# prompt template).
PROMPT = (
    "Launch Desk check-in for the launch pack {pack}. "
    "Call the launch_desk_followup tool with pack set to {pack}. "
    "Then write one short message to the founder from its result: ask how many sign-ups each "
    "channel brought, and say they can reply with the numbers. Suggest one next post, taken "
    "from the suggestions in the result, using only what the result gives. "
    "Name the pack {pack} in the message. Do not wait for an answer."
)

CHANNEL_MAX = 40
SIGNUPS_MAX = 1_000_000
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")


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
    } for hours in CHECKIN_HOURS]


# ------------------------------------------------------------------ reading a pack

class _Refused(Exception):
    pass


def _pack_file(root: Path, name: Any) -> Path:
    """data_dir/packs/<name>/pack.json, refusing any name that leaves the packs folder."""
    if not isinstance(name, str) or not _NAME_RE.match(name) or ".." in name:
        raise _Refused("Unknown pack name.")
    base = Path(root).resolve()
    folder = (base / name).resolve()
    if folder.parent != base or not (folder / "pack.json").is_file():
        raise _Refused("Unknown pack name.")
    return folder / "pack.json"


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


def followup(root: Path, pack: Any) -> dict:
    """What a check-in needs, as data for the agent to phrase. Never raises."""
    try:
        data = _load(_pack_file(root, pack))
    except _Refused as exc:
        return {"ok": False, "error": str(exc)}
    brief = data.get("brief") if isinstance(data.get("brief"), dict) else {}
    hook = data.get("hook") if isinstance(data.get("hook"), str) else ""
    unused = [h for h in _strings(data.get("hooks")) if h.strip() != hook.strip()]
    more = _dicts(data.get("more_references"))
    detail = brief.get("detail") if isinstance(brief.get("detail"), str) else ""
    return {
        "ok": True,
        "pack": pack,
        "post": data.get("caption") or "",
        "intent_url": data.get("intent_url") or "",
        "unused_hooks": unused,
        "more_references": more,
        "after_post_check": {"id": "judge-by-conversion", "text": JUDGE_BY_CONVERSION},
        "results": _dicts(data.get("results")),
        "suggestions": _suggestions(detail.strip(), unused, more),
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
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
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
    data["results"] = _dicts(data.get("results")) + [entry]
    try:
        _write_json(path, data)
    except OSError:
        return {"ok": False, "error": "The pack could not be saved."}
    return {"ok": True, "pack": pack, "results": data["results"]}
