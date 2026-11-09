"""Launch Desk backend for the Hermes Desktop page: the founder's packs, read-only.

Mounted by Hermes at /api/plugins/launch-desk/ (dashboard/manifest.json, "api"). It reads only
this plugin's own packs folder (<hermes home>/plugin-data/<this plugin's namespace>/packs) and,
read-only, the check-in jobs /launch created in Hermes' cron/jobs.json. It never writes, never
reads another plugin's data and never follows a link out of a pack folder.

  GET /packs                         every pack, newest first, with check-ins and sign-ups
  GET /packs/{name}/card             the share card PNG
  GET /packs/{name}/preview          the feed preview PNG
  GET /packs/{name}/images?kind=...  one of them as a data URL (Desktop's REST door carries JSON only)

The pure functions below take their folders as arguments so tests can call them directly.
"""
import base64
import hashlib
import ipaddress
import json
import os
import re
import socket
import stat
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

PLUGIN_ID = "launch-desk"
JOB_PREFIX = "Launch Desk check-in: "  # launch_desk/followup.py JOB_NAME, before {pack}
# A check-in made from the automation blueprint is named after its title, and its prompt carries
# the pack slot (launch_desk/followup.py BLUEPRINT_PROMPT): a folder name, a label or LATEST.
BLUEPRINT_JOB_NAME = "Launch Desk check-in"
BLUEPRINT_PACK_RE = re.compile(r'launch_desk_followup tool with pack set to "([^"\n]*)"')
LATEST = "Your latest pack"  # launch_desk/followup.py LATEST
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}")  # same rule as followup._pack_file
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})-")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
IMAGE_KINDS = {"card": ("card_x", "card-x.png"), "preview": ("preview", "preview-x.png")}

MAX_PACKS = 200
PACK_JSON_MAX = 1024 * 1024
TEXT_MAX = 64 * 1024
IMAGE_MAX = 8 * 1024 * 1024
JOBS_MAX = 8 * 1024 * 1024
LIST_MAX = 50  # checks, references and results read per pack
MISSED_AFTER_S = 600  # a check-in this long past its run time that has not run counts as missed


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
    """At most `limit` bytes of a regular file, refusing a link even if one was swapped in after
    _plain_file looked (O_NOFOLLOW, then fstat on what was actually opened)."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            data = handle.read(limit + 1)
    except OSError:
        return None
    finally:
        if fd >= 0:
            os.close(fd)
    return data if len(data) <= limit else None


def _read_json(path: Optional[Path], limit: int) -> Any:
    raw = _read_bytes(path, limit) if path else None
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        return None


def _valid_name(name: Any) -> bool:
    return isinstance(name, str) and bool(NAME_RE.fullmatch(name)) and ".." not in name


def pack_folder(root: Path, name: Any, listing: Optional[list] = None) -> Optional[Path]:
    """root/name when name is a pack: a valid name listed in root, a plain directory (not a
    link) holding a regular pack.json. `listing` saves a listdir when the caller has one."""
    if not _valid_name(name):
        return None
    try:
        if name not in (listing if listing is not None else os.listdir(root)):
            return None
    except OSError:
        return None
    folder = root / name
    return folder if _is_plain_dir(folder) and _plain_file(folder, "pack.json") else None


def pack_names(root: Path) -> list:
    try:
        entries = os.listdir(root)
    except OSError:
        return []
    return [name for name in entries if pack_folder(root, name, entries) is not None]


# ------------------------------------------------------------------ shaping one pack

def _str(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value.strip() else None


def _int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _dicts(value: Any) -> list:
    return [v for v in value if isinstance(v, dict)][:LIST_MAX] if isinstance(value, list) else []


def _https(value: Any) -> str:
    """The link only when it is a web link. Desktop's openExternal also opens file: URLs and
    local paths, and these strings come from search results and editable files."""
    text = _str(value)
    return text.strip() if text and text.strip().lower().startswith("https://") else ""


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


_PACK_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}")


def _reference(ref: dict) -> dict:
    """brand, views and a web link; the founder's own launch also has its sign-ups and its
    pack's name (its "url" is that name, never a link)."""
    out = {"brand": _str(ref.get("brand")) or "", "views": _int(ref.get("views")), "url": _https(ref.get("url"))}
    signups = _int(ref.get("signups"))
    if signups is not None and signups >= 0:
        name = _str(ref.get("url")) or ""
        out["signups"] = signups
        out["pack"] = name if _PACK_NAME_RE.fullmatch(name) and ".." not in name else ""
    return out


