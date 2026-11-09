"""dashboard/plugin_api.py: the read-only backend behind the Hermes Desktop page."""
import base64
import importlib.util
import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

API_PATH = Path(__file__).resolve().parents[1] / "dashboard" / "plugin_api.py"
MANIFEST = Path(__file__).resolve().parents[1] / "dashboard" / "manifest.json"


def load_api():
    name = "launch_desk_dashboard_api_under_test"
    spec = importlib.util.spec_from_file_location(name, API_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


api = load_api()


def png(color=(20, 20, 20), size=(12, 8)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="PNG")
    return out.getvalue()


CARD, PREVIEW = png((200, 10, 10)), png((10, 200, 10), (8, 12))


def make_pack(root: Path, name: str, created: float, **fields) -> Path:
    """A pack folder as pack.write_pack lays it out; every file stamped `created`."""
    folder = root / name
    folder.mkdir(parents=True)
    data = {
        "brief": {"shipped": "feature", "network": "x", "what": "custom themes",
                  "detail": "people kept asking", "product_url": "https://pawr.link"},
        "brand": {"name": "pawr.link", "url": "https://pawr.link"},
        "hook": "You asked for matching themes. It's here.",
        "hooks": ["One.", "You asked for matching themes. It's here.", "Three."],
        "caption": "caption from pack.json",
        "card": {"layout": "statement", "screenshots": 0},
        "references": [
            {"id": "a", "brand": "Perplexity", "url": "https://posts.design/a", "views": 42749},
            {"id": "b", "brand": "Notion", "url": "https://posts.design/b", "views": 20483},
            {"id": "c", "brand": "beehiiv", "url": "https://posts.design/c", "views": 4256},
        ],
        "intent_url": "https://x.com/intent/post?text=hi",
        "files": {"post": "post-x.txt", "alt_text": "alt-text.txt", "card_x": "card-x.png",
                  "preview": "preview-x.png", "pack": "pack.json"},
    }
    data.update(fields)
    (folder / "pack.json").write_text(json.dumps(data), encoding="utf-8")
    (folder / "post-x.txt").write_text("Post text from the file.\n\nhttps://pawr.link\n", encoding="utf-8")
    (folder / "alt-text.txt").write_text("Launch card for pawr.link.", encoding="utf-8")
    (folder / "card-x.png").write_bytes(CARD)
    (folder / "preview-x.png").write_bytes(PREVIEW)
    for f in folder.iterdir():
        os.utime(f, (created, created))
    return folder


def jobs(path: Path, *records) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"jobs": list(records), "updated_at": "2026-11-06T00:00:00+00:00"}),
                    encoding="utf-8")
    return path


def job(pack, job_id, **extra):
    record = {"id": job_id, "name": f"Launch Desk check-in: {pack}", "enabled": True,
              "state": "scheduled", "next_run_at": "2026-11-07T10:00:00+00:00",
              "last_run_at": None, "last_status": None}
    record.update(extra)
    return record


def snapshot(root: Path) -> dict:
    return {str(p): (p.stat().st_mtime_ns, p.read_bytes() if p.is_file() else None)
            for p in sorted(root.rglob("*"))}


# ------------------------------------------------------------------ manifest

def test_manifest_mounts_this_plugin_api_without_a_dashboard_tab():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["name"] == "launch-desk"  # /api/plugins/launch-desk, the Desktop ctx.rest namespace
    assert manifest["api"] == "plugin_api.py"
    assert manifest["tab"]["hidden"] is True
    assert (MANIFEST.parent / manifest["entry"]).is_file()


def test_data_dir_matches_the_plugin_state_folder_name(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "hermes_cli.plugins_state", None)  # force the spelled-out rule
    monkeypatch.setattr(api, "hermes_home", lambda: tmp_path)
    assert api.packs_root() == tmp_path / "plugin-data" / "agent-plugin-launch-desk-41b438e5" / "packs"
    assert api.jobs_file() == tmp_path / "cron" / "jobs.json"


# ------------------------------------------------------------------ listing

