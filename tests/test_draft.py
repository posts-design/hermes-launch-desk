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
              "Biscuit's page is green now.",
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
    assert parse_draft(reply) == (CLEAN["hooks"], CLEAN["caption"], USE, "")


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
    from launch_desk.draft import _SAME_AS_LISTED
    for phrase in STOCK_PHRASES:
        assert phrase in whole or phrase in _SAME_AS_LISTED


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


@pytest.mark.parametrize("text", ["Small one: themes are here.", "Pick one.", "Live at https://pawr.link/p/2024",
                                  "Your pet's page, your colours."])
def test_plain_text_is_not_flagged(text):
    assert unsupported_details(text, FACTS) == []


def test_unsupported_detail_triggers_redraft():
    invented = {**CLEAN, "caption": "Pet pages can wear your colours now.\n\nPick a theme and the whole page follows. Took a minute.\n\nhttps://pawr.link",
                "hooks": [CLEAN["hooks"][0], "One tap and Biscuit's page is green.",
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


@pytest.mark.parametrize("kind", ["feature", "product", "rebrand", "partnership"])
def test_redraft_prompt_stays_under_6000_chars_for_every_kind_with_a_use_case(kind):
    brand = BrandKit("N" * 300, "https://pawr.link", "T" * 1000, "#fff", "#111", "#f60", None, None)
    brief = Brief(kind, "x", "what " * 500, "https://pawr.link/" + "p" * 300, detail="detail " * 500)
    fails = [Failure(r.id, r.message, r.fix) for r in RULES]
    fails += [Failure(f"hook{n}:{r.id}", r.message, r.fix) for n in (1, 2, 3) for r in RULES]
    for n in range(4):
        detail = unsupported_details(" ".join(str(n) + "9" * (40 + i) for i in range(8)), "")[0]
        fails.append(detail if n == 0 else Failure(f"hook{n}:{detail.rule_id}", detail.message, detail.fix))
    fails.append(Failure("use-case-missing", "m", "Say the use case in the caption."))
    instructions, text = build_redraft_prompt(brief, brand, REFS, pick_hook_shapes(kind, "asked"), set(), ["hook " * 500] * 3,
                                              "cap " * 3000, fails, use_case="use " * 500)
    assert len(instructions) + len(text) < 6000
    assert "Caption:" in text and "Use case:" in text


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
    bad = {**CLEAN, "caption": "Your pet's page can now match your site.\n"
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
    elsewhere = {**CLEAN, "use_case": "Match the theme to your own website."}  # 1 of 4 words in the caption
    host, d = draft([elsewhere, CLEAN])
    assert len(host.llm_calls) == 2
    second = prompt_of(host.llm_calls[1])
    assert "use-case-missing" in second and elsewhere["use_case"] in second
    assert d.failures == () and d.redrafted is True


def test_use_case_said_only_in_the_first_line_triggers_a_redraft():
    # every hook replaces line 1, so the lines after it must say the use case
    first_line = {**CLEAN, "use_case": "Pet pages can wear your colours now."}
    host, d = draft([first_line, CLEAN])
    assert len(host.llm_calls) == 2 and "use-case-missing" in prompt_of(host.llm_calls[1])
    assert d.redrafted and d.failures == ()


def test_unsupported_detail_in_use_case_is_flagged():
    invented = {**CLEAN, "use_case": "Pick a theme in one tap and the whole page follows."}
    _, d = draft([invented, invented])
    ids = {f.rule_id for f in d.failures}
    assert "use-case:unsupported-detail" in ids


def test_use_case_from_the_founder_detail_passes():
    brief = Brief("feature", "x", "custom themes for pet profile pages", "https://pawr.link",
                  detail="people kept asking to match their pet's page to their own site")
    reply = {"use_case": "Match your pet's page to your own site.",
             "hooks": ["Pet pages take custom themes now.", "Pet pages take themes, finally.",
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


@pytest.mark.parametrize("kind", ["feature", "product", "rebrand", "partnership"])
def test_prompt_with_the_longest_shape_notes_stays_under_6000_chars(kind):
    from launch_desk.references import shape_note
    cap = "You can now " + "word " * 300 + "\n" + "\n".join("- item " * 20 for _ in range(6)) + "\nSee it here:"
    assert len(shape_note(cap)) >= 70
    refs = [Reference(f"r{i}", "Brand" * 20, "https://posts.design/x", cap, 10, None, "w") for i in range(3)]
    brand = BrandKit("N" * 300, "https://pawr.link", "T" * 1000, "#fff", "#111", "#f60", None, None,
                     voice_samples=tuple("voice " * 1000 for _ in range(5)))
    brief = Brief(kind, "x", "what " * 500, "https://pawr.link/" + "p" * 300, detail="detail " * 500)
    instructions, text = build_brief_prompt(brief, brand, refs, pick_hook_shapes(kind, "people asked"), set())
    assert len(instructions) + len(text) < 6000


def test_use_case_is_carried_at_forty_percent_of_its_words():
    from launch_desk.draft import use_case_carried
    use = "Match your pet's page to your own site."  # match, pet, page, own, site
    assert use_case_carried(use, "Pet pages take custom themes.\n\nPick one for the page.")  # 2 of 5
    assert not use_case_carried(use, "Custom themes are live.\n\nPick one for the page.")  # 1 of 5
    assert use_case_carried("", "anything")


@pytest.mark.parametrize("detail,shape_id", [("", "audience-call-out"),
                                             ("people kept asking to match their own site", "you-asked")])
def test_prompt_carries_the_picked_hook_shapes(detail, shape_id):
    from launch_desk.hooks import HOOK_SHAPES
    brief = Brief("feature", "x", "custom themes for pet profile pages", "https://pawr.link", detail=detail)
    _, text = build_brief_prompt(brief, BRAND, REFS, pick_hook_shapes("feature", detail), set())
    assert HOOK_SHAPES["bold-contrast"].shape in text and HOOK_SHAPES[shape_id].shape in text


def test_redraft_prompt_shows_the_last_use_case():
    _, text = build_redraft_prompt(BRIEF, BRAND, REFS, SHAPES, set(), CLEAN["hooks"], CLEAN["caption"],
                                   [Failure("em-dash", "m", "f")], use_case=USE)
    assert USE in text


# ------------------------------------------------------------------ hook 1 is the founder's words

from launch_desk.draft import checklist, founder_hook, hook_options, post_failures, site_details  # noqa: E402
from launch_desk.models import Draft  # noqa: E402

LEAVES = "most of our support emails were blurry photos of sad leaves"
LEAF_BRIEF = Brief("feature", "x", "photo diagnosis", "https://fernlight.app", detail=LEAVES)
LEAF_BRAND = BrandKit("Fernlight", "https://fernlight.app", "Plant care for iPhone", "#fff", "#111", "#17a34a",
                      None, None)
LEAF_HOOKS = ("Snap the leaf and get the likely cause.", "Plant care that looks at the leaf.",
              "Fernlight reads the leaf for you.")
LEAF_CAPTION = ("Snap the leaf and get the likely cause.\n\nMost of our support emails were blurry photos of "
                "sad leaves.\n\nNow the photo itself gets you an answer.\n\nhttps://fernlight.app")


def leaf_draft(hooks=LEAF_HOOKS, caption=LEAF_CAPTION):
    return Draft(tuple(hooks), caption, (), False, "Snap the leaf and get the likely cause.")


def ids_of(text, brief=LEAF_BRIEF, brand=LEAF_BRAND, refs=REFS):
    return {f.rule_id for f in post_failures(text, brief, brand, refs)}


@pytest.mark.parametrize("detail,hook", [
    (LEAVES, "Most of our support emails were blurry photos of sad leaves."),
    ("  people kept asking for it!  ", "People kept asking for it!"),
    ("is it finally here?", "Is it finally here?"),
    ('we said "finally."', 'We said "finally."'),
    ("We built it in a weekend", "We built it in a weekend."),
])
def test_founder_hook_is_the_detail_verbatim(detail, hook):
    assert founder_hook(detail) == hook


@pytest.mark.parametrize("detail", ["", "   "])
def test_no_detail_no_founder_hook(detail):
    assert founder_hook(detail) == ""


def test_founder_words_are_option_one_and_model_hooks_fill_two_and_three():
    opts = hook_options(leaf_draft(), LEAF_BRIEF, LEAF_BRAND, REFS)
    assert [o.source for o in opts] == ["founder", "model", "model"]
    assert opts[0].text == founder_hook(LEAVES)
    assert [o.text for o in opts[1:]] == list(LEAF_HOOKS[:2])
    for o in opts:
        assert o.post.splitlines()[0] == o.text
        assert ids_of(o.post) == set()


def test_without_a_detail_the_three_model_hooks_stay():
    brief = Brief("feature", "x", "photo diagnosis", "https://fernlight.app")
    caption = LEAF_CAPTION.replace("Most of our support emails were blurry photos of sad leaves.\n\n", "")
    opts = hook_options(leaf_draft(caption=caption), brief, LEAF_BRAND, REFS)
    assert [o.source for o in opts] == ["model"] * 3 and [o.text for o in opts] == list(LEAF_HOOKS)


@pytest.mark.parametrize("bad", ["Check this out: the leaf tells you why.",
                                 "Your plant care should look at the leaf.",
                                 "The likely cause, from a snap of the leaf, is what you get now.",  # repeats a body line
                                 "Snap a leaf and get a fix in 3 seconds."])
def test_a_model_hook_that_fails_with_the_body_is_not_offered(bad):
    brief = Brief("feature", "x", "photo diagnosis", "https://fernlight.app")
    caption = ("Fernlight reads the leaf for you.\n\nSnap the leaf and get the likely cause.\n\n"
               "https://fernlight.app")
    hooks = (bad, "Plant care that looks at the leaf.", "Fernlight reads the leaf for you.")
    opts = hook_options(leaf_draft(hooks, caption), brief, LEAF_BRAND, REFS)
    assert bad not in [o.text for o in opts]
    assert [o.text for o in opts] == list(hooks[1:])
    assert all(ids_of(o.post, brief) == set() for o in opts)


def test_body_failures_do_not_filter_every_hook():
    brief = Brief("feature", "x", "photo diagnosis", "http://127.0.0.1:8731/k3/")
    caption = "Fernlight reads the leaf for you.\n\nNow the photo itself gets you an answer.\n\nhttp://127.0.0.1:8731/k3/"
    opts = hook_options(leaf_draft(caption=caption), brief, LEAF_BRAND, REFS)
    assert len(opts) == 3
    assert all("private-link" in ids_of(o.post, brief) for o in opts)


def test_founder_hook_with_a_repeating_body_line_drops_that_line_and_passes():
    plain = Brief("feature", "x", "photo diagnosis", "https://fernlight.app", detail=LEAVES)
    caption_hook_post = LEAF_CAPTION.replace(LEAF_CAPTION.splitlines()[0], founder_hook(LEAVES))
    assert "repeats-first-line" in ids_of(caption_hook_post, plain)  # what the founder would have got
    [founder, *models] = hook_options(leaf_draft(), plain, LEAF_BRAND, REFS)
    assert ids_of(founder.post, plain) == set()
    assert "sad leaves" not in "\n".join(founder.post.splitlines()[1:])
    assert founder.dropped and "sad leaves" in founder.dropped[0]
    assert "Now the photo itself gets you an answer." in founder.post and "https://fernlight.app" in founder.post
    assert all("sad leaves" in m.post and not m.dropped for m in models)


def test_a_dropped_line_keeps_its_link():
    caption = ("Snap the leaf and get the likely cause.\n\nMost of our support emails were blurry photos of "
               "sad leaves: https://fernlight.app")
    [founder, *_] = hook_options(leaf_draft(caption=caption), LEAF_BRIEF, LEAF_BRAND, REFS)
    assert founder.post.splitlines()[-1] == "https://fernlight.app"
    assert ids_of(founder.post) == set()


def test_founder_hook_is_offered_even_when_it_fails_a_rule():
    long = "we answered every single one of those blurry leaf photos by hand for two whole years"
    brief = Brief("feature", "x", "photo diagnosis", "https://fernlight.app", detail=long)
    opts = hook_options(leaf_draft(), brief, LEAF_BRAND, REFS)
    assert opts[0].source == "founder" and opts[0].text == founder_hook(long)
    assert "first-line-short" in ids_of(opts[0].post, brief)


def test_a_model_hook_equal_to_the_founder_words_is_not_repeated():
    hooks = ("Most of our support emails were blurry photos of sad leaves", *LEAF_HOOKS[:2])
    opts = hook_options(leaf_draft(hooks=hooks), LEAF_BRIEF, LEAF_BRAND, REFS)
    assert [o.source for o in opts] == ["founder", "model", "model"]
    assert [o.text for o in opts[1:]] == list(LEAF_HOOKS[:2])


def test_when_no_model_hook_passes_and_no_detail_the_least_bad_is_offered():
    brief = Brief("feature", "x", "photo diagnosis", "https://fernlight.app")
    hooks = ("Your plant should check this out.", "Check this out: the leaf.", "Your leaf should say hello to you.")
    opts = hook_options(leaf_draft(hooks, "Snap a leaf.\n\nhttps://fernlight.app"), brief, LEAF_BRAND, REFS)
    assert [o.text for o in opts] == ["Check this out: the leaf."]


def test_draft_keeps_the_first_drafts_failures_when_it_redrafts():
    _, clean = draft([CLEAN])
    assert clean.first_failures == ()
    _, redone = draft([BAD, CLEAN])
    assert redone.redrafted and {"em-dash", "hook1:stock-phrase"} <= {f.rule_id for f in redone.first_failures}
    assert redone.failures == ()


def test_a_private_product_link_is_not_sent_back_to_the_model():
    brief = Brief("feature", "x", "custom themes for pet profile pages", "http://127.0.0.1:8731/k2/")
    reply = {**CLEAN, "caption": CLEAN["caption"].replace("https://pawr.link", "http://127.0.0.1:8731/k2/")}
    host = FakeHost(llm_replies=[reply])
    d = draft_post(host, brief, BRAND, REFS, SHAPES)
    assert len(host.llm_calls) == 1 and d.failures == ()
    assert "private-link" in {f.rule_id for f in post_failures(d.caption, brief, BRAND, REFS)}


# ------------------------------------------------------------------ the visible checklist

@pytest.mark.parametrize("text", [
    LEAF_CAPTION,
    "Check this out: Your plant should — really — 🌿🌿 #a #b #c [link]\nhttps://fernlight.app",
    "Your agent should pull launch posts.\n\nYour agent should pull launch posts, now.\n\nhttp://localhost:3000/",
    "Leaves. " * 60 + "\nhttps://fernlight.app took 3 minutes",
])
def test_checklist_data_matches_the_checks(text):
    failures = post_failures(text, LEAF_BRIEF, LEAF_BRAND, REFS)
    lines = checklist(text, failures, "x")
    assert len(lines) <= 12 and all(line.text for line in lines)
    covered = [r for line in lines for r in line.rules]
    assert sorted(covered) == sorted({r.id for r in RULES} | {"unsupported-detail"})
    assert {r for line in lines if not line.ok for r in line.rules} >= {f.rule_id for f in failures}
    for line in lines:
        assert line.ok == (not set(line.rules) & {f.rule_id for f in failures})


def test_checklist_quotes_what_it_caught():
    text = "Check this out: leaves.\n\nhttp://127.0.0.1:8731/k3/"
    failures = post_failures(text, LEAF_BRIEF, LEAF_BRAND, REFS)
    bad = " ".join(line.text for line in checklist(text, failures, "x") if not line.ok)
    assert "Check this out" in bad and "127.0.0.1:8731" in bad


# ------------------------------------------------------------------ review fixes

@pytest.mark.parametrize("detail,hook", [("iOS users kept asking for widgets", "iOS users kept asking for widgets."),
                                         ("pawr.link pages got slow", "pawr.link pages got slow."),
                                         ("npm install works now", "Npm install works now.")])
def test_founder_hook_keeps_names_with_an_inner_capital_or_a_dot(detail, hook):
    assert founder_hook(detail) == hook


PAWR_BRIEF = Brief("feature", "x", "custom themes for pet pages", "https://pawr.link",
                   detail="people kept asking to match their pet page to their own site")
PAWR_BRAND = BrandKit("pawr.link", "https://pawr.link", "", "#fff", "#111", "#f60", None, None)
PAWR_LIVE = Draft(("Pet pages look the same. We built themes to match your site.",
                   "Custom themes for pet pages are live.", "Are you a pet owner? Stop settling."),
                  "Custom themes for pet pages are live.\n\nYou can now match your pet page to your own site."
                  "\n\nA small thing, but it makes the page feel like yours.\n\nhttps://pawr.link",
                  (), False, "Match your pet page to your own site.")


def test_a_founder_post_that_lost_what_shipped_fails_a_says_what_shipped_line():
    [founder, *_] = hook_options(PAWR_LIVE, PAWR_BRIEF, PAWR_BRAND, REFS)
    assert founder.dropped
    lines = checklist(founder.post, post_failures(founder.post, PAWR_BRIEF, PAWR_BRAND, REFS), "x",
                      use_case=PAWR_LIVE.use_case)
    missing = [line for line in lines if "use-case-missing" in line.rules]
    assert len(lines) <= 12 and len(missing) == 1 and not missing[0].ok
    kept = PAWR_LIVE.caption
    lines = checklist(kept, post_failures(kept, PAWR_BRIEF, PAWR_BRAND, REFS), "x", use_case=PAWR_LIVE.use_case)
    assert not [line for line in lines if "use-case-missing" in line.rules]


def test_the_prompt_tells_the_model_the_founder_words_lead():
    with_detail = build_brief_prompt(PAWR_BRIEF, PAWR_BRAND, REFS, SHAPES, set())[0]
    without = build_brief_prompt(BRIEF, BRAND, REFS, SHAPES, set())[0]
    assert len(with_detail.splitlines()) == len(without.splitlines()) + 1


def test_two_model_hooks_never_share_a_shape():
    brief = Brief("feature", "x", "photo diagnosis", "https://fernlight.app")
    hooks = ("Plant apps guess. We built Fernlight to look at the leaf.",
             "Most plant apps guess. We made Fernlight read the leaf.",
             "Are you a plant parent? Stop guessing at brown spots.")
    caption = "Fernlight reads the leaf for you.\n\nNow the photo itself gets you an answer.\n\nhttps://fernlight.app"
    opts = hook_options(leaf_draft(hooks, caption), brief, LEAF_BRAND, REFS)
    assert [o.text for o in opts] == [hooks[0], hooks[2]]


def test_no_shape_asks_for_we_built_or_watch_this():
    from launch_desk.hooks import HOOK_SHAPES
    for s in HOOK_SHAPES.values():
        assert "we built" not in s.shape.lower() and "watch this" not in s.shape.lower()


def test_a_hook_adding_a_second_invented_detail_is_filtered():
    brief = Brief("feature", "x", "photo diagnosis", "https://fernlight.app")
    caption = "Fernlight reads the leaf for you.\n\nIt took a week to build.\n\nhttps://fernlight.app"
    hooks = ("Snap a leaf, get an answer in 3 seconds.", "Plant care that looks at the leaf.",
             "Fernlight reads the leaf for you.")
    opts = hook_options(leaf_draft(hooks, caption), brief, LEAF_BRAND, REFS)
    assert hooks[0] not in [o.text for o in opts]


def test_the_least_bad_fallback_is_marked():
    brief = Brief("feature", "x", "photo diagnosis", "https://fernlight.app")
    hooks = ("Your plant should check this out.", "Check this out: the leaf.", "Your leaf should say hello to you.")
    [only] = hook_options(leaf_draft(hooks, "Snap a leaf.\n\nhttps://fernlight.app"), brief, LEAF_BRAND, REFS)
    assert only.flagged
    assert not any(o.flagged for o in hook_options(leaf_draft(), LEAF_BRIEF, LEAF_BRAND, REFS))


@pytest.mark.parametrize("hook", ["I built themes for pet pages.", "We’ve built themes for pet pages.",
                                  "Today we're launching themes.", "We've shipped themes.", "We just made themes."])
def test_we_built_covers_i_curly_apostrophes_and_launching(hook):
    from launch_desk.draft import hook_shape
    assert hook_shape(hook) == "we-built"
    assert hook_shape("Pet pages built for you.") == ""


# ------------------------------------------------------------------ counted claims (Task 22)

K3_BRIEF = Brief("feature", "x", "photo diagnosis: snap a leaf, get what is wrong and what to do",
                 "https://fernlight.app", detail="most of our support emails were blurry photos of sad leaves")
K3_BRAND = BrandKit("Fernlight", "https://fernlight.app",
                    "Fernlight is the friendly plant-care app for iPhone.", "#fff", "#111", "#17a34a", None, None)
THREE_TODAY = "You get three things to do today."


def test_three_things_to_do_today_fails_when_the_facts_do_not_say_it():
    [f] = unsupported_details(THREE_TODAY, facts_text(K3_BRIEF, K3_BRAND))
    assert set(f.found.split(", ")) == {"three", "today"}


@pytest.mark.parametrize("where", ["detail", "tagline", "digits"])
def test_three_things_to_do_today_passes_when_the_facts_say_it(where):
    from dataclasses import replace
    brief, brand = K3_BRIEF, K3_BRAND
    if where == "detail":
        brief = replace(brief, detail="you get three things to do today")
    elif where == "tagline":
        brand = replace(brand, tagline="Photo diagnosis: the likely cause and three things to do today.")
    else:
        brief = replace(brief, detail="3 things to do, today")
    assert unsupported_details(THREE_TODAY, facts_text(brief, brand)) == []


@pytest.mark.parametrize("text", ["Live today.", "Ready tonight.", "Shipping this week.", "Check it every day.",
                                  "Half the steps are gone.", "Done in minutes.", "Hundreds of themes."])
def test_time_and_count_claims_are_details(text):
    assert [f.rule_id for f in unsupported_details(text, FACTS)] == ["unsupported-detail"]


def test_checklist_says_which_details_came_from_the_site():
    from dataclasses import replace
    brand = replace(K3_BRAND, tagline="Photo diagnosis: the likely cause and three things to do today.")
    text = "Most of our support emails were blurry photos of sad leaves.\n\n" + THREE_TODAY + "\n\nhttps://fernlight.app"
    fails = post_failures(text, K3_BRIEF, brand, REFS)
    assert "unsupported-detail" not in {f.rule_id for f in fails}
    [line] = [c for c in checklist(text, fails, "x", from_site=site_details(text, K3_BRIEF, brand))
              if "unsupported-detail" in c.rules]
    assert line.ok and "three" in line.text and "today" in line.text
    [plain] = [c for c in checklist(text, fails, "x") if "unsupported-detail" in c.rules]
    assert "three" not in plain.text
    assert site_details(text, replace(K3_BRIEF, detail="three things today"), brand) == []


REPEATED = {**CLEAN, "caption": "Pet pages can wear your colours now.\n\nPick a colour theme in settings.\n\n"
                                "The colour theme follows the whole page.\n\nhttps://pawr.link"}


def test_a_repeated_phrase_triggers_the_redraft_with_the_phrase():
    host, d = draft([REPEATED, CLEAN])
    assert len(host.llm_calls) == 2 and "repeated-phrase" in prompt_of(host.llm_calls[1])
    assert '"colour theme"' in prompt_of(host.llm_calls[1])
    assert [f.found for f in d.first_failures if f.rule_id == "repeated-phrase"] == ["colour theme"]
    assert d.failures == ()


def test_checklist_fails_each_line_says_something_new_on_a_repeated_phrase():
    text = REPEATED["caption"]
    fails = post_failures(text, BRIEF, BRAND, REFS)
    [line] = [c for c in checklist(text, fails, "x") if "repeated-phrase" in c.rules]
    assert not line.ok and "colour theme" in line.text


# ------------------------------------------------------------------ the card line (Task 22)

from launch_desk.draft import card_line  # noqa: E402

K3_POST = ("Most of our support emails were blurry photos of sad leaves.\n\nFernlight reads a leaf photo and "
           "names the cause.\n\nhttps://fernlight.app")
K3_USE = "Fernlight reads a leaf photo and names the cause."


def k3_line(candidate, post=K3_POST, use_case=K3_USE):
    return card_line(candidate, post, K3_BRIEF, K3_BRAND, use_case)


def test_parse_keeps_the_card_line():
    assert parse_draft({**CLEAN, "card_line": "  Pet pages take themes now. "})[3] == "Pet pages take themes now."
    assert parse_draft({**CLEAN, "card_line": 7})[3] == ""


def test_draft_carries_the_model_card_line():
    _, d = draft([{**CLEAN, "card_line": "Pet pages take custom themes now."}])
    assert d.card_line == "Pet pages take custom themes now."
    assert "card_line" in DRAFT_SCHEMA["properties"]


def test_a_good_card_line_is_kept():
    good = "Fernlight now names what is wrong with a leaf."
    assert k3_line(good) == good


@pytest.mark.parametrize("bad", [
    "Fernlight now names what is wrong with your sad houseplant leaf.",  # 11 words
    "Say hello to leaf photo diagnosis in Fernlight.",  # stock phrase
    "Your plants should get a straight answer.",  # template opener
    "Fernlight reads [the photo] for you.",  # placeholder
    "Fernlight diagnoses a leaf in three seconds.",  # unsupported detail
    "Our support emails were blurry photos of sad leaves.",  # too close to line 1
    "",
])
def test_a_failing_card_line_falls_back_to_the_posts_what_shipped_line(bad):
    assert k3_line(bad) == "Fernlight reads a leaf photo and names the cause."


def test_without_a_usable_line_the_card_names_the_product_and_what_shipped():
    post = ("Most of our support emails were blurry photos of sad leaves.\n\nFernlight now reads every photo you "
            "send and names the likely cause right away.\n\nhttps://fernlight.app")
    line = k3_line("", post, use_case="")
    assert line.startswith("Fernlight") and "photo diagnosis" in line
    assert len(line.split()) <= 9
    assert not unsupported_details(line, facts_text(K3_BRIEF, K3_BRAND))


def test_the_card_line_is_never_line_one():
    post = "Fernlight reads a leaf photo.\n\nhttps://fernlight.app"
    for candidate in ("Fernlight reads a leaf photo.", ""):
        assert card_line(candidate, post, K3_BRIEF, K3_BRAND, "Fernlight reads a leaf photo.") != "Fernlight reads a leaf photo."


def test_the_prompt_asks_for_a_card_line_and_stays_short():
    instructions = build_brief_prompt(K3_BRIEF, K3_BRAND, REFS, SHAPES, set())[0]
    assert '"card_line"' in instructions


def test_a_use_case_detail_failure_names_its_phrases():
    invented = {**CLEAN, "use_case": "Pick a theme in one tap and the whole page follows."}
    _, d = draft([invented, invented])
    [f] = [f for f in d.failures if f.rule_id == "use-case:unsupported-detail"]
    assert f.found == "one tap"


def test_the_founder_option_drops_a_line_that_echoes_three_of_its_words():
    brief = Brief("feature", "x", "Brookhour 2.0: focus sessions now show up in your calendar", "https://brookhour.app",
                  detail="people kept asking to see their deep work next to their meetings")
    brand = BrandKit("Brookhour", "https://brookhour.app", "", "#fff", "#111", "#ff5b2e", None, None)
    d = Draft(("Brookhour 2.0 is out.", "Focus sessions land on your calendar.", "Are you a maker? Stop guessing."),
              "Brookhour 2.0 is out.\n\nFocus sessions now show up in your calendar.\n\n"
              "A calm focus timer for Mac, so deep work sits next to your meetings.\n\nhttps://brookhour.app",
              (), False, "Focus sessions now show up in your calendar.")
    [founder, *_] = hook_options(d, brief, brand, REFS)
    assert founder.dropped and "deep work" in founder.dropped[0]
    assert not {"repeated-phrase", "repeats-first-line"} & {f.rule_id for f in post_failures(founder.post, brief, brand, REFS)}


def test_a_lower_case_line_triggers_the_redraft():
    from dataclasses import replace
    lower = {**CLEAN, "caption": CLEAN["caption"].replace("Pick a theme", "pick a theme")}
    host, d = draft([lower, CLEAN], brand=replace(BRAND, voice_samples=()))
    assert len(host.llm_calls) == 2 and "lower-start" in prompt_of(host.llm_calls[1])
    assert d.failures == ()


def test_a_lower_case_voice_drops_the_capitals_rule_from_the_prompt():
    strict = build_brief_prompt(BRIEF, BRAND, REFS, SHAPES, set())[0]
    relaxed = build_brief_prompt(BRIEF, BRAND, REFS, SHAPES, {"lower-start"})[0]
    assert len(relaxed) < len(strict)


# ------------------------------------------------------------------ fallback card lines end cleanly (R43)

from launch_desk.draft import _product_line  # noqa: E402

_BAD_END = {"a", "an", "the", "and", "or", "but", "so", "to", "of", "in", "on", "at", "for", "with", "from", "by"}


def product_line(name, what):
    return _product_line(Brief("feature", "x", what, "https://x.app"),
                         BrandKit(name, "https://x.app", "", "#fff", "#111", "#f60", None, None))


@pytest.mark.parametrize("name,what,expected", [
    ("posts.design", "posts.design MCP: your agent can now search 5,000+ real launch posts, OG images and Headers",
     "posts.design MCP is live."),
    ("Brookhour", "Brookhour 2.0: focus sessions now show up in your calendar", "Brookhour 2.0 is live."),
    ("Fernlight", "photo diagnosis: snap a leaf, get what is wrong and what to do", "Fernlight: photo diagnosis, snap a leaf"),
    ("Inkwell", "dark mode for the editor", "Inkwell: dark mode for the editor"),
    ("Pawr", "a much faster way to share every single pet page with your friends and family", "Pawr is live."),
])
def test_the_fallback_card_line_ends_at_a_natural_break(name, what, expected):
    assert product_line(name, what) == expected


@pytest.mark.parametrize("what", ["search for 5,000+ launch posts", "themes for the", "faster pages and",
                                  "pages that load in 3", "custom themes: now in"])
def test_the_fallback_never_ends_on_a_number_or_small_word(what):
    line = product_line("Pawr", what)
    last = line.rstrip(".").split()[-1].lower()
    assert last not in _BAD_END and not last[0].isdigit() and len(line.split()) <= 9
