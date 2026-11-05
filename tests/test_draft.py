import json

import pytest

from fakes import FakeHost
from launch_desk.draft import (
    DRAFT_SCHEMA,
    DraftError,
    build_brief_prompt,
    build_redraft_prompt,
    draft_post,
    facts_text,
    parse_draft,
    unsupported_details,
)
from launch_desk.hooks import pick_hook_shapes
from launch_desk.models import BrandKit, Brief, Failure, Reference
from launch_desk.rules import RULES, STOCK_PHRASES

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

USE = "Pick a theme and the whole page follows."
CLEAN = {
    "use_case": USE,
    "hooks": ["Pet pages can wear your colours now.",
              "Check this out: Biscuit's page is green.",
              "Your pet's page, your theme."],
    "caption": "Pet pages can wear your colours now.\n\nPick a theme in settings and the whole page follows.\n\nhttps://pawr.link",
}
BAD = {
    "use_case": USE,
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
    one_fail = {**CLEAN, "caption": "Themes — for pet pages — are here.\n\nPick a theme and the whole page follows.\n\nhttps://pawr.link"}
    host, d = draft([one_fail, BAD])
    assert d.caption == one_fail["caption"]
    assert d.redrafted is False
    assert [f.rule_id for f in d.failures] == ["em-dash"]


def test_tie_keeps_the_redraft():
    a = {**CLEAN, "caption": "Themes — for pet pages — are here.\n\nPick a theme and the whole page follows.\n\nhttps://pawr.link"}
    b = {**CLEAN, "caption": "Pet pages — with themes — are here.\n\nPick a theme and the whole page follows.\n\nhttps://pawr.link"}
    _, d = draft([a, b])
    assert d.caption == b["caption"]
    assert d.redrafted is True


def test_junk_redraft_keeps_first_draft():
    one_fail = {**CLEAN, "caption": "Themes — for pet pages — are here.\n\nPick a theme and the whole page follows.\n\nhttps://pawr.link"}
    host, d = draft([one_fail, "no json here"])
    assert d.caption == one_fail["caption"]
    assert d.redrafted is False


def test_voice_sample_relaxes_rule():
    brand = BrandKit("pawr.link", "https://pawr.link", "", "#fff", "#111", "#f60", None, None,
                     voice_samples=("We shipped — at last — the thing — you asked for.",))
    two_dashes = {**CLEAN, "caption": "Themes — for pet pages — are here.\n\nPick a theme and the whole page follows.\n\nhttps://pawr.link"}
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
    assert parse_draft(reply) == (CLEAN["hooks"], CLEAN["caption"], USE)


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
    {"hooks": ["a", "b", "c"], "caption": "x"},
    {"hooks": ["a", "b", "c"], "caption": "x", "use_case": "  "},
    {"hooks": ["a", "b", "c"], "caption": "x", "use_case": 3},
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


FACTS = facts_text(BRIEF, BRAND)


def test_took_a_minute_without_fact_is_flagged():
    fails = unsupported_details("I set mine to match his collar. Took a minute.", FACTS)
    assert [f.rule_id for f in fails] == ["unsupported-detail"]
    assert "minute" in fails[0].message and "minute" in fails[0].fix


@pytest.mark.parametrize("text", ["Watch me redo my page with one tap.", "It works instantly.",
                                  "Three new themes.", "Now 40 themes.", "A dozen colours."])
def test_invented_counts_and_effort_are_flagged(text):
    assert [f.rule_id for f in unsupported_details(text, FACTS)] == ["unsupported-detail"]


def test_number_in_facts_is_allowed():
    facts = "posts.design 1,200 launch posts you can filter in seconds"
    assert unsupported_details("Now 1,200 posts, filtered in seconds.", facts) == []
    assert unsupported_details("Now 1,300 posts.", facts) != []


@pytest.mark.parametrize("text", ["Small one: themes are here.", "Pick one.", "Live today at https://pawr.link/p/2024",
                                  "Your pet's page, your colours."])
def test_plain_text_is_not_flagged(text):
    assert unsupported_details(text, FACTS) == []


def test_unsupported_detail_triggers_redraft():
    invented = {**CLEAN, "caption": "Pet pages can wear your colours now.\n\nPick a theme and the whole page follows. Took a minute.\n\nhttps://pawr.link",
                "hooks": [CLEAN["hooks"][0], "Check this out: one tap and Biscuit's page is green.",
                          CLEAN["hooks"][2]]}
    host, d = draft([invented, CLEAN])
    assert len(host.llm_calls) == 2
    second = prompt_of(host.llm_calls[1])
    assert "unsupported-detail" in second and "hook2:unsupported-detail" in second
    assert d.failures == () and d.redrafted is True


def test_unsupported_detail_reported_when_redraft_keeps_it():
    invented = {**CLEAN, "caption": "Pet pages can wear your colours now.\n\nPick a theme and the whole page follows. Took a minute.\n\nhttps://pawr.link"}
    _, d = draft([invented, invented])
    assert [f.rule_id for f in d.failures] == ["unsupported-detail"]


def test_redraft_lists_each_fix_once():
    fix = next(r.fix for r in RULES if r.id == "em-dash")
    fails = [Failure("em-dash", "m", fix), Failure("hook2:em-dash", "Hook 2: m", fix),
             Failure("hook3:em-dash", "Hook 3: m", fix)]
    _, text = build_redraft_prompt(BRIEF, BRAND, REFS, SHAPES, set(), ["a", "b", "c"], "cap", fails)
    assert text.count(fix) == 1
    for rule_id in ("em-dash", "hook2:em-dash", "hook3:em-dash"):
        assert rule_id in text


def test_redraft_prompt_stays_under_6000_chars():
    long_refs = [Reference(f"r{i}", "Brand" * 20, "https://posts.design/x", "word " * 2000, 10, None, "w")
                 for i in range(3)]
    brand = BrandKit("N" * 300, "https://pawr.link", "T" * 1000, "#fff", "#111", "#f60", None, None,
                     voice_samples=tuple("voice " * 1000 for _ in range(5)))
    brief = Brief("feature", "x", "what " * 500, "https://pawr.link/" + "p" * 300)
    hook_rules = ("first-line-short", "stock-phrase", "em-dash", "emoji", "placeholder",
                  "link-first-line", "copied-run")
    fails = [Failure(r.id, r.message, r.fix) for r in RULES]
    fails += [Failure(f"hook{n}:{r.id}", r.message, r.fix) for n in (1, 2, 3) for r in RULES if r.id in hook_rules]
    for n in range(4):  # different long phrases in each place, so the fix lines cannot be merged
        detail = unsupported_details(" ".join(str(n) + "9" * (40 + i) for i in range(8)), "")[0]
        fails.append(detail if n == 0 else Failure(f"hook{n}:{detail.rule_id}", detail.message, detail.fix))
    assert len(fails) >= 30
    hooks = ["hook " * 500] * 3
    instructions, text = build_redraft_prompt(brief, brand, long_refs, SHAPES, set(), hooks, "cap " * 3000, fails)
    assert len(instructions) + len(text) < 6000


class RaisingHost(FakeHost):
    def __init__(self, exc):
        super().__init__()
        self.exc = exc

    def complete_json(self, instructions, text, schema):
        self.llm_calls.append((instructions, text, schema))
        raise self.exc


def test_call_errors_become_draft_error_with_cause():
    exc = RuntimeError("provider down")
    host = RaisingHost(exc)
    with pytest.raises(DraftError) as info:
        draft_post(host, BRIEF, BRAND, REFS, SHAPES)
    assert len(host.llm_calls) == 2
    assert info.value.__cause__ is exc


def test_programming_errors_propagate():
    with pytest.raises(AttributeError):
        draft_post(RaisingHost(AttributeError("bug")), BRIEF, BRAND, REFS, SHAPES)


def test_founder_detail_is_a_fact_in_prompt_and_check():
    brief = Brief("feature", "x", "custom themes for pet profile pages", "https://pawr.link",
                  detail="12 people asked to match their own site")
    assert "12 people asked" in facts_text(brief, BRAND)
    _instructions, text = build_brief_prompt(brief, BRAND, REFS, SHAPES, set())
    assert "12 people asked to match their own site" in text.split("REAL LAUNCH POSTS")[0]
    assert unsupported_details("12 people asked for this.", facts_text(brief, BRAND)) == []
    assert Brief("feature", "x", "w", "u").detail == ""


def test_prompt_asks_each_line_to_say_something_new():
    instructions, _text = build_brief_prompt(BRIEF, BRAND, REFS, SHAPES, set())
    assert "Don't restate the first line." in instructions


def test_restating_caption_triggers_redraft_naming_the_rule():
    bad = {**CLEAN, "caption": "Check this out: your pet's page can now match your site.\n"
                              "Now your pet's page can match your own site.\n\n" + USE + "\n\nhttps://pawr.link"}
    host, d = draft([bad, CLEAN])
    assert len(host.llm_calls) == 2
    assert "repeats-first-line" in prompt_of(host.llm_calls[1])
    assert d.failures == () and d.redrafted is True


def test_repeats_first_line_is_not_a_hook_rule():
    from launch_desk.draft import HOOK_RULES
    assert "repeats-first-line" not in HOOK_RULES


# ------------------------------------------------------------------ use case and shapes

def test_schema_requires_a_use_case():
    assert DRAFT_SCHEMA["properties"]["use_case"]["type"] == "string"
    assert {"use_case", "hooks", "caption"} <= set(DRAFT_SCHEMA["required"])


def test_draft_returns_the_use_case():
    _, d = draft([CLEAN])
    assert d.use_case == USE


def test_reply_without_use_case_is_retried_then_fails():
    no_use = {k: v for k, v in CLEAN.items() if k != "use_case"}
    host = FakeHost(llm_replies=[no_use, no_use])
    with pytest.raises(DraftError):
        draft_post(host, BRIEF, BRAND, REFS, SHAPES)
    assert len(host.llm_calls) == 2


def test_caption_body_must_carry_the_use_case():
    elsewhere = {**CLEAN, "use_case": "Match your pet's page to your own site."}
    host, d = draft([elsewhere, CLEAN])
    assert len(host.llm_calls) == 2
    second = prompt_of(host.llm_calls[1])
    assert "use-case-missing" in second and elsewhere["use_case"] in second
    assert d.failures == () and d.redrafted is True


def test_use_case_in_the_first_line_counts():
    first_line = {**CLEAN, "use_case": "Pet pages can wear your colours now."}
    host, d = draft([first_line])
    assert len(host.llm_calls) == 1 and d.failures == ()


def test_unsupported_detail_in_use_case_is_flagged():
    invented = {**CLEAN, "use_case": "Pick a theme in one tap and the whole page follows."}
    _, d = draft([invented, invented])
    ids = {f.rule_id for f in d.failures}
    assert "use-case:unsupported-detail" in ids


def test_use_case_from_the_founder_detail_passes():
    brief = Brief("feature", "x", "custom themes for pet profile pages", "https://pawr.link",
                  detail="people kept asking to match their pet's page to their own site")
    reply = {"use_case": "Match your pet's page to your own site.",
             "hooks": ["Pet pages take custom themes now.", "Check this out: pet pages take themes.",
                       "People kept asking for this one."],
             "caption": "Pet pages take custom themes now.\n\nPeople kept asking to match their pet's page "
                        "to their own site. Now you can.\n\nhttps://pawr.link"}
    host = FakeHost(llm_replies=[reply])
    d = draft_post(host, brief, BRAND, REFS, SHAPES)
    assert d.failures == () and len(host.llm_calls) == 1


def test_prompt_names_each_reference_shape_and_borrows_the_top_one():
    from launch_desk.references import shape_note
    instructions, text = build_brief_prompt(BRIEF, BRAND, REFS, SHAPES, set())
    for r in REFS:
        assert shape_note(r.caption) in text
    assert shape_note(REFS[0].caption) in instructions
    assert REFS[0].brand in instructions
    no_refs = build_brief_prompt(BRIEF, BRAND, [], SHAPES, set())[0]
    assert shape_note(REFS[0].caption) not in no_refs


def test_prompt_with_detail_and_long_everything_stays_under_6000_chars():
    long_refs = [Reference(f"r{i}", "Brand" * 20, "https://posts.design/x", "word " * 2000, 10, None, "w")
                 for i in range(3)]
    brand = BrandKit("N" * 300, "https://pawr.link", "T" * 1000, "#fff", "#111", "#f60", None, None,
                     voice_samples=tuple("voice " * 1000 for _ in range(5)))
    brief = Brief("feature", "x", "what " * 500, "https://pawr.link/" + "p" * 300, detail="detail " * 500)
    instructions, text = build_brief_prompt(brief, brand, long_refs, SHAPES, set())
    assert len(instructions) + len(text) < 6000
    hooks = ["hook " * 500] * 3
    fails = [Failure(r.id, r.message, r.fix) for r in RULES]
    r_ins, r_text = build_redraft_prompt(brief, brand, long_refs, SHAPES, set(), hooks, "cap " * 3000, fails,
                                         use_case="use " * 500)
    assert len(r_ins) + len(r_text) < 6000


def test_redraft_prompt_shows_the_last_use_case():
    _, text = build_redraft_prompt(BRIEF, BRAND, REFS, SHAPES, set(), CLEAN["hooks"], CLEAN["caption"],
                                   [Failure("em-dash", "m", "f")], use_case=USE)
    assert USE in text
