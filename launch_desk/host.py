from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class Host(Protocol):
    def choose(self, question: str, choices: list[dict]) -> str | None: ...

    def complete_json(self, instructions: str, text: str, schema: dict) -> dict | None: ...

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

    def complete_json(self, instructions: str, text: str, schema: dict) -> dict | None:
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
        return getattr(result, "parsed", None)

    def mcp(self, tool: str, args: dict) -> dict:
        return self.ctx.call_mcp("posts", tool, args, timeout=30)

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
