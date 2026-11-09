from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import time
import uuid
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

    def say(self, text: str) -> None: ...

    def media_path(self, src: Path, label: str) -> str: ...

    def schedule(self, job: dict) -> dict: ...

    def config(self, key: str) -> Any: ...

    def which(self, name: str) -> str | None: ...

    def run(self, argv: list[str], cwd: Path, env: dict, timeout: float) -> tuple[int, str]: ...


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
            logging.getLogger(__name__).warning("launch-desk: complete_structured failed", exc_info=True)
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

    def say(self, text: str) -> None:
        """A progress line in the chat. Best effort: a refused or failed post is ignored."""
        try:
            self.ctx.post_message(text)
        except Exception:
            pass

    def schedule(self, job: dict) -> dict:
        """One call to Hermes' own cron tool, as the model would make it (Hermes applies its
        usual checks); its JSON reply as a dict. The tool is cronjob_manage in Hermes 0.22.7
        and was cronjob before."""
        reply: Any = None
        for name in ("cronjob_manage", "cronjob"):
            reply = self.ctx.dispatch_tool(name, dict(job))
            if isinstance(reply, str):
                try:
                    reply = json.loads(reply)
                except ValueError:
                    return {"success": False, "error": "unreadable reply from the scheduler"}
            if not isinstance(reply, dict):
                return {"success": False, "error": "unexpected reply from the scheduler"}
            if not str(reply.get("error") or "").startswith("Unknown tool"):
                break
        return reply

    def config(self, key: str) -> Any:
        """One of this plugin's settings (plugins.entries.launch-desk.settings.<key> in Hermes'
        config.yaml), read-only; None when unset or unreadable."""
        get = getattr(self.ctx, "get_config", None)
        if get is None:
            return None
        try:
            return get(key, None)
        except Exception:
            logging.getLogger(__name__).warning("launch-desk: setting %s unreadable", key, exc_info=True)
            return None

    def which(self, name: str) -> str | None:
        return shutil.which(name)

    def run(self, argv: list[str], cwd: Path, env: dict, timeout: float) -> tuple[int, str]:
        """Run one command (list args, no shell) in its own process group: its exit code and
        its output (stdout and stderr together). TimeoutError after `timeout` seconds, once the
        process group has been stopped."""
        proc = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            out, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _stop(proc)
            raise TimeoutError(f"no exit after {timeout} s") from None
        return proc.returncode, (out or b"").decode("utf-8", "replace")[-20_000:]

    def media_path(self, src: Path, label: str) -> str:
        """The path for a MEDIA line. Chat apps only get files from Hermes' media cache, so a
        copy goes to <HERMES_HOME>/cache/images, or cache/videos for an MP4 (Hermes clears them
        after about a day); the original stays put. data_dir is <HERMES_HOME>/plugin-data/<namespace>:
        if it isn't (or isn't absolute), or the copy fails, the original path is used."""
        try:
            data = self.data_dir()
            if not data.is_absolute():
                return str(src)
            home = data.parents[1]
            if data.parent != home / "plugin-data":
                return str(src)
            video = Path(src).suffix.lower() == ".mp4"
            cache = home / "cache" / ("videos" if video else "images")
            cache.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            dest = cache / f"launch-desk_{stamp}_{uuid.uuid4().hex[:8]}_{label}{'.mp4' if video else '.png'}"
            shutil.copyfile(src, dest)
            return str(dest)
        except Exception:
            logging.getLogger(__name__).warning("launch-desk: media copy failed", exc_info=True)
            return str(src)


def _stop(proc: subprocess.Popen) -> None:
    """Stop a command that ran too long: its process group gets SIGTERM, then SIGKILL."""
    for sig in (signal.SIGTERM, getattr(signal, "SIGKILL", signal.SIGTERM)):
        try:
            if hasattr(os, "killpg"):
                os.killpg(proc.pid, sig)
            else:
                proc.kill()
        except (OSError, ProcessLookupError):
            pass
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue
