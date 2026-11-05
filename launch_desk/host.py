from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol


class SourceUnavailable(Exception):
    """posts.design could not be reached or refused the call.

    kind: "permission", "timeout" or "error".
    """

    def __init__(self, message: str = "", kind: str = "error"):
        super().__init__(message)
        self.kind = kind


class Host(Protocol):
    def choose(self, question: str, choices: list[dict]) -> str | None: ...

    def complete_json(self, instructions: str, text: str, schema: dict) -> dict | str | None: ...

    def mcp(self, tool: str, args: dict) -> dict: ...

    def state_get(self, key: str) -> Any: ...

    def state_set(self, key: str, value: Any) -> None: ...

    def data_dir(self) -> Path: ...

    def surface(self) -> str: ...

    def unattended(self) -> bool: ...


class HermesHost:
    """Host backed by a Hermes PluginContext."""

    def __init__(self, ctx):
        self.ctx = ctx

    def choose(self, question: str, choices: list[dict]) -> str | None:
        return self.ctx.ask_choice(question, choices)

    def complete_json(self, instructions: str, text: str, schema: dict) -> dict | str | None:
        """The parsed JSON object; else the raw reply text (some providers answer in plain
        text or a fenced block); None on an empty reply or any error."""
        try:
            result = self.ctx.llm.complete_structured(
                instructions=instructions,
                input=[{"type": "text", "text": text}],
                json_schema=schema,
                schema_name="launch_desk",
                purpose="launch-desk",
                max_tokens=2000,
            )
        except Exception:
            return None
        parsed = getattr(result, "parsed", None)
        if isinstance(parsed, dict):
            return parsed
        text = getattr(result, "text", None)
        return text if isinstance(text, str) and text.strip() else None

    def mcp(self, tool: str, args: dict) -> dict:
        try:
            envelope = self.ctx.call_mcp("posts", tool, args, timeout=30)
        except PermissionError as exc:
            raise SourceUnavailable(str(exc), kind="permission") from exc
        except TimeoutError as exc:
            raise SourceUnavailable(str(exc), kind="timeout") from exc
        except Exception as exc:
            raise SourceUnavailable(str(exc), kind="error") from exc
        if not isinstance(envelope, dict) or not envelope.get("ok"):
            detail = envelope.get("error") if isinstance(envelope, dict) else None
            raise SourceUnavailable(str(detail or "call failed"), kind="error")
        try:
            data = json.loads(envelope.get("result"))
        except (TypeError, ValueError) as exc:
            raise SourceUnavailable("unreadable reply", kind="error") from exc
        if not isinstance(data, dict):
            raise SourceUnavailable("unexpected reply shape", kind="error")
        return data

    def state_get(self, key: str) -> Any:
        return self.ctx.state.get(key)

    def state_set(self, key: str, value: Any) -> None:
        self.ctx.state.set(key, value)

    def data_dir(self) -> Path:
        path = Path(self.ctx.state.data_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def surface(self) -> str:
        return self.ctx.session_surface()

    def unattended(self) -> bool:
        return self.ctx.current_cron_execution() is not None
