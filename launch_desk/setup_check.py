"""Setup check: is posts.design reachable through Hermes, and if not, which one command fixes it.

Missing setup is not an error: /launch offers the sample pack and carries on.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from .host import SourceUnavailable

ADD_SERVER = "hermes mcp add posts --url https://posts.design/mcp --auth oauth"
ALLOWLIST = "hermes config set plugins.entries.launch-desk.mcp_allowlist '[\"posts\"]'"
SIGN_IN = "hermes mcp login posts"

# Substrings (lower-case) of the texts Hermes' MCP layer returns.
# tools/mcp_tool_handlers.py: "MCP server '<name>' is not connected" (also what an unconfigured
# server gets), "... requires re-authentication ... Run `hermes mcp login <name>`".
_SERVER_MARKS = ("is not connected", "not found", "unknown server", "no mcp server",
                 "not configured", "no such server", "unknown mcp")
_SIGNIN_MARKS = ("401", "unauthorized", "login", "auth", "token")


@dataclass(frozen=True)
class SetupIssue:
    code: Literal["server", "allowlist", "signin", "unreachable"]
    message: str
    command: str


def _issue(code: str) -> SetupIssue:
    if code == "server":
        return SetupIssue("server", "posts.design is not set up in Hermes yet.", ADD_SERVER)
    if code == "allowlist":
        return SetupIssue("allowlist", "Launch Desk is not allowed to use the posts.design server yet.", ALLOWLIST)
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
    if any(m in text for m in _SERVER_MARKS):
        return "server"
    if any(m in text for m in _SIGNIN_MARKS):
        return "signin"
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
