import json

import pytest

from fakes import FakeHost
from launch_desk.draft import (
    DRAFT_SCHEMA,
    DraftError,
    build_brief_prompt,
    draft_post,
    parse_draft,
)
from launch_desk.hooks import pick_hook_shapes
from launch_desk.models import BrandKit, Brief, Reference
from launch_desk.rules import STOCK_PHRASES

BRIEF = Brief("feature", "x", "custom themes for pet profile pages", "https://pawr.link")
BRAND = BrandKit("pawr.link", "https://pawr.link", "A link page for your pet", "#fff", "#111",
                 "#f60", None, None, voice_samples=("We shipped a small thing today. Pet pages load faster now.",))
REFS = [
    Reference("r1", "Notion", "https://posts.design/r1",
              "Small QoL improvement: you can now share pages with your Custom Agents straight from the Share menu.",
              1000, "feature-update", "Close match"),
    Reference("r2", "Perplexity", "https://posts.design/r2",
              "You can now build custom reusable agents in the Perplexity Agent API with Profiles, Skills, and managed connectors.",
              2000, "feature-update", "Close match"),
    Reference("r3", "beehiiv", "https://posts.design/r3",
              "five new ways to turn an audience into a room full of people who know each other.",
              3000, "feature-update", "Same kind"),
]
SHAPES = pick_hook_shapes("feature")

CLEAN = {
    "hooks": ["Pet pages can wear your colours now.",
              "Check this out: Biscuit's page is green.",
              "Your pet's page, your theme."],
    "caption": "Pet pages can wear your colours now.\n\nPick a theme in settings and the whole page follows.\n\nhttps://pawr.link",
}
BAD = {
    "hooks": ["We are excited to announce themes", "https://pawr.link has themes", "Themes are here."],
    "caption": "Themes are here — finally — for pet pages.\n\nhttps://pawr.link",
}


def draft(replies, brand=BRAND):
    host = FakeHost(llm_replies=list(replies))
    return host, draft_post(host, BRIEF, brand, REFS, SHAPES)


def prompt_of(call):
    instructions, text, _schema = call
    return instructions + "\n" + text


def test_clean_reply_passes_without_redraft():
    host, d = draft([CLEAN])
    assert len(host.llm_calls) == 1
    assert host.llm_calls[0][2] == DRAFT_SCHEMA
    assert d.redrafted is False
    assert d.failures == ()
    assert d.caption == CLEAN["caption"]
    assert d.hooks == tuple(CLEAN["hooks"])


def test_failing_reply_redrafts_once_with_fixes():
    host, d = draft([BAD, CLEAN])
    assert len(host.llm_calls) == 2
    second = prompt_of(host.llm_calls[1])
    for rule_id in ("em-dash", "hook1:stock-phrase", "hook2:link-first-line"):
        assert rule_id in second
    assert BAD["caption"] in second  # the previous draft is shown
    assert d.redrafted is True
    assert d.failures == ()
    assert d.caption == CLEAN["caption"]


def test_still_failing_after_redraft_reports_failures():
    host, d = draft([BAD, BAD])
    assert len(host.llm_calls) == 2
    ids = {f.rule_id for f in d.failures}
    assert {"em-dash", "hook1:stock-phrase", "hook2:link-first-line"} <= ids
    assert all(f.message and f.fix for f in d.failures)


def test_better_first_draft_is_kept_over_worse_redraft():
    one_fail = {**CLEAN, "caption": "Themes — for pet pages — are here.\n\nhttps://pawr.link"}
    host, d = draft([one_fail, BAD])
    assert d.caption == one_fail["caption"]
    assert d.redrafted is False
    assert [f.rule_id for f in d.failures] == ["em-dash"]


def test_tie_keeps_the_redraft():
    a = {**CLEAN, "caption": "Themes — for pet pages — are here.\n\nhttps://pawr.link"}
    b = {**CLEAN, "caption": "Pet pages — with themes — are here.\n\nhttps://pawr.link"}
    _, d = draft([a, b])
    assert d.caption == b["caption"]
    assert d.redrafted is True


