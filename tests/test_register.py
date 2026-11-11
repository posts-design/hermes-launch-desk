import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

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


def test_a_stale_stored_path_never_takes_a_slot(tmp_path):
    a, b = _pngs(tmp_path, "a.png", "b.png")
    gone = str(tmp_path / "gone.png")
    ctx = StubCtx()
    ctx.state.set("screenshot", [a, gone])  # the file was deleted after it was attached
    load_plugin().register(ctx)
    ctx.tools["launch_desk_attach_screenshot"]["handler"]({"path": b})
    assert ctx.state.get("screenshot") == [a, b]


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


class BlueprintCtx(StubCtx):
    def __init__(self):
        super().__init__()
        self.blueprints = {}

    def register_automation_blueprint(self, key, **kwargs):
        self.blueprints[key] = kwargs


def test_register_adds_the_follow_up_tools_with_schemas():
    ctx = StubCtx()
    load_plugin().register(ctx)
    for name, required in (("launch_desk_followup", {"pack"}),
                           ("launch_desk_record_results", {"pack", "channel", "signups"})):
        tool = ctx.tools[name]
        assert tool["toolset"] == "launch_desk"
        assert tool["schema"]["name"] == name
        assert set(tool["schema"]["parameters"]["required"]) == required


def test_manifest_lists_every_registered_tool():
    import re
    ctx = StubCtx()
    load_plugin().register(ctx)
    manifest = (ROOT / "plugin.yaml").read_text(encoding="utf-8")
    listed = set(re.findall(r"^\s*-\s*(launch_desk_\w+)\s*$", manifest, re.M))
    assert listed == set(ctx.tools)


def test_follow_up_tools_read_and_write_packs_in_the_data_dir(tmp_path):
    ctx = StubCtx()
    ctx.state.data_dir = tmp_path / "data"
    load_plugin().register(ctx)
    folder = ctx.state.data_dir / "packs" / "p1"
    folder.mkdir(parents=True)
    (folder / "pack.json").write_text(json.dumps({"caption": "Hi", "hook": "Hi", "brief": {}}))
    record = ctx.tools["launch_desk_record_results"]["handler"]
    out = json.loads(record({"pack": "p1", "channel": "X", "signups": 3}))
    assert out["ok"] is True
    got = json.loads(ctx.tools["launch_desk_followup"]["handler"]({"pack": "p1"}))
    assert got["ok"] is True and got["results"][0]["signups"] == 3
    bad = json.loads(ctx.tools["launch_desk_followup"]["handler"]({"pack": "../p1"}))
    assert bad["ok"] is False


def test_follow_up_tools_never_raise_on_junk_arguments(tmp_path):
    ctx = StubCtx()
    ctx.state.data_dir = tmp_path
    load_plugin().register(ctx)
    for name in ("launch_desk_followup", "launch_desk_record_results"):
        for args in (None, {}, {"pack": 5}, "oops"):
            assert json.loads(ctx.tools[name]["handler"](args))["ok"] is False


def test_blueprint_is_registered_only_when_hermes_has_the_method():
    plain = StubCtx()
    load_plugin().register(plain)  # an older Hermes: no method, no error
    assert not hasattr(plain, "blueprints")

    ctx = BlueprintCtx()
    load_plugin().register(ctx)
    bp = ctx.blueprints["follow-up"]
    assert [s["name"] for s in bp["slots"]] == ["pack"]
    assert "{pack}" in bp["prompt_template"] and "launch_desk_followup" in bp["prompt_template"]
    assert bp["schedule_template"] and "{" not in bp["schedule_template"]
    assert "launch-desk:launch-desk" in bp["skills"]


def test_manifest_keeps_the_hermes_floor():
    manifest = (ROOT / "plugin.yaml").read_text(encoding="utf-8")
    assert 'requires_hermes: ">=0.22.6"' in manifest


def test_blueprint_pack_slot_is_optional_with_recent_packs_as_suggestions(tmp_path):
    ctx = BlueprintCtx()
    ctx.state.data_dir = tmp_path
    for n, (name, created) in enumerate((("2026-11-05-a", "2026-11-05T09:30:00+00:00"),
                                         ("2026-11-07-b", "2026-11-07T02:06:00+00:00"))):
        folder = tmp_path / "packs" / name
        folder.mkdir(parents=True)
        (folder / "pack.json").write_text(json.dumps({"caption": "Hi.", "hook": "Hi.", "brief": {},
                                                      "brand": {"name": "pawr.link"}, "created_at": created}))
    load_plugin().register(ctx)
    [slot] = ctx.blueprints["follow-up"]["slots"]
    plugin = load_plugin()
    assert slot["optional"] is True and slot["strict"] is False and slot["type"] == "enum"
    assert slot["default"] == plugin.followup.LATEST and slot["options"][0] == plugin.followup.LATEST
    assert slot["options"][1:] == plugin.followup.recent_labels(tmp_path / "packs")
    assert len(slot["options"]) == 3 and slot["help"] and slot["label"]