def test_lists_packs_newest_first_with_the_fields_the_pane_shows(tmp_path):
    make_pack(tmp_path, "2026-11-05-pawr-link-themes", 1_000)
    make_pack(tmp_path, "2026-11-06-pawr-link-themes", 3_000)
    make_pack(tmp_path, "2026-11-05-pawr-link-themes-2", 2_000)
    listing = api.list_packs(tmp_path)
    names = [p["name"] for p in listing["packs"]]
    assert names == ["2026-11-06-pawr-link-themes", "2026-11-05-pawr-link-themes-2",
                     "2026-11-05-pawr-link-themes"]
    pack = listing["packs"][0]
    assert pack["date"] == "2026-11-06"
    assert pack["post"] == "Post text from the file.\n\nhttps://pawr.link"
    assert pack["hook"] == "You asked for matching themes. It's here."
    assert pack["hook_source"] is None
    assert pack["checks"] is None
    assert pack["product"] == "pawr.link"
    assert pack["layout"] == "statement"
    assert pack["intent_url"] == "https://x.com/intent/post?text=hi"
    assert pack["references"] == [
        {"brand": "Perplexity", "views": 42749, "url": "https://posts.design/a"},
        {"brand": "Notion", "views": 20483, "url": "https://posts.design/b"},
        {"brand": "beehiiv", "views": 4256, "url": "https://posts.design/c"},
    ]
    assert pack["has_card"] and pack["has_preview"]
    assert pack["alt_text"] == "Launch card for pawr.link."
    assert pack["signups"] == [] and pack["checkins"] == []
    assert listing["checkins"] == {"next_run_at": None, "missed": 0}
    # Only what the page shows goes out.
    assert set(pack) == {"name", "date", "created", "product", "post", "hook", "hook_source", "checks",
                         "references", "layout", "intent_url", "alt_text", "has_card", "has_preview",
                         "signups", "checkins"}


def test_recording_results_later_does_not_move_a_pack_to_the_top(tmp_path):
    old = make_pack(tmp_path, "2026-11-05-a", 1_000)
    make_pack(tmp_path, "2026-11-05-b", 2_000)
    os.utime(old / "pack.json", (9_000, 9_000))  # launch_desk_record_results rewrote it
    assert [p["name"] for p in api.list_packs(tmp_path)["packs"]] == ["2026-11-05-b", "2026-11-05-a"]


def test_missing_root_lists_nothing(tmp_path):
    assert api.list_packs(tmp_path / "packs", tmp_path / "cron" / "jobs.json") == {
        "packs": [], "checkins": {"next_run_at": None, "missed": 0}}


def test_missing_and_wrong_typed_fields_fall_back(tmp_path):
    folder = tmp_path / "2026-11-06-bare"
    folder.mkdir()
    (folder / "pack.json").write_text(json.dumps({
        "hook": 5, "hooks": "nope", "references": [None, {"brand": 3, "views": "many"}],
        "card": [], "intent_url": "javascript:alert(1)", "files": {"post": 7}, "results": {"x": 1},
        "caption": "From the caption.",
    }), encoding="utf-8")
    pack = api.list_packs(tmp_path)["packs"][0]
    assert pack["post"] == "From the caption."  # no post file: the caption
    assert pack["hook"] == ""
    assert pack["references"] == [{"brand": "", "views": None, "url": ""}]
    assert pack["layout"] is None and pack["intent_url"] is None
    assert pack["has_card"] is False and pack["has_preview"] is False
    assert pack["signups"] == [] and pack["hook_source"] is None and pack["checks"] is None
    assert pack["date"] == "2026-11-06"


def test_hook_source_and_the_saved_checklist_are_passed_through(tmp_path):
    # The shape /launch saves (launch_desk.models.CheckLine): {rules, ok, text}.
    make_pack(tmp_path, "2026-11-06-founder", 1_000, hook_source="founder", checks=[
        {"rules": ["first-line-words"], "ok": True, "text": "First line: 10 words"},
        {"rules": ["x-length"], "ok": False, "text": "Fits X: 290/280"},
        {"rules": [], "ok": "yes", "text": "Odd flag"},
        {"rules": ["no-text"], "ok": True},
        "a bare string",
    ])
    pack = api.list_packs(tmp_path)["packs"][0]
    assert pack["hook_source"] == "founder"
    assert pack["checks"] == [
        {"text": "First line: 10 words", "ok": True},
        {"text": "Fits X: 290/280", "ok": False},
        {"text": "Odd flag", "ok": None},
    ]


