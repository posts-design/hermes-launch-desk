import importlib.util
import json
from pathlib import Path

from launch_desk.models import Brief
from launch_desk.references import CATEGORY, SamplePackSource, rank_references

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("build_sample_pack", ROOT / "tools" / "build_sample_pack.py")
bsp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bsp)

KIND_FOR = {v: k for k, v in CATEGORY.items()}
XAI = {"grok", "grok bot", "spacex ai"}


def raw_item(id, brand, views, caption="A caption here"):
    return {"key": {"id": id}, "kind": "post", "url": f"https://posts.design/{id}", "caption": caption,
            "brand": {"name": brand}, "sourceMetrics": {"views": views}}


def entry(id, brand, views, category="announcement"):
    return {"id": id, "brand": brand, "url": f"https://posts.design/{id}", "caption": "A caption",
            "views": views, "category": category, "why": "x"}


def write_raw(folder, name, items):
    (folder / name).write_text(json.dumps({"ok": True, "result": json.dumps({"items": items, "also": []})}))


def test_generator_leaves_out_excluded_brands_and_ids_from_raw_and_base(tmp_path):
    dropped_id = sorted(bsp.EXCLUDE_IDS)[0]
    write_raw(tmp_path, "announcement-1.json", [
        raw_item("wh", "White House", 9_000_000), raw_item(dropped_id, "Grok", 8_000_000),
        raw_item("lin", "Linear", 50_000)])
    base = [entry("mint", "United States Mint", 2_000_000), entry("keep", "Resend", 10_000)]
    out = bsp.build(tmp_path, base)
    assert {e["id"] for e in out} == {"lin", "keep"}


def test_generator_pick_takes_only_the_named_ids(tmp_path):
    write_raw(tmp_path, "feature-update-1.json", [
        raw_item("a", "A", 900_000), raw_item("b", "B", 1_000), raw_item("c", "C", 500)])
    out = bsp.build(tmp_path, [entry("base", "Base", 10)], pick=["b", "c", "missing"])
    assert {e["id"] for e in out} == {"base", "b", "c"}
    assert {e["category"] for e in out if e["id"] in "bc"} == {"feature-update"}


def test_generated_why_is_what_ranking_shows_for_that_category(tmp_path):
    write_raw(tmp_path, "feature-update-1.json", [raw_item("lin", "Linear", 50_000)])
    [e] = bsp.build(tmp_path)
    [ref] = rank_references([e], Brief("feature", "x", "themes", "https://a.test"))
    assert e["why"] == ref.why


def test_shipped_pack_is_curated():
    entries = SamplePackSource().entries
    assert [e["id"] for e in entries if bsp.excluded(e)] == []
    assert sum(e["brand"].lower() in XAI for e in entries) == 1
    for e in entries:
        [ref] = rank_references([e], Brief(KIND_FOR[e["category"]], "x", "themes", "https://a.test"))
        assert e["why"] == ref.why
