import json
from pathlib import Path

from fakes import FakeHost
from launch_desk.models import Brief
from launch_desk.references import (
    McpSource,
    SamplePackSource,
    build_search_args,
    rank_references,
)

FIX = Path(__file__).parent / "fixtures"
FEATURE = Brief("feature", "x", "custom themes for pet profile pages", "https://pawr.link")


def _fix(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def item(id, brand, views, category="feature-update", caption="A caption here"):
    return {"id": id, "brand": brand, "url": f"https://posts.design/{id}", "caption": caption,
            "views": views, "category": category}


def live(id, brand, views, origin="main", pos=0, loose=False, caption="A caption here"):
    return {"key": {"id": id}, "url": f"https://posts.design/{id}", "caption": caption,
            "brand": {"name": brand}, "sourceMetrics": {"views": views},
            "_origin": origin, "_pos": pos, "_loose": loose}


def test_args_for_feature_use_feature_update_category():
    args = build_search_args(FEATURE)
    assert args["category"] == "feature-update"
    assert args["collection"] == "posts"
    assert args["limit"] == 10
    assert args["query"] == FEATURE.what
    assert len(args["alsoQueries"]) == 2


def test_also_queries_include_keyword_phrase_from_what():
    also = build_search_args(FEATURE)["alsoQueries"]
    assert also[0] == "custom themes pet profile"
    assert also[1] and also[1] != also[0]
    long = build_search_args(Brief("product", "x", "The very best app for you and your dogs and cats", "u"))
    assert len(long["alsoQueries"][0].split()) <= 4
    assert "the" not in long["alsoQueries"][0].split()


def test_args_category_per_kind_and_query_cut():
    cats = {k: build_search_args(Brief(k, "x", "y" * 400, "u"))["category"]
            for k in ("feature", "product", "rebrand", "partnership")}
    assert cats == {"feature": "feature-update", "product": "announcement",
                    "rebrand": "rebrand", "partnership": "partner"}
    assert len(build_search_args(Brief("feature", "x", "y" * 400, "u"))["query"]) == 160


def test_rank_prefers_tier_then_views_one_per_brand():
    items = [
        live("a1", "Acme", 1_000_000, origin="also"),
        live("b1", "Beta", 5_000, loose=True),
        live("b2", "Beta", 90_000),
        live("c1", "Cora", 200_000, loose=True, pos=1),
        live("d1", "Dune", 300, origin="also", pos=2),
    ]
    out = rank_references(items, FEATURE)
    assert [r.id for r in out] == ["b2", "c1", "a1"]
    assert len({r.brand for r in out}) == 3
    assert all(r.why for r in out)
    assert all(r.category is None for r in out)


def test_pack_items_still_rank_by_category_match():
    items = [item("p1", "P", 9_000_000, category="announcement"), item("p2", "Q", 1_000)]
    assert rank_references(items, FEATURE, k=2)[0].id == "p2"


def test_items_without_caption_are_skipped():
    items = [live("n", "No Cap", 9_000_000, caption=None), live("k", "Known", 10)]
    assert [r.id for r in rank_references(items, FEATURE, k=3)] == ["k"]


def test_main_query_hits_outrank_also_query_hits():
    raw = _fix("search_references.json")
    items = McpSource(FakeHost(mcp_replies={"search_references": raw})).search(build_search_args(FEATURE))
    origin = {i["key"]["id"]: i["_origin"] for i in items}
    ranked = rank_references(items, FEATURE, k=50)
    mains = [r for r in ranked if origin[r.id] == "main"]
    assert mains
    first_also = next(n for n, r in enumerate(ranked) if origin[r.id] == "also")
    assert all(ranked.index(r) < first_also for r in mains)


def test_missing_views_rank_below_known_views():
    items = [live("n", "None Co", None), live("k", "Known", 10)]
    assert [r.id for r in rank_references(items, FEATURE, k=2)] == ["k", "n"]


def test_rank_exclude_skips_shown_ids():
    items = [item("a", "A", 900), item("b", "B", 800), item("c", "C", 700), item("d", "D", 600)]
    out = rank_references(items, FEATURE, exclude={"a"})
    assert [r.id for r in out] == ["b", "c", "d"]


def test_rank_reads_real_search_shape():
    raw = _fix("search_references.json")
    items = McpSource(FakeHost(mcp_replies={"search_references": raw})).search(build_search_args(FEATURE))
    assert len(items) > 4  # main items plus alsoQueries items
    out = rank_references(items, FEATURE)
    assert len(out) == 3
    assert all(r.url.startswith("https://posts.design/") and r.views for r in out)
    assert len({r.id for r in out}) == 3








def test_pack_source_search_filters_by_category():
    pack = SamplePackSource()
    out = pack.search({"category": "announcement"})
    assert out and all(i["category"] == "announcement" for i in out)


def test_sample_pack_entries_have_required_fields():
    entries = json.loads((Path(__file__).parent.parent / "launch_desk" / "sample_pack.json")
                         .read_text(encoding="utf-8"))
    assert 25 <= len(entries) <= 40
    assert len({e["id"] for e in entries}) == len(entries)
    for e in entries:
        assert e["id"] and e["brand"] and e["caption"] and e["category"] and e["why"]
        assert e["url"].startswith("https://posts.design/")
        assert isinstance(e["views"], int) and e["views"] > 0
    assert {e["category"] for e in entries} == {"announcement", "feature-update"}
    brands = {}
    for e in entries:
        brands[e["brand"]] = brands.get(e["brand"], 0) + 1
    assert max(brands.values()) <= 2
