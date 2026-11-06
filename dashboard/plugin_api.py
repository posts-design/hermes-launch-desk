"""Launch Desk backend for the Hermes Desktop pane: the founder's packs, read-only.

Mounted by Hermes at /api/plugins/launch-desk/ (dashboard/manifest.json, "api"). It reads only
this plugin's own packs folder (<hermes home>/plugin-data/<this plugin's namespace>/packs) and
the names of Hermes' cron jobs that /launch created for check-ins. It never writes, never reads
another plugin's data and never follows a link out of a pack folder.

  GET /packs                  every pack, newest first, with check-ins and recorded sign-ups
  GET /packs/{name}/card      the share card PNG
  GET /packs/{name}/preview   the feed preview PNG
  GET /packs/{name}/images    both as data URLs (the Desktop REST door only carries JSON)

The pure functions below take their folders as arguments so tests can call them directly.
"""
import base64
import hashlib
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

PLUGIN_ID = "launch-desk"
JOB_PREFIX = "Launch Desk check-in: "  # launch_desk/followup.py JOB_NAME, before {pack}
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}")  # same rule as followup._pack_file
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})-")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
IMAGE_KINDS = {"card": ("card_x", "card-x.png"), "preview": ("preview", "preview-x.png")}

MAX_PACKS = 200
PACK_JSON_MAX = 1024 * 1024
TEXT_MAX = 64 * 1024
IMAGE_MAX = 8 * 1024 * 1024
JOBS_MAX = 8 * 1024 * 1024
LIST_MAX = 50  # hooks, checks, references, results kept per pack


# ------------------------------------------------------------------ where things are

def hermes_home() -> Path:
    from hermes_constants import get_hermes_home  # inside the gateway (or its profile host)
    return Path(get_hermes_home())


def data_dir() -> Path:
    """The same folder ctx.state.data_dir gives the plugin."""
    try:
        from hermes_cli.plugins_state import PluginState
        return Path(PluginState(PLUGIN_ID).data_dir)
    except Exception:  # an older Hermes: its naming rule, spelled out
        digest = hashlib.sha256(PLUGIN_ID.encode("utf-8")).hexdigest()[:8]
        return hermes_home() / "plugin-data" / f"agent-plugin-{PLUGIN_ID}-{digest}"


def packs_root() -> Path:
    return data_dir() / "packs"


def jobs_file() -> Path:
    return hermes_home() / "cron" / "jobs.json"


# ------------------------------------------------------------------ safe reads

def _is_plain_dir(path: Path) -> bool:
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except OSError:
        return False


def _plain_file(folder: Path, filename: Any) -> Optional[Path]:
    """folder/filename when filename is a bare name listed in the folder and is a regular file
    (not a link); None otherwise. Never builds a path from anything not in the listing."""
    if not isinstance(filename, str) or not filename or filename in (".", ".."):
        return None
    if "/" in filename or "\\" in filename or "\x00" in filename:
        return None
    try:
        if filename not in os.listdir(folder):
            return None
        mode = os.lstat(folder / filename).st_mode
    except OSError:
        return None
    return folder / filename if stat.S_ISREG(mode) else None


def _read_bytes(path: Path, limit: int) -> Optional[bytes]:
    try:
        with open(path, "rb") as handle:
            data = handle.read(limit + 1)
    except OSError:
        return None
    return data if len(data) <= limit else None


def _read_json(path: Optional[Path], limit: int) -> Any:
    raw = _read_bytes(path, limit) if path else None
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        return None


def pack_names(root: Path) -> list:
    """Pack folders in root: plain directories (not links) with a valid name and a pack.json."""
    try:
        entries = os.listdir(root)
    except OSError:
        return []
    names = []
    for name in entries:
        if not NAME_RE.fullmatch(name) or ".." in name:
            continue
        folder = root / name
        if _is_plain_dir(folder) and _plain_file(folder, "pack.json"):
            names.append(name)
    return names


def pack_folder(root: Path, name: Any) -> Optional[Path]:
    """root/name only when name is one of pack_names(root)."""
    if not isinstance(name, str) or name not in pack_names(root):
        return None
    return root / name


# ------------------------------------------------------------------ shaping one pack

