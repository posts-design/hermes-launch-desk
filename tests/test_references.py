import json
import re
from pathlib import Path

import pytest

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


def test_pack_ranks_keyword_overlap_before_category_and_views():
    items = [
        item("off", "Off", 9_000_000, caption="timeline. ticker. trade."),
        item("cat", "Cat", 5_000_000, category="announcement", caption="Custom themes for every page"),
        item("one", "One", 1_000, caption="Introducing custom pairs"),
        item("two", "Two", 500, caption="New profile themes are here"),
    ]
    assert [r.id for r in rank_references(items, FEATURE, k=4)] == ["cat", "two", "one", "off"]
    same = [item("a", "A", 900, caption="Dark mode"), item("b", "B", 100, caption="Pet profiles")]
    out = rank_references(same, FEATURE, k=2)
    assert [r.id for r in out] == ["b", "a"]
    assert out[0].why != out[1].why and "100" in out[0].why


ON_TOPIC = re.compile(r"\b(themes?|custom\w*|profiles?)\b", re.I)


def test_sample_pack_picks_on_topic_posts_for_a_themes_feature():
    pack = SamplePackSource()
    items = pack.search(build_search_args(FEATURE))
    picked = rank_references(items, FEATURE)
    assert len(picked) == 3
    assert all(ON_TOPIC.search(r.caption) for r in picked)
    on_topic = [e for e in items if ON_TOPIC.search(e["caption"])]
    assert len(on_topic) >= 3
    top_views = max(e["views"] for e in items)
    assert all(r.views < top_views for r in picked)  # the biggest off-topic post is not picked


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


# ------------------------------------------------------------------ shape notes

from launch_desk.references import caption_shape, shape_note  # noqa: E402


def _captions():
    """Every real caption we keep: the recorded search reply and the sample pack, by brand and first words."""
    raw = _fix("search_references.json")
    groups = [raw["items"]] + [a.get("items") or [] for a in raw.get("also") or []]
    found = {}
    for it in [i for g in groups for i in g] + SamplePackSource().entries:
        cap = it.get("caption")
        if cap:
            brand = it["brand"]["name"] if isinstance(it.get("brand"), dict) else (it.get("brand") or "")
            found[(brand, " ".join(cap.split()[:3]))] = cap
    return found


CAPS = _captions()


def cap(brand, start):
    return next(c for (b, s), c in CAPS.items() if b == brand and s.startswith(start))


@pytest.mark.parametrize("brand,start,opening", [
    ("Perplexity", "You can now", "can-now"),
    ("Notion", "Small QoL", "can-now"),
    ("Cursor", "Cursor can now", "can-now"),
    ("beehiiv 🐝", "five new ways", "number"),
    ("Resend", "Need to catch", "question"),
    ("Airbnb", "Hey everyone,", "audience"),
    ("Granola", "Introducing People", "announce"),
    ("Linear", "New: MCP", "announce"),
    ("Relume", "Today we’re launching", "announce"),
    ("X", "timeline. ticker.", "short-claim"),
    ("fomo", "Your conviction,", "short-claim"),
    ("MetaMask", "swap fees on", "statement"),
])
def test_opening_move_of_real_captions(brand, start, opening):
    assert caption_shape(cap(brand, start))["opening"] == opening


def test_lists_links_and_line_counts_of_real_captions():
    raycast = caption_shape(cap("Raycast", "New update is"))
    assert raycast["list"] and raycast["link"]
    vercel = caption_shape(cap("Vercel", "fx v0.0.16"))
    assert vercel["list"] and vercel["link"]
    assert caption_shape(cap("Linear", "New └ Nested"))["list"]
    perplexity = caption_shape(cap("Perplexity", "You can now"))
    assert (perplexity["lines"], perplexity["list"], perplexity["link"]) == (2, False, False)
    assert caption_shape(cap("Grok", "An early beta"))["link"]  # ends on "Try it at"
    assert caption_shape(cap("X", "timeline. ticker."))["lines"] == 1


def test_length_bucket_grows_with_the_caption():
    sizes = ["short", "medium", "long"]
    short = caption_shape(cap("X", "timeline. ticker."))["size"]
    medium = caption_shape(cap("Perplexity", "You can now"))["size"]
    long = caption_shape(cap("beehiiv 🐝", "five new ways"))["size"]
    assert sizes.index(short) < sizes.index(medium) < sizes.index(long)


def test_every_real_caption_gets_a_note_and_shapes_differ():
    notes = {c: shape_note(c) for c in CAPS.values()}
    assert all(n and "\n" not in n and len(n) <= 80 for n in notes.values())
    assert len(set(notes.values())) >= 8
    assert shape_note(cap("Perplexity", "You can now")) != shape_note(cap("beehiiv 🐝", "five new ways"))


def test_empty_caption_has_a_shape():
    assert caption_shape("")["lines"] == 0 and shape_note("")


def test_reference_line_carries_brand_shape_views_and_fit():
    from launch_desk.models import Reference
    from launch_desk.references import reference_line
    r = Reference("a", "Perplexity", "https://posts.design/a", cap("Perplexity", "You can now"),
                  42749, None, "Close match for your launch, 42.7K views")
    line = reference_line(r)
    assert line.startswith("Perplexity")
    assert shape_note(r.caption) in line and "42.7K" in line and "Close match for your launch" in line
    assert line.count("42.7K") == 1
    none = reference_line(Reference("b", "", "u", "Hi.", None, None, "A related launch"))
    assert shape_note("Hi.") in none and "A related launch" in none and "views" not in none


def test_reference_line_skips_the_shape_of_an_empty_caption():
    from launch_desk.models import Reference
    from launch_desk.references import reference_line
    line = reference_line(Reference("a", "Raycast", "u", "", 72620, None, "Same kind of launch, 72.6K views"))
    assert shape_note("") not in line and "72.6K" in line and "Same kind of launch" in line


def test_reference_line_caps_the_brand_at_24_characters():
    from launch_desk.models import Reference
    from launch_desk.references import reference_line
    line = reference_line(Reference("a", "B" * 60, "u", "Hi.", None, None, "A related launch"))
    assert "B" * 20 in line and "B" * 25 not in line


@pytest.mark.parametrize("caption", ["新しいテーマは？\n今すぐ", "¿Ya probaste los temas nuevos\nYa están."])
def test_full_width_and_spanish_question_marks_count(caption):
    assert caption_shape(caption)["opening"] == "question"

