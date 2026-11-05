"""Launch Desk: a posts.design plugin for Hermes Agent."""
from __future__ import annotations

import json
from pathlib import Path

try:
    from .launch_desk import wizard
    from .launch_desk.host import HermesHost
except ImportError:  # imported as a top-level module (pytest collects the repo root)
    from launch_desk import wizard
    from launch_desk.host import HermesHost

SCREENSHOT_SCHEMA = {
    "name": "launch_desk_attach_screenshot",
    "description": "Attach a product screenshot (PNG, JPEG or WebP) for the next /launch share card.",
    "parameters": {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Path to the image file."}},
        "required": ["path"],
    },
}


def _is_image(path: Path) -> bool:
    try:
        with open(path, "rb") as handle:
            head = handle.read(12)
    except OSError:
        return False
    return (
        head.startswith(b"\x89PNG\r\n\x1a\n")
        or head.startswith(b"\xff\xd8\xff")
        or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")
    )


def register(ctx):
    def launch(raw_args="", **kwargs):
        return wizard.run_launch(HermesHost(ctx), raw_args or "")

    def attach_screenshot(args, **kwargs):
        path = Path(str((args or {}).get("path", ""))).expanduser()
        if not path.is_file():
            return json.dumps({"ok": False, "error": "File not found."})
        if not _is_image(path):
            return json.dumps({"ok": False, "error": "Not a PNG, JPEG or WebP image."})
        ctx.state.set("screenshot", str(path))
        return json.dumps({"ok": True, "path": str(path)})

    ctx.register_command(
        "launch", launch,
        description="Turn what you just shipped into a launch post pack",
        args_hint="<what you shipped> [-- <one real detail>]",
    )
    ctx.register_tool(
        name="launch_desk_attach_screenshot",
        toolset="launch_desk",
        schema=SCREENSHOT_SCHEMA,
        handler=attach_screenshot,
        description="Attach a product screenshot for the next /launch share card.",
    )
