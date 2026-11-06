"""Launch Desk: a posts.design plugin for Hermes Agent."""
from __future__ import annotations

import json
from pathlib import Path

try:
    from .launch_desk import followup, wizard
    from .launch_desk.host import HermesHost
except ImportError:  # imported as a top-level module (pytest collects the repo root)
    from launch_desk import followup, wizard
    from launch_desk.host import HermesHost

SCREENSHOT_SCHEMA = {
    "name": "launch_desk_attach_screenshot",
    "description": ("Attach a product screenshot (PNG, JPEG or WebP) for the next /launch share card. "
                    "Up to two are kept; two make a side-by-side card, and a third replaces the oldest."),
    "parameters": {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Path to the image file."}},
        "required": ["path"],
    },
}


FOLLOWUP_SCHEMA = {
    "name": "launch_desk_followup",
    "description": ("Read an approved Launch Desk pack for a check-in: the post, its X link, the unused "
                    "hooks, references not shown, the after-post check, any recorded sign-ups and up to "
                    "three next-post suggestions taken from the pack. Phrase the check-in from this only."),
    "parameters": {
        "type": "object",
        "properties": {"pack": {"type": "string", "description": "The pack's name, as in the check-in."}},
        "required": ["pack"],
    },
}
RECORD_SCHEMA = {
    "name": "launch_desk_record_results",
    "description": ("Record how many sign-ups one channel brought for a Launch Desk pack, when the founder "
                    "replies to a check-in with numbers. Call once per channel."),
    "parameters": {
        "type": "object",
        "properties": {
            "pack": {"type": "string", "description": "The pack's name, as in the check-in."},
            "channel": {"type": "string", "description": "Where it was posted, like X or LinkedIn (40 characters at most)."},
            "signups": {"type": "integer", "minimum": 0, "maximum": followup.SIGNUPS_MAX,
                        "description": "Sign-ups from that channel."},
        },
        "required": ["pack", "channel", "signups"],
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
        kept = [p for p in wizard.screenshot_paths(ctx.state.get("screenshot"))
                if p != str(path) and Path(p).is_file()]  # a deleted file never takes a slot
        ctx.state.set("screenshot", (kept + [str(path)])[-wizard.SCREENSHOTS_MAX:])
        return json.dumps({"ok": True, "path": str(path)})

    def packs_root() -> Path:
        return HermesHost(ctx).data_dir() / "packs"

    def _args(args) -> dict:
        return args if isinstance(args, dict) else {}

    def read_followup(args, **kwargs):
        a = _args(args)
        try:
            return json.dumps(followup.followup(packs_root(), a.get("pack")), ensure_ascii=False)
        except Exception:
            return json.dumps({"ok": False, "error": "The pack could not be read."})

    def record_results(args, **kwargs):
        a = _args(args)
        try:
            out = followup.record_results(packs_root(), a.get("pack"), a.get("channel"), a.get("signups"))
            return json.dumps(out, ensure_ascii=False)
        except Exception:
            return json.dumps({"ok": False, "error": "The result could not be saved."})

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
    ctx.register_tool(
        name="launch_desk_followup",
        toolset="launch_desk",
        schema=FOLLOWUP_SCHEMA,
        handler=read_followup,
        description="Read an approved launch pack for a check-in.",
    )
    ctx.register_tool(
        name="launch_desk_record_results",
        toolset="launch_desk",
        schema=RECORD_SCHEMA,
        handler=record_results,
        description="Record sign-ups per channel for a launch pack.",
    )
    if hasattr(ctx, "register_automation_blueprint"):  # Hermes 0.22.7 and later
        ctx.register_automation_blueprint(
            "follow-up",
            title="Launch Desk check-in",
            description=("One check-in on a launch pack, a day from now: asks how many sign-ups each "
                         "channel brought and suggests one next post from the pack."),
            category="work",
            schedule_template="in 24h",
            prompt_template=followup.PROMPT,
            slots=[{"name": "pack", "type": "text", "label": "Which launch pack (its folder name)?"}],
            deliver_default="origin",
            skills=[followup.SKILL],
            tags=["launch", "follow-up"],
        )
    skill = Path(__file__).resolve().parent / "skills" / "launch-desk" / "SKILL.md"
    if skill.is_file() and hasattr(ctx, "register_skill"):
        ctx.register_skill(
            "launch-desk", skill,
            "When a founder has shipped something and wants to announce it: the launch workflow and /launch.",
        )