def _references(value: Any) -> list:
    return [_reference(ref) for ref in _dicts(value)]


def _checks(value: Any) -> Optional[list]:
    """The visible checklist /launch saves: checks = [{rules, ok, text}]. None when absent."""
    if not isinstance(value, list):
        return None
    return [{"text": c["text"].strip(), "ok": c.get("ok") if isinstance(c.get("ok"), bool) else None}
            for c in _dicts(value) if _str(c.get("text"))]


def _signups(value: Any) -> list:
    """The last number recorded for each channel, in the order channels first appeared."""
    latest: dict = {}
    for r in _dicts(value):
        channel, signups = _str(r.get("channel")), _int(r.get("signups"))
        if not channel or signups is None:
            continue
        key = channel.strip().casefold()
        first = latest[key]["channel"] if key in latest else channel.strip()
        latest[key] = {"channel": first, "signups": signups, "recorded_at": _str(r.get("recorded_at"))}
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
    """Everything the page shows for one pack, from its own folder. None if pack.json is unreadable."""
    data = _read_json(_plain_file(folder, "pack.json"), PACK_JSON_MAX)
    if not isinstance(data, dict):
        return None
    files = data.get("files") if isinstance(data.get("files"), dict) else {}
    brand = data.get("brand") if isinstance(data.get("brand"), dict) else {}
    card = data.get("card") if isinstance(data.get("card"), dict) else {}
    created = _created(folder)
    dated = DATE_RE.match(folder.name)
    alt = _plain_file(folder, files.get("alt_text"))
    alt_raw = _read_bytes(alt, TEXT_MAX) if alt else None
    return {
        "name": folder.name,
        "date": dated.group(1) if dated else (_iso(created)[:10] if created else None),
        "created": _iso(created) if created else None,
        "product": _str(brand.get("name")),
        "post": _post_text(folder, files, data),
        "hook": (_str(data.get("hook")) or "").strip(),
        "hook_source": _str(data.get("hook_source")),
        "checks": _checks(data.get("checks")),
        "references": _references(data.get("references")),
        "layout": _str(card.get("layout")),
        "intent_url": _https(data.get("intent_url")) or None,
        "alt_text": alt_raw.decode("utf-8", "replace").strip() if alt_raw else None,
        "has_card": _image_file(folder, files, "card") is not None,
        "has_preview": _image_file(folder, files, "preview") is not None,
        "signups": _signups(data.get("results")),
        "checkins": list(checkins or []),
    }


# ------------------------------------------------------------------ pack labels (the blueprint's)

def _made(data: dict, name: str) -> str:
    created = data.get("created_at")
    if isinstance(created, str) and created.strip():
        return created.strip()
    m = DATE_RE.match(name)
    return m.group(1) if m else ""


def _private(host: str) -> bool:
    """launch_desk/rules.py _private_host, offline: localhost, .local, private or local addresses."""
    host = host.lower().rstrip(".")
    if host == "localhost" or host.endswith((".localhost", ".local")):
        return True
    try:
        addr = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        if not re.fullmatch(r"(?:0x[0-9a-f]*|[0-9]+)(?:\.(?:0x[0-9a-f]*|[0-9]+)){0,3}", host):
            return False
        try:
            addr = ipaddress.ip_address(socket.inet_aton(host))
        except OSError:
            return False
    return addr.is_loopback or addr.is_private or addr.is_link_local or addr.is_unspecified