@pytest.mark.parametrize("url", ["file:///etc/passwd", "/Applications/Calculator.app", "~/Desktop/x.sh",
                                 "javascript:alert(1)", "http://posts.design/a", "smb://host/share",
                                 "\\\\host\\share", " ", None, 7])
def test_only_https_links_reach_the_page(tmp_path, url):
    make_pack(tmp_path, "2026-11-06-links", 1_000, intent_url=url,
              references=[{"brand": "Bad", "url": url, "views": 1},
                          {"brand": "Good", "url": "https://posts.design/ok", "views": 2}])
    pack = api.list_packs(tmp_path)["packs"][0]
    assert [r["url"] for r in pack["references"]] == ["", "https://posts.design/ok"]
    assert pack["intent_url"] is None


def test_signups_show_the_latest_number_per_channel(tmp_path):
    make_pack(tmp_path, "2026-11-06-r", 1_000, results=[
        {"channel": "X", "signups": 4, "recorded_at": "2026-11-07T10:00:00+00:00"},
        {"channel": "LinkedIn", "signups": 2, "recorded_at": "2026-11-07T10:00:00+00:00"},
        {"channel": "x", "signups": 12, "recorded_at": "2026-11-09T10:00:00+00:00"},
        {"channel": "Reddit", "signups": "lots"},
    ])
    pack = api.list_packs(tmp_path)["packs"][0]
    assert [(s["channel"], s["signups"]) for s in pack["signups"]] == [("X", 12), ("LinkedIn", 2)]


def test_skips_folders_that_are_not_packs(tmp_path):
    root = tmp_path / "packs"
    make_pack(root, "2026-11-06-good", 1_000)
    (root / "no-pack-json").mkdir()
    bad = root / "2026-11-06-bad-json"
    bad.mkdir()
    (bad / "pack.json").write_text("{not json", encoding="utf-8")
    make_pack(root, ".hidden-pack", 1_000)  # a name /launch never writes
    make_pack(root, "2026-11-06 has spaces", 1_000)
    (root / "stray.txt").write_text("x", encoding="utf-8")
    outside = make_pack(tmp_path / "elsewhere", "2026-11-06-outside", 1_000)
    (root / "2026-11-06-linked").symlink_to(outside, target_is_directory=True)
    assert [p["name"] for p in api.list_packs(root)["packs"]] == ["2026-11-06-good"]
    assert api.pack_image(root, "2026-11-06-linked", "card") is None


def test_a_linked_pack_json_is_not_read(tmp_path):
    secret = tmp_path / "secret.json"
    secret.write_text(json.dumps({"hook": "not yours"}), encoding="utf-8")
    folder = tmp_path / "packs" / "2026-11-06-link"
    folder.mkdir(parents=True)
    (folder / "pack.json").symlink_to(secret)
    assert api.list_packs(tmp_path / "packs")["packs"] == []


def test_one_pack_name_is_checked_without_listing_every_pack(tmp_path, monkeypatch):
    root = tmp_path / "packs"
    for i in range(5):
        make_pack(root, f"2026-11-0{i + 1}-p", 1_000 + i)
    calls = []
    real = os.listdir
    monkeypatch.setattr(api.os, "listdir", lambda p: calls.append(Path(p)) or real(p))
    assert api.pack_image(root, "2026-11-03-p", "card") == CARD
    assert calls.count(root) == 1 and len(calls) <= 4  # root once, then only that pack's folder


def test_reads_refuse_a_link_even_when_asked_directly(tmp_path):
    (tmp_path / "real.png").write_bytes(CARD)
    (tmp_path / "link.png").symlink_to(tmp_path / "real.png")
    assert api._read_bytes(tmp_path / "real.png", 1_000_000) == CARD
    assert api._read_bytes(tmp_path / "link.png", 1_000_000) is None  # O_NOFOLLOW
    assert api._read_bytes(tmp_path, 1_000_000) is None  # not a regular file


