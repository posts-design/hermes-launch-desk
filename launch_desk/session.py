"""Small facts about the chat a command runs in, shared by /launch and /launch film."""
from __future__ import annotations

import re

LOCAL_SURFACES = {"cli", "tui", "local", "desktop", "acp"}


def surface(host) -> str:
    try:
        return str(host.surface() or "")
    except Exception:
        return ""


def is_local(host) -> bool:
    """A terminal or desktop client, where long links and paths can be clicked or copied."""
    try:
        return host.surface() in LOCAL_SURFACES
    except Exception:
        return True


def is_unattended(host) -> bool:
    """A cron job or another run nobody answers."""
    try:
        if host.unattended():
            return True
    except Exception:
        pass
    try:
        return host.surface() == "cron"
    except Exception:
        return False


_SECRET_RE = re.compile(
    r"(?i)\b(bearer|basic|token|access_token|api[_-]?key|key|secret|password|authorization)(\s*[:=]\s*|\s+)\S+")
_LONG_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-.~+/=]{24,}")
REASON_MAX = 200


def redact(text: str) -> str:
    """An error message for the log: credentials and long opaque tokens out, one line, short."""
    text = _SECRET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]", " ".join(str(text).split()))
    text = _LONG_TOKEN_RE.sub("[redacted]", text)
    return text[:REASON_MAX]
