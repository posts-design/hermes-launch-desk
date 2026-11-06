"""Scripted in-memory Host for tests."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any


class FakeHost:
    def __init__(self, choices=None, llm_replies=None, mcp_replies=None, surface="cli",
                 unattended=False, data_dir=None, schedule_replies=None):
        self.choices = list(choices or [])
        self.llm_replies = list(llm_replies or [])
        self.mcp_replies = dict(mcp_replies or {})
        self._surface = surface
        self._unattended = unattended
        self._state: dict[str, Any] = {}
        self._data_dir = Path(data_dir) if data_dir else Path(tempfile.mkdtemp(prefix="launch-desk-"))
        self.asked: list[tuple[str, list[dict]]] = []
        self.llm_calls: list[tuple[str, str, dict]] = []
        self.mcp_calls: list[tuple[str, dict]] = []
        self.said: list[str] = []
        self.schedule_replies = list(schedule_replies or [])
        self.scheduled: list[dict] = []
        self.events: list[tuple[str, str]] = []  # ("ask", question) and ("say", text), in order

    def choose(self, question: str, choices: list[dict]) -> str | None:
        self.asked.append((question, choices))
        self.events.append(("ask", question))
        return self.choices.pop(0) if self.choices else None

    def complete_json(self, instructions: str, text: str, schema: dict) -> dict | str | None:
        self.llm_calls.append((instructions, text, schema))
        return self.llm_replies.pop(0) if self.llm_replies else None

    def mcp(self, tool: str, args: dict) -> dict:
        self.mcp_calls.append((tool, args))
        reply = self.mcp_replies.get(tool)
        if isinstance(reply, Exception):
            raise reply
        if reply is None:
            raise RuntimeError(f"no scripted reply for {tool}")
        return reply

    def state_get(self, key: str) -> Any:
        return self._state.get(key)

    def state_set(self, key: str, value: Any) -> None:
        self._state[key] = value

    def data_dir(self) -> Path:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        return self._data_dir

    def surface(self) -> str:
        return self._surface

    def unattended(self) -> bool:
        return self._unattended

    def say(self, text: str) -> None:
        self.said.append(text)
        self.events.append(("say", text))

    def media_path(self, src: Path, label: str) -> str:
        return str(src)

    def schedule(self, job: dict) -> dict:
        """A scripted cronjob_manage create: the next scripted reply (an Exception is raised),
        else a success with a made-up job id."""
        self.scheduled.append(job)
        reply = self.schedule_replies.pop(0) if self.schedule_replies else None
        if isinstance(reply, Exception):
            raise reply
        if reply is None:
            reply = {"success": True, "job_id": f"job{len(self.scheduled)}", "deliver": "origin",
                     "next_run_at": "2026-11-06T12:00:00+00:00"}
        return reply
