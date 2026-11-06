"""desktop/plugin.js: the Hermes Desktop half. Checked without Desktop by tools/desktop_check.mjs.

The render check needs Hermes' own React: set HERMES_AGENT_SRC to a hermes-agent checkout with
node_modules (e.g. /home/ops/launch-desk-hermes/hermes-agent). Without it, only the import and
parse checks run.
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_JS = ROOT / "desktop" / "plugin.js"
CHECK = ROOT / "tools" / "desktop_check.mjs"
ALLOWED = {"@hermes/plugin-sdk", "react", "react/jsx-runtime"}

NODE = shutil.which("node")
SRC = Path(os.environ["HERMES_AGENT_SRC"]) if os.environ.get("HERMES_AGENT_SRC") else None
HAS_REACT = bool(SRC and (SRC / "node_modules" / "react").is_dir() and (SRC / "node_modules" / "react-dom").is_dir())


def source() -> str:
    return PLUGIN_JS.read_text(encoding="utf-8")


def test_imports_only_what_the_desktop_loader_allows():
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", source(), flags=re.S)
    specs = re.findall(r"\bimport\s+(?:[^;]*?\bfrom\s*)?['\"]([^'\"]+)['\"]", code)
    specs += re.findall(r"\bexport\s+[^;]*?\bfrom\s*['\"]([^'\"]+)['\"]", code)
    assert specs and set(specs) <= ALLOWED
    assert not re.search(r"\bimport\s*\(", code)  # no dynamic imports
    assert not re.search(r"\b(eval|Function)\s*\(", code)


def test_plugin_id_matches_the_package_and_backend_names():
    plugin_id = re.search(r"\bconst ID = '([^']+)'", source()).group(1)
    yaml_name = re.search(r"^name:\s*(\S+)", (ROOT / "plugin.yaml").read_text(encoding="utf-8"), re.M).group(1)
    manifest = json.loads((ROOT / "dashboard" / "manifest.json").read_text(encoding="utf-8"))
    assert plugin_id == yaml_name == manifest["name"]
    assert re.search(r"\bid: ID\b", source())


def test_only_reads_from_its_backend():
    rests = re.findall(r"ctx\.rest\(([^)]*)\)", source())
    assert rests and all("method" not in r for r in rests)  # GET only: the backend is read-only
    assert "hermesDesktop" not in source()  # no raw native bridge


def run_check(*extra):
    return subprocess.run([NODE, str(CHECK), *extra], capture_output=True, text=True, timeout=120)


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_parses_as_an_es_module():
    result = run_check()
    report = json.loads(result.stdout)
    assert result.returncode == 0, report["failures"]
    assert report["parses"] is True


@pytest.mark.skipif(not (NODE and HAS_REACT), reason="needs node and HERMES_AGENT_SRC with node_modules")
def test_registers_a_page_and_renders_every_state():
    result = run_check("--react", str(SRC / "node_modules"), "--sdk-src", str(SRC / "apps" / "desktop" / "src"))
    report = json.loads(result.stdout)
    assert result.returncode == 0, report["failures"]
    areas = {c["area"]: c for c in report["contributions"]}
    assert areas["routes"]["path"] == areas["sidebar.nav"]["path"] == "/launch-desk"
    assert {"palette", "statusBar.right"} <= set(areas)
    assert report["sdkChecked"] and report["reactWarnings"] == []
    assert report["loaded"]["packs"] == 2 and report["loaded"]["founderMarked"] == 1
    assert report["states"] == {"empty": True, "error": True, "loading": True}
    assert report["chip"] == {"due": True, "hiddenWhenNone": True, "hiddenWhenOff": True}


@pytest.mark.skipif(not (NODE and HAS_REACT), reason="needs node and HERMES_AGENT_SRC with node_modules")
def test_renders_packs_from_the_real_backend_listing(tmp_path):
    from test_dashboard_api import api, jobs, job, make_pack  # noqa: PLC0415

    root = tmp_path / "packs"
    make_pack(root, "2026-11-06-a", 2_000, hook_source="founder",
              results=[{"channel": "X", "signups": 5}, {"channel": "LinkedIn", "signups": 1}])
    make_pack(root, "2026-11-05-b", 1_000, hook_source="model")
    path = jobs(tmp_path / "jobs.json", job("2026-11-06-a", "j1"), job("2026-11-06-a", "j2"))
    listing = api.list_packs(root, path)
    images = {p["name"]: {"card": api.data_url(api.pack_image(root, p["name"], "card")),
                          "preview": api.data_url(api.pack_image(root, p["name"], "preview"))}
              for p in listing["packs"]}
    data = tmp_path / "data.json"
    data.write_text(json.dumps({"packs": listing, "images": images}), encoding="utf-8")
    result = run_check("--react", str(SRC / "node_modules"), "--data", str(data), "--out", str(tmp_path / "p.html"))
    report = json.loads(result.stdout)
    assert result.returncode == 0, report["failures"]
    assert report["loaded"] == {"packs": 2, "founderMarked": 1, "copyButtons": 2, "images": 2,
                                "checkins": 2, "channels": 2, "emptyOrError": 0}
    assert (tmp_path / "p.html").is_file()