# ------------------------------------------------------------------ check-ins

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)


def test_check_ins_come_from_hermes_jobs_by_name(tmp_path):
    root = tmp_path / "packs"
    make_pack(root, "2026-11-06-a", 2_000)
    make_pack(root, "2026-11-05-b", 1_000)
    path = jobs(tmp_path / "cron" / "jobs.json",
                job("2026-11-06-a", "j72", next_run_at="2026-11-09T10:00:00+00:00"),
                job("2026-11-06-a", "j24", next_run_at="2026-11-08T10:00:00+00:00"),
                job("2026-11-05-b", "jdone", state="completed", next_run_at=None,
                    last_run_at="2026-11-06T09:00:00+00:00", last_status="ok"),
                job("2026-11-05-b", "joff", enabled=False),
                job("2026-11-05-b", "jmarked", paused_at="2026-11-06T08:00:00+00:00"),  # enabled, but held
                job("2026-11-05-b", "jstate", state="paused"),
                job("2026-11-05-b", "jzero", enabled=0),
                {"id": "other", "name": "Morning brief", "state": "scheduled",
                 "next_run_at": "2026-11-07T07:00:00+00:00"},
                "junk")
    listing = api.list_packs(root, path, NOW)
    a, b = listing["packs"]
    assert [c["id"] for c in a["checkins"]] == ["j24", "j72"]  # soonest first
    assert all(c["state"] == "scheduled" and not c["missed"] for c in a["checkins"])
    assert {c["id"]: c["state"] for c in b["checkins"]} == {
        "jdone": "completed", "joff": "paused", "jmarked": "paused", "jstate": "paused", "jzero": "paused"}
    assert listing["checkins"] == {"next_run_at": "2026-11-08T10:00:00+00:00", "missed": 0}


def test_a_check_in_past_its_time_that_never_ran_is_missed(tmp_path):
    root = tmp_path / "packs"
    make_pack(root, "2026-11-05-a", 1_000)
    path = jobs(tmp_path / "jobs.json",
                job("2026-11-05-a", "late", next_run_at="2026-11-07T09:00:00+00:00"),
                job("2026-11-05-a", "just-now", next_run_at="2026-11-07T11:58:00+00:00"),  # within the grace
                job("2026-11-05-a", "ran", next_run_at="2026-11-07T08:00:00+00:00",
                    last_run_at="2026-11-07T08:00:05+00:00"),
                job("2026-11-05-a", "paused-late", enabled=False, next_run_at="2026-11-06T09:00:00+00:00"),
                job("2026-11-05-a", "naive", next_run_at="2026-11-06T09:00:00"),
                job("2026-11-05-a", "next", next_run_at="2026-11-10T09:00:00+00:00"))
    listing = api.list_packs(root, path, NOW)
    missed = {c["id"]: c["missed"] for c in listing["packs"][0]["checkins"]}
    assert missed == {"late": True, "just-now": False, "ran": False, "paused-late": False,
                      "naive": True, "next": False}
    assert listing["checkins"] == {"next_run_at": "2026-11-10T09:00:00+00:00", "missed": 2}


@pytest.mark.parametrize("content", ["", "{broken", "[]", json.dumps({"jobs": "nope"})])
def test_unreadable_jobs_file_means_no_check_ins(tmp_path, content):
    root = tmp_path / "packs"
    make_pack(root, "2026-11-06-a", 1_000)
    path = tmp_path / "jobs.json"
    path.write_text(content, encoding="utf-8")
    listing = api.list_packs(root, path)
    assert listing["packs"][0]["checkins"] == [] and listing["checkins"]["missed"] == 0


# ------------------------------------------------------------------ images and traversal