def _str(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value.strip() else None


def _int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _dicts(value: Any) -> list:
    return [v for v in value if isinstance(v, dict)][:LIST_MAX] if isinstance(value, list) else []


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def _created(folder: Path) -> float:
    """When the pack was written: the oldest file in it. Recording sign-ups rewrites pack.json
    and the founder may edit the post file, but the card is never rewritten."""
    times = []
    try:
        for entry in os.scandir(folder):
            if entry.is_file(follow_symlinks=False):
                times.append(entry.stat(follow_symlinks=False).st_mtime)
    except OSError:
        pass
    return min(times) if times else 0.0


def _references(value: Any) -> list:
    out = []
    for ref in _dicts(value):
        out.append({"brand": _str(ref.get("brand")) or "", "views": _int(ref.get("views")),
                    "url": _str(ref.get("url")) or ""})
    return out


def _checks(data: dict) -> Optional[list]:
    """The pack's visible checks (Task 19), when saved: one {label, ok, detail} per rule.
    Accepts a list of dicts with a pass flag, or of plain strings. None when absent."""
    raw = data.get("checks", data.get("checklist"))
    if not isinstance(raw, list):
        return None
    out = []
    for item in raw[:LIST_MAX]:
        if isinstance(item, str) and item.strip():
            out.append({"label": item.strip(), "ok": None, "detail": None})
        elif isinstance(item, dict):
            label = _str(item.get("label")) or _str(item.get("text")) or _str(item.get("message")) \
                or _str(item.get("rule")) or _str(item.get("id"))
            if not label:
                continue
            ok = next((item[k] for k in ("ok", "passed", "pass") if isinstance(item.get(k), bool)), None)
            if ok is None and isinstance(item.get("status"), str):
                ok = {"pass": True, "ok": True, "fail": False, "failed": False}.get(item["status"].lower())
            out.append({"label": label, "ok": ok, "detail": _str(item.get("detail")) or _str(item.get("fix"))})
    return out


def _results(value: Any) -> list:
    out = []
    for r in _dicts(value):
        channel, signups = _str(r.get("channel")), _int(r.get("signups"))
        if channel and signups is not None:
            out.append({"channel": channel.strip(), "signups": signups,
                        "recorded_at": _str(r.get("recorded_at"))})
    return out


def _latest_by_channel(results: list) -> list:
    """The last number recorded for each channel, in the order channels first appeared."""
    latest: dict = {}
    for r in results:
        key = r["channel"].casefold()
        latest[key] = r if key not in latest else {**r, "channel": latest[key]["channel"]}
    return list(latest.values())


def _post_text(folder: Path, files: dict, data: dict) -> str:
    path = _plain_file(folder, files.get("post"))
    raw = _read_bytes(path, TEXT_MAX) if path else None
    if raw is not None:
        try:
            text = raw.decode("utf-8-sig").strip()
        except UnicodeDecodeError:
            text = ""
        if text:
            return text
    return (_str(data.get("caption")) or "").strip()


def _image_file(folder: Path, files: dict, kind: str) -> Optional[Path]:
    key, default = IMAGE_KINDS[kind]
    name = files.get(key) if isinstance(files.get(key), str) else default
    if not name.lower().endswith(".png"):
        return None
    return _plain_file(folder, name)


def summarize_pack(folder: Path, checkins: Optional[list] = None) -> Optional[dict]:
    """Everything the pane shows for one pack, from its own folder. None if pack.json is unreadable."""
    data = _read_json(_plain_file(folder, "pack.json"), PACK_JSON_MAX)
    if not isinstance(data, dict):
        return None
    files = data.get("files") if isinstance(data.get("files"), dict) else {}
    brief = data.get("brief") if isinstance(data.get("brief"), dict) else {}
    brand = data.get("brand") if isinstance(data.get("brand"), dict) else {}
    card = data.get("card") if isinstance(data.get("card"), dict) else {}
    created = _created(folder)
    dated = DATE_RE.match(folder.name)
    results = _results(data.get("results"))
    alt = _plain_file(folder, files.get("alt_text"))
    alt_raw = _read_bytes(alt, TEXT_MAX) if alt else None
    intent = _str(data.get("intent_url"))
    return {
        "name": folder.name,
        "date": dated.group(1) if dated else (_iso(created)[:10] if created else None),
        "created": _iso(created) if created else None,
        "product": _str(brand.get("name")),
        "product_url": _str(brand.get("url")) or _str(brief.get("product_url")),
        "network": _str(brief.get("network")) or "x",
        "what": _str(brief.get("what")),
        "post": _post_text(folder, files, data),
        "hook": (_str(data.get("hook")) or "").strip(),
        "hook_source": _str(data.get("hook_source")),
        "hooks": [h for h in data.get("hooks", []) if isinstance(h, str) and h.strip()][:LIST_MAX]
        if isinstance(data.get("hooks"), list) else [],
        "checks": _checks(data),
        "references": _references(data.get("references")),
        "layout": _str(card.get("layout")),
        "screenshots": _int(card.get("screenshots")),
        "intent_url": intent if intent and intent.startswith("https://") else None,
        "alt_text": alt_raw.decode("utf-8", "replace").strip() if alt_raw else None,
        "has_card": _image_file(folder, files, "card") is not None,
        "has_preview": _image_file(folder, files, "preview") is not None,
        "results": results,
        "signups": _latest_by_channel(results),
        "checkins": list(checkins or []),
    }


# ------------------------------------------------------------------ check-ins (Hermes cron)

def checkins_by_pack(path: Path) -> dict:
    """{pack name: [check-in jobs]} from Hermes' jobs.json, matched by the job name /launch gives
    them. Read-only: the file is parsed here, never loaded through the scheduler (which may repair
    and rewrite it). Missing or unreadable file: no check-ins."""
    data = _read_json(path, JOBS_MAX) if path.is_file() else None
    jobs = data.get("jobs") if isinstance(data, dict) else data
    out: dict = {}
    for job in _dicts(jobs) if isinstance(jobs, list) else []:
        name = job.get("name")
        if not isinstance(name, str) or not name.startswith(JOB_PREFIX):
            continue
        pack = name[len(JOB_PREFIX):].strip()
        enabled = job.get("enabled", True) is not False
        state = _str(job.get("state")) or "scheduled"
        if state not in ("completed", "error") and not enabled:
            state = "paused"
        elif state == "paused" and enabled:
            state = "scheduled"
        out.setdefault(pack, []).append({
            "id": _str(job.get("id")) or "",
            "state": state,
            "next_run_at": _str(job.get("next_run_at")),
            "last_run_at": _str(job.get("last_run_at")),
            "last_status": _str(job.get("last_status")),
        })
    for jobs_for_pack in out.values():
        jobs_for_pack.sort(key=lambda j: j["next_run_at"] or j["last_run_at"] or "")
    return out


def is_due(checkin: dict) -> bool:
    """A check-in still to come: scheduled with a next run."""
    return checkin.get("state") == "scheduled" and bool(checkin.get("next_run_at"))


# ------------------------------------------------------------------ the listing

def list_packs(root: Path, jobs_path: Optional[Path] = None) -> dict:
    """Every readable pack, newest first (by when it was written, then by name)."""
    checkins = checkins_by_pack(jobs_path) if jobs_path is not None else {}
    packs = []
    for name in pack_names(root):
        summary = summarize_pack(root / name, checkins.get(name))
        if summary is not None:
            packs.append(summary)
    packs.sort(key=lambda p: (p["created"] or "", p["name"]), reverse=True)
    packs = packs[:MAX_PACKS]
    due = sum(1 for p in packs for c in p["checkins"] if is_due(c))
    return {"packs": packs, "checkins_due": due}


def pack_image(root: Path, name: Any, kind: str) -> Optional[bytes]:
    """The PNG bytes of a pack's card or preview, only from inside that pack's folder."""
    folder = pack_folder(root, name)
    if folder is None or kind not in IMAGE_KINDS:
        return None
    data = _read_json(_plain_file(folder, "pack.json"), PACK_JSON_MAX)
    files = data.get("files") if isinstance(data, dict) and isinstance(data.get("files"), dict) else {}
    path = _image_file(folder, files, kind)
    raw = _read_bytes(path, IMAGE_MAX) if path else None
    return raw if raw and raw.startswith(PNG_MAGIC) else None


def data_url(png: Optional[bytes]) -> Optional[str]:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii") if png else None


# ------------------------------------------------------------------ routes

def _build_router():
    try:
        from fastapi import APIRouter, HTTPException, Response
    except ImportError:  # outside Hermes: the functions above still import for tests
        return None

    api = APIRouter()
    no_store = {"Cache-Control": "no-store"}

    # Plain `def` handlers: FastAPI runs them in a worker thread, off the gateway's event loop.
    @api.get("/packs")
    def get_packs():
        return list_packs(packs_root(), jobs_file())

    def _png(name: str, kind: str):
        png = pack_image(packs_root(), name, kind)
        if png is None:
            raise HTTPException(status_code=404, detail="No such image.")
        return Response(content=png, media_type="image/png", headers=no_store)

    @api.get("/packs/{name}/card")
    def get_card(name: str):
        return _png(name, "card")

    @api.get("/packs/{name}/preview")
    def get_preview(name: str):
        return _png(name, "preview")

    @api.get("/packs/{name}/images")
    def get_images(name: str):
        root = packs_root()
        if pack_folder(root, name) is None:
            raise HTTPException(status_code=404, detail="No such pack.")
        return {"name": name, "card": data_url(pack_image(root, name, "card")),
                "preview": data_url(pack_image(root, name, "preview"))}

    return api


router = _build_router()
