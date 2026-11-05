"""Setup check: is posts.design reachable through Hermes, and if not, which one command fixes it.

Missing setup is not an error: /launch offers the sample pack and carries on.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Literal

from .host import SourceUnavailable

ADD_SERVER = "hermes mcp add posts --url https://posts.design/mcp --auth oauth"
ALLOWLIST = "hermes config set plugins.entries.launch-desk.mcp_allowlist '[\"posts\"]'"
SIGN_IN = "hermes mcp login posts"

# Texts Hermes' MCP layer returns (tools/mcp_tool_handlers.py): sign-in is "requires
# re-authentication ... Run `hermes mcp login <name>`" (its detail can carry upstream text, so
# sign-in is checked first); an unconfigured or stopped server is "MCP server '<name>' is not
# connected". "transport is down" and "unreachable after N consecutive failures" are transient.
_SIGNIN_MARKS = ("re-authentication", "hermes mcp login", "401", "unauthorized")
_SIGNIN_WORDS = re.compile(r"\b(auth|token)\b")
_SERVER_MARKS = ("is not connected",)


@dataclass(frozen=True)
class SetupIssue:
    code: Literal["server", "allowlist", "signin", "unreachable"]
    message: str
    command: str


def _issue(code: str) -> SetupIssue:
    if code == "server":
        return SetupIssue("server", "posts.design is not connected in Hermes. Add it with the command below; if you already did, run `hermes mcp list` and restart Hermes.", ADD_SERVER)
    if code == "allowlist":
        # Hermes checks the allowlist before it looks for the server, so this can also mean
        # the server was never added: give both steps, in order.
        return SetupIssue("allowlist", "Launch Desk can't use posts.design yet. Run these two commands; "
                          "skip the first if you already added the server.", f"{ADD_SERVER}\n{ALLOWLIST}")
    if code == "signin":
        return SetupIssue("signin", "posts.design needs you to sign in.", SIGN_IN)
    return SetupIssue("unreachable", "posts.design didn't answer; the sample pack will be used.", "")


def _classify(exc: Exception) -> str:
    if isinstance(exc, SourceUnavailable) and exc.kind == "permission":
        return "allowlist"
    if isinstance(exc, PermissionError):
        return "allowlist"
    if isinstance(exc, SourceUnavailable) and exc.kind == "timeout":
        return "unreachable"
    text = str(exc).lower()
    if any(m in text for m in _SIGNIN_MARKS) or _SIGNIN_WORDS.search(text):
        return "signin"
    if any(m in text for m in _SERVER_MARKS):
        return "server"
    return "unreachable"


def check_setup(probe: Callable[[], dict]) -> list[SetupIssue]:
    """Run the free `get_my_access` probe; return what to fix (empty when all is well)."""
    try:
        reply = probe()
    except Exception as exc:  # any failure is a setup finding, never a crash
        return [_issue(_classify(exc))]
    if isinstance(reply, dict) and reply.get("allowed") is False:
        return [_issue("signin")]
    return []