def test_images_come_only_from_inside_the_named_pack(tmp_path):
    make_pack(tmp_path, "2026-11-06-a", 1_000)
    assert api.pack_image(tmp_path, "2026-11-06-a", "card") == CARD
    assert api.pack_image(tmp_path, "2026-11-06-a", "preview") == PREVIEW
    for name in ["../2026-11-06-a", "2026-11-06-a/..", "..", ".", "", "/etc", "2026-11-06-a/card-x.png",
                 "2026-11-06-missing", None, 5]:
        assert api.pack_image(tmp_path, name, "card") is None, name
    assert api.pack_image(tmp_path, "2026-11-06-a", "pack") is None


@pytest.mark.parametrize("card_name", ["../../outside.png", "/etc/passwd", "sub/card.png", "pack.json",
                                       "missing.png", "linked.png", "fake.png"])
def test_card_paths_that_leave_the_folder_or_are_not_png_are_refused(tmp_path, card_name):
    folder = make_pack(tmp_path / "packs", "2026-11-06-a", 1_000)
    data = json.loads((folder / "pack.json").read_text(encoding="utf-8"))
    data["files"]["card_x"] = card_name
    (folder / "pack.json").write_text(json.dumps(data), encoding="utf-8")
    (tmp_path / "outside.png").write_bytes(CARD)
    (folder / "linked.png").symlink_to(tmp_path / "outside.png")
    (folder / "fake.png").write_bytes(b"not a png at all")
    assert api.pack_image(tmp_path / "packs", "2026-11-06-a", "card") is None
    assert api.list_packs(tmp_path / "packs")["packs"][0]["has_card"] is (card_name == "fake.png")


def test_reading_never_changes_the_packs_folder(tmp_path):
    root = tmp_path / "packs"
    make_pack(root, "2026-11-06-a", 1_000, results=[{"channel": "X", "signups": 3}])
    path = jobs(tmp_path / "cron" / "jobs.json", job("2026-11-06-a", "j1"))
    before, jobs_before = snapshot(root), path.read_bytes()
    api.list_packs(root, path)
    api.pack_image(root, "2026-11-06-a", "card")
    api.data_url(api.pack_image(root, "2026-11-06-a", "preview"))
    assert snapshot(root) == before and path.read_bytes() == jobs_before


# ------------------------------------------------------------------ the mounted routes