def _site(url: Any) -> str:
    """launch_desk/followup.py _site: the host, lower case, without www, with its port when local."""
    if not isinstance(url, str):
        return ""
    try:
        parts = urllib.parse.urlsplit(url.strip())
        host, port = (parts.hostname or "").lower(), parts.port
    except ValueError:
        return ""
    host = host[4:] if host.startswith("www.") else host
    return f"{host}:{port}" if host and port and _private(host) else host


def _pack_site(data: dict) -> str:
    brand = data.get("brand") if isinstance(data.get("brand"), dict) else {}
    brief = data.get("brief") if isinstance(data.get("brief"), dict) else {}
    return _site(brand.get("url")) or _site(brief.get("product_url"))


def _label(name: str, data: dict, folder: Optional[Path] = None) -> str:
    """The plugin's pack_label (launch_desk/followup.py), for matching a blueprint check-in: the
    brand's name, else its site, else the folder name; then when it was made."""
    brand = data.get("brand") if isinstance(data.get("brand"), dict) else {}
    who = " ".join(str(brand.get("name") or "").split()) or _pack_site(data) or name
    when = ""
    created = data.get("created_at")
    try:
        stamp = datetime.fromisoformat(created) if isinstance(created, str) and created.strip() else None
    except ValueError:
        stamp = None
    if stamp is None and folder is not None and _created(folder):
        stamp = datetime.fromtimestamp(_created(folder), tz=timezone.utc)
    if stamp is not None:
        stamp = stamp.astimezone(timezone.utc) if stamp.tzinfo else stamp
        when = f"{stamp.day} {MONTHS[stamp.month - 1]} {stamp:%H:%M} UTC"
    else:
        m = DATE_RE.match(name)
        if m:
            _year, month, day = (int(x) for x in m.group(1).split("-"))
            when = f"{day} {MONTHS[month - 1]}" if 1 <= month <= 12 else ""
    return f"{who} \u00b7 {when}" if when else who


def _pack_index(root: Path) -> list:
    """(label, pack name, made) for every pack, newest first, as the blueprint offers them."""
    found = []
    for name in pack_names(root):
        data = _read_json(_plain_file(root / name, "pack.json"), PACK_JSON_MAX)
        if isinstance(data, dict):
            found.append((_made(data, name), name, data))
    found.sort(key=lambda t: (t[0], t[1]), reverse=True)
    out, seen = [], {}
    for made, name, data in found:
        label = _label(name, data, root / name)
        seen[label] = seen.get(label, 0) + 1
        out.append((label if seen[label] == 1 else f"{label} ({seen[label]})", name, made))
    return out


def pack_index(root: Path) -> list:
    return _pack_index(root)


def pack_labels(root: Path) -> dict:
    """{label: pack name} for every pack, newest first, as the blueprint offers them."""
    return {label: name for label, name, _made_at in pack_index(root)}


def _blueprint_pack(prompt: Any, index: list, created: Any = None) -> Optional[str]:
    """The pack a blueprint check-in is for: LATEST (or blank) is the newest pack made before the
    job was created (the one it ran for), else a pack name or a label. None when it names no pack
    this folder has."""
    m = BLUEPRINT_PACK_RE.search(prompt) if isinstance(prompt, str) else None
    if not m:
        return None
    value = " ".join(m.group(1).split())
    if value.lower() in ("", LATEST.lower()):
        made_by = _when(created)
        for _label_text, name, made in index:
            when = _when(made)
            if made_by is None or when is None or when <= made_by:
                return name
        return None
    if any(value == name for _label_text, name, _made_at in index):
        return value
    return next((name for label, name, _made_at in index if label == value), None)


# ------------------------------------------------------------------ check-ins (Hermes cron)

def _when(value: Any) -> Optional[datetime]:
    try:
        when = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None
    return when.replace(tzinfo=timezone.utc) if when and when.tzinfo is None else when