def test_junk_redraft_keeps_first_draft():
    one_fail = {**CLEAN, "caption": "Themes — for pet pages — are here.\n\nhttps://pawr.link"}
    host, d = draft([one_fail, "no json here"])
    assert d.caption == one_fail["caption"]
    assert d.redrafted is False


def test_voice_sample_relaxes_rule():
    brand = BrandKit("pawr.link", "https://pawr.link", "", "#fff", "#111", "#f60", None, None,
                     voice_samples=("We shipped — at last — the thing — you asked for.",))
    two_dashes = {**CLEAN, "caption": "Themes — for pet pages — are here.\n\nhttps://pawr.link"}
    host, d = draft([two_dashes], brand=brand)
    assert len(host.llm_calls) == 1
    assert d.failures == ()


def test_junk_then_good_retries_with_same_prompt():
    host, d = draft(["Sorry, here you go!", CLEAN])
    assert len(host.llm_calls) == 2
    assert host.llm_calls[0] == host.llm_calls[1]
    assert d.caption == CLEAN["caption"]


def test_junk_twice_raises_draft_error():
    host = FakeHost(llm_replies=["not json at all", None])
    with pytest.raises(DraftError) as info:
        draft_post(host, BRIEF, BRAND, REFS, SHAPES)
    assert len(host.llm_calls) == 2
    assert str(info.value)


@pytest.mark.parametrize("reply", [
    "```json\n" + json.dumps(CLEAN) + "\n```",
    "Here is the draft:\n```\n" + json.dumps(CLEAN) + "\n```\nHope it helps.",
    json.dumps(CLEAN),
    "Sure! " + json.dumps(CLEAN),
    CLEAN,
])
def test_parse_accepts_fenced_json(reply):
    assert parse_draft(reply) == (CLEAN["hooks"], CLEAN["caption"])


@pytest.mark.parametrize("reply", [
    None, "", "not json", "[1, 2, 3]", 42,
    {"hooks": ["a", "b"], "caption": "x"},
    {"hooks": ["a", "b", "c", "d"], "caption": "x"},
    {"hooks": ["a", "", "c"], "caption": "x"},
    {"hooks": ["a", 2, "c"], "caption": "x"},
    {"hooks": "a b c", "caption": "x"},
    {"hooks": ["a", "b", "c"]},
    {"hooks": ["a", "b", "c"], "caption": "   "},
    {"hooks": ["a", "b", "c"], "caption": ["x"]},
])
def test_parse_rejects_wrong_shapes(reply):
    assert parse_draft(reply) is None


def test_prompt_stays_under_6000_chars_with_long_captions():
    long_refs = [Reference(f"r{i}", "Brand" * 20, "https://posts.design/x", "word " * 2000, 10, None, "w")
                 for i in range(3)]
    brand = BrandKit("N" * 300, "https://pawr.link", "T" * 1000, "#fff", "#111", "#f60", None, None,
                     voice_samples=tuple("voice " * 1000 for _ in range(5)))
    brief = Brief("feature", "x", "what " * 500, "https://pawr.link/" + "p" * 300)
    instructions, text = build_brief_prompt(brief, brand, long_refs, SHAPES, set())
    assert len(instructions) + len(text) < 6000


def test_prompt_carries_the_facts_and_references():
    instructions, text = build_brief_prompt(BRIEF, BRAND, REFS, SHAPES, set())
    whole = instructions + text
    for value in (BRAND.name, BRAND.tagline, BRIEF.what, BRIEF.product_url, BRAND.voice_samples[0]):
        assert value in whole
    for ref in REFS:
        assert ref.caption in whole
    for phrase in STOCK_PHRASES:
        assert phrase in whole


def test_relaxed_rule_shortens_the_rules():
    strict = build_brief_prompt(BRIEF, BRAND, REFS, SHAPES, set())[0]
    relaxed = build_brief_prompt(BRIEF, BRAND, REFS, SHAPES, {"em-dash", "emoji", "hashtags"})[0]
    assert len(relaxed) < len(strict)