@pytest.fixture
def client(tmp_path, monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    root = tmp_path / "packs"
    make_pack(root, "2026-11-05-older", 1_000)
    make_pack(root, "2026-11-06-newer", 2_000, hook_source="founder")
    make_pack(tmp_path, "secret-pack", 3_000)  # beside packs/, never reachable
    jobs(tmp_path / "cron" / "jobs.json", job("2026-11-06-newer", "j1", next_run_at="2099-01-01T00:00:00+00:00"))
    monkeypatch.setattr(api, "packs_root", lambda: root)
    monkeypatch.setattr(api, "jobs_file", lambda: tmp_path / "cron" / "jobs.json")
    app = fastapi.FastAPI()
    app.include_router(api._build_router(), prefix="/api/plugins/launch-desk")
    return TestClient(app)


def test_route_lists_packs(client):
    res = client.get("/api/plugins/launch-desk/packs")
    assert res.status_code == 200
    body = res.json()
    assert [p["name"] for p in body["packs"]] == ["2026-11-06-newer", "2026-11-05-older"]
    assert body["packs"][0]["hook_source"] == "founder"
    assert [c["id"] for c in body["packs"][0]["checkins"]] == ["j1"]
    assert body["checkins"] == {"next_run_at": "2099-01-01T00:00:00+00:00", "missed": 0}


def test_routes_serve_the_card_and_preview_pngs(client):
    card = client.get("/api/plugins/launch-desk/packs/2026-11-06-newer/card")
    preview = client.get("/api/plugins/launch-desk/packs/2026-11-06-newer/preview")
    assert card.status_code == 200 and card.headers["content-type"] == "image/png" and card.content == CARD
    assert preview.status_code == 200 and preview.content == PREVIEW


def test_images_route_returns_one_image_as_a_data_url(client):
    base = "/api/plugins/launch-desk/packs/2026-11-05-older/images"
    card, preview = client.get(base).json(), client.get(base + "?kind=preview").json()
    assert card["kind"] == "card" and card["url"].startswith("data:image/png;base64,")
    assert base64.b64decode(card["url"].split(",", 1)[1]) == CARD
    assert base64.b64decode(preview["url"].split(",", 1)[1]) == PREVIEW
    assert client.get(base + "?kind=pack").status_code == 404


@pytest.mark.parametrize("path", [
    "/packs/..%2Fsecret-pack/card", "/packs/%2E%2E/card", "/packs/secret-pack/card",
    "/packs/2026-11-06-missing/preview", "/packs/2026-11-06-newer/pack.json",
    "/packs/..%2F..%2Fcron/images", "/packs/secret-pack/images?kind=card", "/packs/2026-11-06-newer%2F..%2F..%2Fsecret-pack/card",
])
def test_routes_refuse_anything_outside_the_packs(client, path):
    res = client.get("/api/plugins/launch-desk" + path)
    assert res.status_code == 404
    assert res.content != CARD


def test_only_get_routes_exist(client):
    assert client.post("/api/plugins/launch-desk/packs", json={}).status_code == 405
    assert client.delete("/api/plugins/launch-desk/packs/2026-11-06-newer/card").status_code == 405



def test_your_own_launch_reference_carries_its_sign_ups_and_pack(tmp_path):
    make_pack(tmp_path, "2026-11-07-own", 1_000, references=[
        {"id": "own:2026-11-02-a", "brand": "Your own launch", "url": "2026-11-02-a", "views": None,
         "signups": 12},
        {"id": "own:bad", "brand": "Your own launch", "url": "../../etc", "views": None, "signups": 3},
        {"id": "a", "brand": "Perplexity", "url": "https://posts.design/a", "views": 42749},
    ])
    refs = api.list_packs(tmp_path)["packs"][0]["references"]
    assert refs[0] == {"brand": "Your own launch", "views": None, "url": "", "signups": 12, "pack": "2026-11-02-a"}
    assert refs[1]["signups"] == 3 and refs[1]["pack"] == ""
    assert refs[2] == {"brand": "Perplexity", "views": 42749, "url": "https://posts.design/a"}


@pytest.mark.parametrize("signups", [-1, True, "12", 1.5])
def test_a_malformed_own_sign_up_count_is_left_out(tmp_path, signups):
    make_pack(tmp_path, "2026-11-07-own", 1_000, references=[
        {"brand": "Your own launch", "url": "2026-11-02-a", "views": None, "signups": signups}])
    ref = api.list_packs(tmp_path)["packs"][0]["references"][0]
    assert "signups" not in ref and "pack" not in ref


# ------------------------------------------------------------------ R57: check-ins made from the blueprint

def blueprint_job(job_id, pack_value, **extra):
    from launch_desk import followup
    record = {"id": job_id, "name": "Launch Desk check-in", "enabled": True, "state": "scheduled",
              "next_run_at": "2026-11-08T10:00:00+00:00", "last_run_at": None,
              "prompt": followup.BLUEPRINT_PROMPT.format(pack=pack_value)}
    record.update(extra)
    return record


def test_blueprint_check_ins_are_matched_by_label_name_or_latest(tmp_path):
    from launch_desk import followup
    root = tmp_path / "packs"
    make_pack(root, "2026-11-07-new", 2_000, created_at="2026-11-07T02:06:00+00:00")
    make_pack(root, "2026-11-05-old", 1_000, created_at="2026-11-05T09:30:00+00:00",
              brand={"name": "Brookhour", "url": "https://brookhour.app"})
    old_label = followup.recent_labels(root)[1]
    path = jobs(tmp_path / "jobs.json",
                blueprint_job("by-latest", followup.LATEST),
                blueprint_job("by-label", old_label),
                blueprint_job("by-name", "2026-11-05-old"),
                blueprint_job("unknown", "no such pack"),
                {"id": "other", "name": "Launch Desk check-in", "prompt": "something else"})
    listing = api.list_packs(root, path, NOW)
    by_pack = {p["name"]: sorted(c["id"] for c in p["checkins"]) for p in listing["packs"]}
    assert by_pack == {"2026-11-07-new": ["by-latest"], "2026-11-05-old": ["by-label", "by-name"]}


def test_the_dashboard_labels_packs_as_the_plugin_does(tmp_path):
    from launch_desk import followup
    root = tmp_path / "packs"
    make_pack(root, "2026-11-07-new", 2_000, created_at="2026-11-07T02:06:00+00:00")
    make_pack(root, "2026-11-06-same", 1_500, created_at="2026-11-07T02:06:00+00:00")
    make_pack(root, "2026-11-05-old", 1_000)  # no created_at: the folder date
    assert api.pack_labels(root) == dict(zip(followup.recent_labels(root, limit=99),
                                             ["2026-11-07-new", "2026-11-06-same", "2026-11-05-old"]))


def test_the_listing_caps_before_reading_every_pack(tmp_path, monkeypatch):
    root = tmp_path / "packs"
    for n in range(api.MAX_PACKS + 5):
        (root / f"2026-11-{n % 28 + 1:02d}-p{n:03d}").mkdir(parents=True)
        (root / f"2026-11-{n % 28 + 1:02d}-p{n:03d}" / "pack.json").write_text("{}", encoding="utf-8")
    seen = []
    real = api.summarize_pack
    monkeypatch.setattr(api, "summarize_pack", lambda folder, checkins=None: seen.append(folder) or real(folder, checkins))
    listing = api.list_packs(root)
    assert len(seen) <= api.MAX_PACKS and len(listing["packs"]) <= api.MAX_PACKS


def test_labels_fall_back_to_the_site_then_the_folder_as_the_plugin_does(tmp_path):
    from launch_desk import followup
    root = tmp_path / "packs"
    make_pack(root, "2026-11-07-a", 3_000, created_at="2026-11-07T02:06:00+00:00", brand={"url": "https://www.pawr.link"})
    make_pack(root, "2026-11-06-b", 2_000, created_at="2026-11-06T02:06:00+00:00", brand={},
              brief={"product_url": "http://127.0.0.1:8731/k3/"})
    make_pack(root, "2026-11-05-c", 1_000, created_at="2026-11-05T02:06:00+00:00", brand={}, brief={})
    assert api.pack_labels(root) == dict(zip(followup.recent_labels(root, limit=99),
                                             ["2026-11-07-a", "2026-11-06-b", "2026-11-05-c"]))
    assert list(api.pack_labels(root))[0].startswith("pawr.link")


def test_a_latest_check_in_stays_with_the_pack_that_was_newest_when_it_was_made(tmp_path):
    from launch_desk import followup
    root = tmp_path / "packs"
    make_pack(root, "2026-11-05-old", 1_000, created_at="2026-11-05T09:00:00+00:00")
    make_pack(root, "2026-11-07-new", 2_000, created_at="2026-11-07T09:00:00+00:00")
    path = jobs(tmp_path / "jobs.json",
                blueprint_job("made-on-6th", followup.LATEST, created_at="2026-11-06T12:00:00+00:00"),
                blueprint_job("made-on-8th", followup.LATEST, created_at="2026-11-08T12:00:00+00:00"),
                blueprint_job("no-date", followup.LATEST))
    by_pack = {p["name"]: sorted(c["id"] for c in p["checkins"]) for p in api.list_packs(root, path, NOW)["packs"]}
    assert by_pack == {"2026-11-07-new": ["made-on-8th", "no-date"], "2026-11-05-old": ["made-on-6th"]}


def test_labels_are_built_only_when_a_blueprint_check_in_exists(tmp_path, monkeypatch):
    root = tmp_path / "packs"
    make_pack(root, "2026-11-06-a", 2_000)
    path = jobs(tmp_path / "jobs.json", job("2026-11-06-a", "j1"))
    built = []
    real = api.pack_index
    monkeypatch.setattr(api, "pack_index", lambda r: built.append(r) or real(r))
    api.list_packs(root, path, NOW)
    assert built == []
    from launch_desk import followup
    api.list_packs(root, jobs(tmp_path / "jobs2.json", blueprint_job("b1", followup.LATEST)), NOW)
    assert len(built) == 1
