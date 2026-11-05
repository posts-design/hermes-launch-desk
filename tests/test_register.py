import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_plugin():
    spec = importlib.util.spec_from_file_location(
        "launch_desk_plugin", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StubState:
    def __init__(self):
        self.values = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


class StubCtx:
    def __init__(self):
        self.commands = {}
        self.tools = {}
        self.skills = {}
        self.state = StubState()

    def register_command(self, name, handler, description="", args_hint=""):
        self.commands[name] = handler

    def register_tool(self, name, toolset, schema, handler, **kwargs):
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler}


    def register_skill(self, name, path, description=""):
        self.skills[name] = path


def test_register_adds_skill_with_existing_file():
    ctx = StubCtx()
    load_plugin().register(ctx)
    assert Path(ctx.skills["launch-desk"]).is_file()


def test_register_adds_launch_command_and_tool():
    ctx = StubCtx()
    load_plugin().register(ctx)
    assert "launch" in ctx.commands
    assert "launch_desk_attach_screenshot" in ctx.tools
    assert ctx.tools["launch_desk_attach_screenshot"]["toolset"] == "launch_desk"


def test_attach_screenshot_stores_valid_image(tmp_path):
    from PIL import Image

    png = tmp_path / "shot.png"
    Image.new("RGB", (8, 8), "white").save(png)
    ctx = StubCtx()
    load_plugin().register(ctx)
    handler = ctx.tools["launch_desk_attach_screenshot"]["handler"]
    result = json.loads(handler({"path": str(png)}))
    assert result["ok"] is True
    assert ctx.state.get("screenshot") == [str(png)]


def _pngs(tmp_path, *names):
    from PIL import Image

    out = []
    for name in names:
        p = tmp_path / name
        Image.new("RGB", (8, 8), "white").save(p)
        out.append(str(p))
    return out


def test_attach_keeps_two_screenshots_and_drops_the_oldest(tmp_path):
    a, b, c = _pngs(tmp_path, "a.png", "b.png", "c.png")
    ctx = StubCtx()
    load_plugin().register(ctx)
    handler = ctx.tools["launch_desk_attach_screenshot"]["handler"]
    handler({"path": a})
    handler({"path": b})
    assert ctx.state.get("screenshot") == [a, b]
    handler({"path": c})
    assert ctx.state.get("screenshot") == [b, c]
    handler({"path": b})  # attaching one again makes it the newest, never a duplicate
    assert ctx.state.get("screenshot") == [c, b]


def test_attach_keeps_a_screenshot_stored_by_an_older_version(tmp_path):
    old, new = _pngs(tmp_path, "old.png", "new.png")
    ctx = StubCtx()
    ctx.state.set("screenshot", old)  # older versions stored one path as a string
    load_plugin().register(ctx)
    ctx.tools["launch_desk_attach_screenshot"]["handler"]({"path": new})
    assert ctx.state.get("screenshot") == [old, new]


def test_attach_screenshot_rejects_missing_and_non_image(tmp_path):
    ctx = StubCtx()
    load_plugin().register(ctx)
    handler = ctx.tools["launch_desk_attach_screenshot"]["handler"]
    missing = json.loads(handler({"path": str(tmp_path / "nope.png")}))
    assert missing["ok"] is False
    text = tmp_path / "a.png"
    text.write_text("not an image")
    bad = json.loads(handler({"path": str(text)}))
    assert bad["ok"] is False
    assert ctx.state.get("screenshot") is None