def test_blueprint_with_no_packs_offers_only_the_latest(tmp_path):
    ctx = BlueprintCtx()
    ctx.state.data_dir = tmp_path
    load_plugin().register(ctx)
    [slot] = ctx.blueprints["follow-up"]["slots"]
    assert slot["options"] == [load_plugin().followup.LATEST]


HERMES_SRC = os.environ.get("HERMES_AGENT_SRC")


@pytest.mark.skipif(not HERMES_SRC, reason="needs HERMES_AGENT_SRC (Hermes' cron.blueprint_plugins)")
def test_hermes_fills_the_blueprint_blank_label_or_name(tmp_path):
    sys.path.insert(0, HERMES_SRC)
    try:
        from cron.blueprint_catalog import BlueprintFillError, fill_blueprint
        from cron.blueprint_plugins import build_plugin_blueprint
    finally:
        sys.path.remove(HERMES_SRC)
    ctx = BlueprintCtx()
    ctx.state.data_dir = tmp_path
    load_plugin().register(ctx)
    kwargs = dict(ctx.blueprints["follow-up"])
    blueprint = build_plugin_blueprint("launch-desk", "follow-up", **kwargs)  # Hermes' own validation
    latest = load_plugin().followup.LATEST
    assert f'"{latest}"' in fill_blueprint(blueprint, {})["prompt"]  # the slot left out: the latest
    label = "pawr.link · 7 Nov 02:06"
    spec = fill_blueprint(blueprint, {"pack": label})
    assert f'"{label}"' in spec["prompt"] and spec["name"] == kwargs["title"]
    assert f'"2026-11-07-b"' in fill_blueprint(blueprint, {"pack": "2026-11-07-b"})["prompt"]
    with pytest.raises(BlueprintFillError):  # Hermes 0.22.7 drops a blank optional value before
        fill_blueprint(blueprint, {"pack": ""})  # formatting: an explicit "" cannot reach the prompt


def _png(path):
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), "white").save(path)
    return path


def test_screenshot_refuses_hermes_home_outside_its_media_caches(tmp_path):
    plugin = load_plugin()
    home = tmp_path / "hermes"
    assert plugin.screenshot_refusal(_png(home / "cache" / "images" / "img.png"), home.resolve()) is None
    assert plugin.screenshot_refusal(_png(home / "image_cache" / "img.png"), home.resolve()) is None
    assert plugin.screenshot_refusal(_png(home / "plugin-data" / "ns" / "card.png"), home.resolve()) is None
    assert plugin.screenshot_refusal(_png(home / "logs" / "x.png"), home.resolve())
    assert plugin.screenshot_refusal(_png(home / "x.png"), home.resolve())


def test_screenshot_refuses_hidden_folders_and_symlinks(tmp_path):
    plugin = load_plugin()
    user = tmp_path / "user"
    assert plugin.screenshot_refusal(_png(user / ".ssh" / "k.png"), None, user)
    assert plugin.screenshot_refusal(_png(user / "Desktop" / "shot.png"), None, user) is None
    link = user / "Desktop" / "link.png"
    link.symlink_to(user / "Desktop" / "shot.png")
    assert plugin.screenshot_refusal(link, None, user)


def test_attach_refuses_a_file_in_hermes_home(tmp_path):
    class Ctx(StubCtx):
        def __init__(self, data):
            super().__init__()
            self.state.data_dir = str(data)

    home = tmp_path / "hermes"
    ctx = Ctx(home / "plugin-data" / "agent-plugin-launch-desk")
    load_plugin().register(ctx)
    handler = ctx.tools["launch_desk_attach_screenshot"]["handler"]
    assert json.loads(handler({"path": str(_png(home / "auth" / "x.png"))}))["ok"] is False
    ok = json.loads(handler({"path": str(_png(home / "cache" / "images" / "img.png"))}))
    assert ok["ok"] is True