def checkins_by_pack(path: Path, now: Optional[datetime] = None, root: Optional[Path] = None) -> dict:
    """{pack name: [check-in jobs]} from Hermes' jobs.json, matched by the job name /launch gives
    them. Read-only: the file is parsed here, never loaded through the scheduler (which may repair
    and rewrite it). Missing or unreadable file: no check-ins.

    state follows the scheduler: completed and error stay; a job runs only when it is enabled and
    carries no pause marker (cron.jobs.is_job_runnable), else it is paused. A scheduled check-in
    whose run time passed more than MISSED_AFTER_S ago without a run is marked missed. With
    `root`, check-ins made from the automation blueprint (named BLUEPRINT_JOB_NAME) are matched
    too, by the pack their prompt names (a "latest" one goes with the newest pack made before the
    job was created)."""
    now = now or datetime.now(timezone.utc)
    data = _read_json(path, JOBS_MAX)
    jobs = data.get("jobs") if isinstance(data, dict) else data
    out: dict = {}
    index: Optional[list] = None  # built only when a blueprint check-in is there
    for job in _dicts(jobs) if isinstance(jobs, list) else []:
        name = job.get("name")
        if isinstance(name, str) and name.strip() == BLUEPRINT_JOB_NAME and root is not None:
            if index is None:
                index = list(pack_index(root))
            pack = _blueprint_pack(job.get("prompt"), index, job.get("created_at"))
            if pack is None:
                continue
        elif isinstance(name, str) and name.startswith(JOB_PREFIX):
            pack = name[len(JOB_PREFIX):].strip()
        else:
            continue
        stored = (_str(job.get("state")) or "").strip()
        runnable = bool(job.get("enabled", True)) and stored != "paused" and not job.get("paused_at")
        state = stored if stored in ("completed", "error") else "scheduled" if runnable else "paused"
        next_run = _when(job.get("next_run_at"))
        missed = (state == "scheduled" and next_run is not None and not _str(job.get("last_run_at"))
                  and (now - next_run).total_seconds() > MISSED_AFTER_S)
        out.setdefault(pack, []).append({
            "id": _str(job.get("id")) or "",
            "state": state,
            "missed": missed,
            "next_run_at": _str(job.get("next_run_at")),
            "last_run_at": _str(job.get("last_run_at")),
        })
    for jobs_for_pack in out.values():
        jobs_for_pack.sort(key=lambda j: j["next_run_at"] or j["last_run_at"] or "")
    return out


def checkin_summary(packs: list, now: Optional[datetime] = None) -> dict:
    """For the status bar: the next check-in still to come, and how many were missed."""
    now = now or datetime.now(timezone.utc)
    upcoming = [c["next_run_at"] for p in packs for c in p["checkins"]
                if c["state"] == "scheduled" and not c["missed"] and (_when(c["next_run_at"]) or now) > now]
    upcoming.sort(key=_when)
    return {"next_run_at": upcoming[0] if upcoming else None,
            "missed": sum(1 for p in packs for c in p["checkins"] if c["missed"])}


# ------------------------------------------------------------------ the listing

def list_packs(root: Path, jobs_path: Optional[Path] = None, now: Optional[datetime] = None) -> dict:
    """Every readable pack, newest first (by when it was written, then by name). At most
    MAX_PACKS are read: the newest by when their files were written (one directory scan each)."""
    checkins = checkins_by_pack(jobs_path, now, root) if jobs_path is not None else {}
    names = sorted(pack_names(root), key=lambda n: (_created(root / n), n), reverse=True)[:MAX_PACKS]
    packs = []
    for name in names:
        summary = summarize_pack(root / name, checkins.get(name))
        if summary is not None:
            packs.append(summary)
    packs.sort(key=lambda p: (p["created"] or "", p["name"]), reverse=True)
    packs = packs[:MAX_PACKS]
    return {"packs": packs, "checkins": checkin_summary(packs, now)}


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
    def get_image(name: str, kind: str = "card"):
        root = packs_root()
        if kind not in IMAGE_KINDS or pack_folder(root, name) is None:
            raise HTTPException(status_code=404, detail="No such pack.")
        return {"name": name, "kind": kind, "url": data_url(pack_image(root, name, kind))}

    return api


router = _build_router()
