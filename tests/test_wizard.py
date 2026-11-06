import io
import json
import logging
import re
from pathlib import Path

import pytest
from PIL import Image

from fakes import FakeHost
from launch_desk import wizard
from launch_desk.brand import PRESET_ACCENTS
from launch_desk.host import SourceUnavailable
from launch_desk.models import Brief
from launch_desk.references import McpSource, build_search_args, rank_references
from launch_desk.rules import RULES
from launch_desk.setup_check import ADD_SERVER

FIX = Path(__file__).parent / "fixtures"
URL = "https://pawr.link"
ARGS = f"{URL} custom themes for pet profile pages -- people kept asking to match their own site"
DETAIL = "people kept asking to match their own site"
SEARCH = json.loads((FIX / "search_references.json").read_text(encoding="utf-8"))
PACK_IDS = {e["id"] for e in json.loads(
    (Path(wizard.__file__).parent / "sample_pack.json").read_text(encoding="utf-8"))}
EM_DASH_MESSAGE = next(r.message for r in RULES if r.id == "em-dash")

CLEAN = {
    "use_case": "Pick a theme and the whole page follows.",
    "hooks": ["Pet pages can wear your colours now.",
              "Check this out: your pet's page in your colours.",
              "Your pet's page, your theme."],
    "caption": "Pet pages can wear your colours now.\n\nPick a theme in settings and the whole page follows."
               "\n\nhttps://pawr.link",
}
OTHER = {
    "use_case": "Match the page to your own site.",
    "hooks": ["Themes for pet pages are live.", "Check this out: pet pages match your site.",
              "Match your pet's page to your site."],
    "caption": "Themes for pet pages are live.\n\nMatch the page to your own site.\n\nhttps://pawr.link",
}
PNG = io.BytesIO()
Image.new("RGB", (64, 64), "#e8590c").save(PNG, "PNG")

PHASE1 = ["feature", "x", "keep", "1"]


def fake_get(calls=None):
    def get(url):
        if calls is not None:
            calls.append(url)
        if url.endswith((".png", ".ico")):
            return PNG.getvalue()
        if ".css" in url:
            return (FIX / "pawr.css").read_bytes()
        return (FIX / "pawr.html").read_bytes()
    return get


def no_get(url):
    raise AssertionError(f"no site read expected, got {url}")


def make_host(choices=(), llm=(CLEAN,), access=None, search=SEARCH, **kw):
    replies = {"get_my_access": access if access is not None else {"allowed": True}}
    if search is not None:
        replies["search_references"] = search
    return FakeHost(choices=list(choices), llm_replies=list(llm), mcp_replies=replies, **kw)


def start(host, args=ARGS, get=None):
    return wizard.run_launch(host, args, get=get or fake_get())


def resume(host, *choices):
    host.choices.extend(choices)
    return wizard.run_launch(host, "", get=no_get)


def packs(host):
    return list((host.data_dir() / "packs").glob("*/pack.json"))


def files(host):
    return [p for p in host.data_dir().rglob("*") if p.is_file()]


def media(reply):
    return [Path(line[len("MEDIA:"):].strip()) for line in reply.splitlines() if line.startswith("MEDIA:")]


def questions(host, choice_value):
    """Questions whose choices include this value."""
    return [q for q, ch in host.asked if any(c["value"] == choice_value for c in ch)]


def tool_names(host):
    return [t for t, _ in host.mcp_calls]


def pending(host):
    return host.state_get("pending")


# ------------------------------------------------------------------ stops before work

@pytest.mark.parametrize("kw", [{"unattended": True}, {"surface": "cron"}])
def test_unattended_run_returns_without_asking(kw):
    host = make_host(PHASE1, **kw)
    assert start(host).strip()
    assert resume(host).strip()
    assert host.asked == [] and host.mcp_calls == [] and host.llm_calls == []
    assert files(host) == []


@pytest.mark.parametrize("args", ["", "   ", "help", f"{URL}", f"{URL} -- {DETAIL}", "brand"])
def test_missing_args_returns_usage(args):
    host = make_host(PHASE1)
    reply = wizard.run_launch(host, args, get=no_get)
    assert "/launch" in reply
    assert host.asked == [] and host.mcp_calls == [] and host.llm_calls == []


def test_first_run_without_brand_or_url_stops_with_usage():
    host = make_host(PHASE1)
    reply = wizard.run_launch(host, "custom themes for pet profile pages", get=no_get)
    assert "https://" in reply
    assert host.asked == [] and host.mcp_calls == []


def test_parse_args_strips_url_and_splits_detail():
    p = wizard.parse_args(f"custom themes {URL}/pets, for pet pages -- {DETAIL}")
    assert p.url == f"{URL}/pets"
    assert p.what == "custom themes for pet pages"
    assert p.detail == DETAIL
    assert wizard.parse_args("just a thing").url is None
    assert wizard.parse_args("a thing -- with https://x.test/a.").url == "https://x.test/a"


def test_no_network_before_the_first_answer():
    host = make_host([])
    start(host, get=no_get)
    assert host.mcp_calls == [] and len(host.asked) == 1


# ------------------------------------------------------------------ phase 1

def test_phase1_ends_with_media_lines_and_stores_pending():
    calls = []
    host = make_host(PHASE1)
    reply = start(host, get=fake_get(calls))

    assert calls and calls[0] == URL
    shown = media(reply)
    assert len(shown) == 2 and all(p.is_file() for p in shown)
    assert all((host.data_dir() / "pending") in p.parents for p in shown)
    assert {Image.open(p).size for p in shown} >= {(1200, 675)}

    p = pending(host)
    assert p["hooks"] == CLEAN["hooks"] and p["hook"] == CLEAN["hooks"][0]
    assert p["caption"] == CLEAN["caption"]
    assert p["brief"]["what"] == "custom themes for pet profile pages"
    assert p["brief"]["detail"] == DETAIL and p["brief"]["network"] == "x"
    assert len(p["refs"]) == 3 and set(p["shown"]) == {r["id"] for r in p["refs"]}
    assert isinstance(p["created_at"], float)
    assert {p["card"], p["preview"]} == {str(s) for s in shown}

    assert packs(host) == [] and host.state_get("brand") is None
    assert DETAIL in host.llm_calls[0][1]
    assert len(host.said) == 4  # reading the site, finding references (+ screenshot tip), drafting, drawing
    hook_q = questions(host, "1")[0]
    for r in p["refs"]:
        assert r["url"] in hook_q and r["brand"] in hook_q


def test_hook_question_lists_each_reference_with_its_shape():
    from launch_desk.references import shape_note
    host = make_host(PHASE1)
    start(host)
    p = pending(host)
    lines = questions(host, "1")[0].splitlines()
    for r in p["refs"]:
        line = next(ln for ln in lines if r["url"] in ln)
        assert r["brand"] in line and shape_note(r["caption"]) in line
    # the draft was told to borrow the top reference's shape, and the question names it
    assert shape_note(p["refs"][0]["caption"]) in host.llm_calls[0][0]


def test_feature_detail_about_asking_picks_the_you_asked_hook():
    from launch_desk.hooks import HOOK_SHAPES
    host = make_host(PHASE1)
    start(host)  # ARGS carries "people kept asking ..."
    assert HOOK_SHAPES["you-asked"].shape in host.llm_calls[0][1]
    quiet = make_host(PHASE1)
    start(quiet, args=f"{URL} custom themes for pet profile pages")
    assert HOOK_SHAPES["you-asked"].shape not in quiet.llm_calls[0][1]


def test_remaining_failures_are_shown_in_the_phase1_reply():
    bad = {"use_case": "Pick one.", "hooks": ["Pet pages — themes — are here.", "Themes.", "Your theme."],
           "caption": "Pet pages — themes — are here.\n\nPick one.\n\nhttps://pawr.link"}
    host = make_host(PHASE1, llm=(bad, bad))
    reply = start(host)
    flagged = [line for line in reply.splitlines() if EM_DASH_MESSAGE in line]
    assert len(flagged) == 1


@pytest.mark.parametrize("surface,in_reply", [("cli", True), ("tui", True), ("local", True),
                                              ("telegram", False)])
def test_setup_and_linkedin_lines_appear_once(surface, in_reply):
    down = SourceUnavailable("MCP server 'posts' is not connected")
    x_host = make_host(PHASE1, access=down, surface=surface)
    li_host = make_host(["feature", "linkedin", "keep", "1"], access=down, surface=surface)
    x_reply, li_reply = start(x_host), start(li_host)
    assert pending(li_host)["brief"]["network"] == "x"
    said = "\n".join(li_host.said)
    assert (li_reply.count(ADD_SERVER), said.count(ADD_SERVER)) == ((1, 0) if in_reply else (0, 1))
    extra_reply = [line for line in plain(li_reply) if line and line not in plain(x_reply)]
    extra_said = [line for line in li_host.said if line not in x_host.said]
    assert (len(extra_reply), len(extra_said)) == ((1, 0) if in_reply else (0, 1))


def test_mcp_down_uses_sample_pack_and_says_so_in_reply():
    host = make_host(PHASE1, access=SourceUnavailable("MCP server 'posts' is not connected"))
    reply = start(host)
    assert "search_references" not in tool_names(host)
    assert {r["id"] for r in pending(host)["refs"]} <= PACK_IDS
    assert reply.count(ADD_SERVER) == 1
    assert len(media(reply)) == 2


def test_unreachable_tries_live_then_falls_back_to_sample_pack():
    host = make_host(PHASE1, access=SourceUnavailable("transport is down", kind="timeout"),
                     search=SourceUnavailable("timeout", kind="timeout"))
    start(host)
    assert tool_names(host).count("search_references") == 1
    assert {r["id"] for r in pending(host)["refs"]} <= PACK_IDS


def test_too_few_live_matches_falls_back_and_says_it_differently():
    few = dict(SEARCH, items=SEARCH["items"][1:2], also=[])
    host = make_host(PHASE1, search=few)
    start(host)
    down = make_host(PHASE1, search=SourceUnavailable("timeout", kind="timeout"))
    start(down)
    assert {r["id"] for r in pending(host)["refs"]} <= PACK_IDS
    assert set(host.said) - set(down.said) and set(down.said) - set(host.said)


def plain(reply):
    return [line for line in reply.splitlines() if not line.startswith("MEDIA:")]


@pytest.mark.parametrize("kind", ["rebrand", "partnership"])
def test_pack_without_the_category_adds_one_note_to_the_reply(kind):
    down = SourceUnavailable("MCP server 'posts' is not connected")
    feature = make_host(PHASE1, access=down)
    other = make_host([kind, "x", "keep", "1"], access=down)
    feature_reply, other_reply = start(feature), start(other)
    extra = [line for line in plain(other_reply) if line and line not in plain(feature_reply)]
    assert len(extra) == 1
    assert len(plain(other_reply)) == len(plain(feature_reply)) + 1
    assert {r["id"] for r in pending(other)["refs"]} <= PACK_IDS


@pytest.mark.parametrize("answered", range(len(PHASE1)))
def test_none_at_each_phase1_question_saves_nothing(answered):
    host = make_host(PHASE1[:answered])
    reply = start(host)
    assert reply.strip() and "Traceback" not in reply
    assert pending(host) is None and host.state_get("brand") is None
    assert files(host) == []
    assert len(host.asked) == answered + 1


def test_new_launch_replaces_the_waiting_draft():
    host = make_host(PHASE1 + PHASE1, llm=(CLEAN, OTHER))
    first = start(host)
    old_dir = Path(pending(host)["dir"])
    usage = start(host, args="dark mode for pet pages")  # no brand stored yet and no URL
    assert "/launch" in usage and old_dir.is_dir()
    second = start(host, args=f"{URL} dark mode for pet pages")
    new_dir = Path(pending(host)["dir"])
    assert new_dir != old_dir and not old_dir.exists()
    assert [d for d in (host.data_dir() / "pending").iterdir()] == [new_dir]
    assert len(second.splitlines()) == len(first.splitlines()) + 2  # one note line and a blank


def test_stopping_a_new_launch_keeps_the_waiting_draft():
    host = make_host(PHASE1 + ["feature"])
    start(host)
    before = pending(host)
    start(host, args=f"{URL} dark mode")
    assert pending(host) == before and Path(before["dir"]).is_dir()


@pytest.mark.parametrize("exists", [True, False])
def test_screenshot_from_state_is_used_only_if_file_exists(tmp_path, monkeypatch, exists):
    shot = tmp_path / "shot.png"
    if exists:
        Image.new("RGB", (1600, 900), "white").save(shot)
    seen = []
    real = wizard.render_card

    def spy(brand, screenshot, headline, size, out, **kw):
        seen.append((screenshot, headline))
        return real(brand, screenshot, headline, size, out, **kw)

    monkeypatch.setattr(wizard, "render_card", spy)
    host = make_host(PHASE1)
    host.state_set("screenshot", str(shot))
    start(host)
    assert len(seen) == 1
    assert bool(seen[0][0]) == exists and seen[0][1] == CLEAN["hooks"][0]


def test_long_hook_falls_back_to_what_for_card_headline(monkeypatch):
    long = dict(CLEAN, hooks=["Pet pages can wear your colours now, and so can every link page on it today",
                              *CLEAN["hooks"][1:]])
    seen = []
    real = wizard.render_card

    def spy(brand, screenshot, headline, size, out, **kw):
        seen.append(headline)
        return real(brand, screenshot, headline, size, out, **kw)

    monkeypatch.setattr(wizard, "render_card", spy)
    start(make_host(PHASE1, llm=(long, long)))
    assert seen == ["Custom themes for pet profile pages"]


def test_card_drops_check_this_out_but_the_post_keeps_it(monkeypatch):
    seen = []
    real = wizard.render_card

    def spy(brand, screenshot, headline, size, out, **kw):
        seen.append(headline)
        return real(brand, screenshot, headline, size, out, **kw)

    monkeypatch.setattr(wizard, "render_card", spy)
    host = make_host(["feature", "x", "keep", "2"])
    start(host)
    hook = CLEAN["hooks"][1]
    assert seen == ["Your pet's page in your colours."]
    assert pending(host)["hook"] == hook
    resume(host, "approve")
    assert json.loads(packs(host)[0].read_text())["caption"].splitlines()[0] == hook


def test_card_headline_capitalises_the_fallback():
    assert wizard.card_headline("", "dark mode for pet pages") == "Dark mode for pet pages"
    assert wizard.card_headline("check this out: dark mode.", "x") == "Dark mode."


def test_unreadable_site_offers_the_five_presets_and_continues():
    def down(url):
        raise TimeoutError("slow")

    host = make_host(["feature", "x", PRESET_ACCENTS[4], "1"])
    reply = start(host, get=down)
    accent_q = [ch for q, ch in host.asked if any(c["value"] == PRESET_ACCENTS[0] for c in ch)]
    assert [c["value"] for c in accent_q[0]] == list(PRESET_ACCENTS)
    assert pending(host)["brand"]["accent"] == PRESET_ACCENTS[4]
    assert len(media(reply)) == 2


# ------------------------------------------------------------------ phase 2

def test_phase2_approve_writes_pack_and_clears_pending():
    host = make_host(PHASE1)
    start(host)
    pending_dir = Path(pending(host)["dir"])
    reply = resume(host, "approve")

    [pack_json] = packs(host)
    data = json.loads(pack_json.read_text())
    assert data["hook"] == CLEAN["hooks"][0] and data["brief"]["detail"] == DETAIL
    assert data["intent_url"] in reply and str(pack_json.parent) in reply
    shown = media(reply)
    assert len(shown) == 2 and all(p.is_file() and p.parent == pack_json.parent for p in shown)
    assert Image.open(pack_json.parent / data["files"]["card_x"]).size == (1200, 675)
    assert pending(host) is None and not pending_dir.exists()

    stored = host.state_get("brand")
    assert stored["url"] == URL and stored["accent"] == data["brand"]["accent"]
    assert Path(stored["logo_path"]).parent == host.data_dir() / "brand"
    assert Path(stored["logo_path"]).is_file()
    assert CLEAN["hooks"][0] in (pack_json.parent / "alt-text.txt").read_text()


@pytest.mark.parametrize("surface", ["cli", "tui", "local", "desktop", "acp"])
def test_approve_on_a_local_client_shows_the_raw_intent_url_and_full_folder(surface):
    host = make_host(PHASE1, surface=surface)
    start(host)
    reply = resume(host, "approve")
    [pack_json] = packs(host)
    url = json.loads(pack_json.read_text())["intent_url"]
    lines = plain(reply)
    assert any(url in line.split() for line in lines)
    assert f"]({url})" not in reply
    assert any(str(pack_json.parent) in line for line in lines)


@pytest.mark.parametrize("surface", ["telegram", "discord", "slack", "some-new-app"])
def test_approve_in_a_chat_app_links_the_intent_and_names_only_the_folder(surface):
    host = make_host(PHASE1, surface=surface)
    start(host)
    reply = resume(host, "approve")
    [pack_json] = packs(host)
    url = json.loads(pack_json.read_text())["intent_url"]
    lines = plain(reply)
    link = re.compile(r"\[[^\]\n]+\]\(" + re.escape(url) + r"\)")
    assert sum(len(link.findall(line)) for line in lines) == 1
    assert all(url not in link.sub("", line) for line in lines)
    assert any(pack_json.parent.name in line for line in lines)
    assert not any(str(pack_json.parent) in line for line in lines)


def test_phase2_discard_clears_pending_and_leaves_no_files():
    host = make_host(PHASE1)
    start(host)
    reply = resume(host, "discard")
    assert reply.strip() and pending(host) is None
    assert files(host) == [] and host.state_get("brand") is None


def test_phase2_none_keeps_the_draft_waiting():
    host = make_host(PHASE1)
    start(host)
    before = pending(host)
    resume(host)                 # no answer to Approve / Change hook / Swap / Discard
    resume(host, "hook")         # no answer to the hook question
    assert pending(host) == before
    assert Path(before["card"]).is_file() and packs(host) == []


def test_expired_pending_is_treated_as_absent(monkeypatch):
    host = make_host(PHASE1)
    start(host)
    pending_dir = Path(pending(host)["dir"])
    later = wizard._now() + wizard.PENDING_TTL_S + 60
    monkeypatch.setattr(wizard, "_now", lambda: later)
    asked = len(host.asked)
    reply = resume(host, "approve")
    assert "/launch" in reply and len(host.asked) == asked
    assert pending(host) is None and not pending_dir.exists() and packs(host) == []


def test_swap_references_reranks_without_repeats():
    host = make_host(PHASE1, llm=(CLEAN, OTHER))
    start(host)
    first = {r["id"] for r in pending(host)["refs"]}
    brief = Brief("feature", "x", "custom themes for pet profile pages", URL)
    items = McpSource(FakeHost(mcp_replies={"search_references": SEARCH})).search(build_search_args(brief))
    assert first == {r.id for r in rank_references(items, brief)}

    reply = resume(host, "swap", "2")
    p = pending(host)
    swapped = {r["id"] for r in p["refs"]}
    assert len(swapped) == 3 and not (swapped & first)
    assert set(p["shown"]) == first | swapped
    assert p["hook"] == OTHER["hooks"][1] and p["caption"] == OTHER["caption"]
    assert tool_names(host).count("search_references") == 1  # no second search
    assert len(host.llm_calls) == 2
    assert all(m.is_file() for m in media(reply))

    resume(host, "approve")
    data = json.loads(packs(host)[0].read_text())
    assert {r["id"] for r in data["references"]} == swapped and data["hook"] == OTHER["hooks"][1]


def test_change_hook_picks_among_the_same_three_and_rerenders():
    host = make_host(PHASE1)
    start(host)
    old = pending(host)
    reply = resume(host, "hook", "3")
    p = pending(host)
    assert p["hook"] == CLEAN["hooks"][2] and p["hooks"] == old["hooks"]
    assert len(host.llm_calls) == 1
    assert {str(m) for m in media(reply)} == {p["card"], p["preview"]}
    assert all(m.is_file() for m in media(reply))
    assert not Path(old["card"]).exists()
    resume(host, "approve")
    data = json.loads(packs(host)[0].read_text())
    assert data["caption"].splitlines()[0] == CLEAN["hooks"][2]


def test_second_launch_reuses_stored_brand_silently():
    host = make_host(PHASE1, llm=(CLEAN, CLEAN))
    start(host)
    resume(host, "approve")
    host.choices.extend(["feature", "x", "1"])
    reply = start(host, args="dark mode for pet pages", get=no_get)
    assert len(questions(host, "keep")) == 1
    assert len(media(reply)) == 2


def test_picked_accent_is_used():
    host = make_host(["feature", "x", PRESET_ACCENTS[2], "1"])
    start(host)
    resume(host, "approve")
    assert json.loads(packs(host)[0].read_text())["brand"]["accent"] == PRESET_ACCENTS[2]
    assert host.state_get("brand")["accent"] == PRESET_ACCENTS[2]
    [accent_q] = [ch for q, ch in host.asked if any(c["value"] == "keep" for c in ch)]
    assert {c["value"] for c in accent_q} == {"keep", *PRESET_ACCENTS}


def test_brand_subcommand_rereads_and_stores():
    host = make_host([PRESET_ACCENTS[1]])
    reply = wizard.run_launch(host, f"brand {URL}", get=fake_get())
    assert host.state_get("brand")["accent"] == PRESET_ACCENTS[1]
    assert host.state_get("brand")["url"] == URL
    assert host.mcp_calls == [] and host.llm_calls == [] and packs(host) == []
    assert reply.strip()


def test_new_url_rereads_brand():
    host = make_host(PHASE1, llm=(CLEAN, CLEAN))
    start(host)
    resume(host, "approve")
    calls = []
    host.choices.extend(["feature", "x", "keep", "1"])
    start(host, args="https://other.test themes", get=fake_get(calls))
    assert calls and calls[0] == "https://other.test"
    assert pending(host)["brand"]["url"] == "https://other.test"


# ------------------------------------------------------------------ failures

def test_draft_error_gives_plain_message_and_saves_nothing(caplog):
    host = make_host(PHASE1, llm=("not json", "still not json"))
    with caplog.at_level(logging.ERROR, logger=wizard.__name__):
        reply = start(host)
    assert "Traceback" not in reply and len(reply.splitlines()) == 1
    assert pending(host) is None and files(host) == []
    assert any(r.exc_info for r in caplog.records)


def test_unexpected_exception_never_reaches_the_gateway(caplog):
    host = make_host(PHASE1)

    def boom(question, choices):
        raise KeyError("internal")

    host.choose = boom
    with caplog.at_level(logging.ERROR, logger=wizard.__name__):
        reply = start(host)
    assert reply.strip() and "KeyError" not in reply and "Traceback" not in reply
    assert any(r.exc_info for r in caplog.records)
    assert files(host) == []


def test_swap_draft_error_keeps_the_draft_waiting(caplog):
    host = make_host(PHASE1, llm=(CLEAN,))  # the redraft gets no reply
    start(host)
    before = pending(host)
    with caplog.at_level(logging.ERROR, logger=wizard.__name__):
        reply = resume(host, "swap")
    assert "Traceback" not in reply and pending(host) == before
    assert Path(before["card"]).is_file()


# ------------------------------------------------------------------ voice samples

def with_brand(**kw):
    """A host with the pawr brand stored (one approved launch) and nothing waiting."""
    host = make_host(PHASE1, llm=(CLEAN, CLEAN), **kw)
    start(host)
    resume(host, "approve")
    return host


def voice(host, text, sep=": "):
    return wizard.run_launch(host, f"voice{sep}{text}", get=no_get)


def samples(host):
    return host.state_get("brand")["voice_samples"]


def test_launch_text_starting_with_voice_is_a_normal_launch():
    host = make_host(PHASE1)
    start(host, f"Voice mode in the editor -- people asked {URL}")
    assert questions(host, "feature") and pending(host) is not None


@pytest.mark.parametrize("sep", [":", " :", ": "])
def test_voice_colon_forms_store_a_sample_and_clear(sep):
    host = with_brand()
    voice(host, "hi there", sep=sep)
    assert samples(host) == ["hi there"]
    voice(host, "clear", sep=sep)
    assert samples(host) == []


def test_voice_without_a_brand_saves_nothing():
    host = make_host()
    reply = voice(host, "We shipped themes. People asked.")
    assert reply.strip() and host.state_get("brand") is None
    assert host.asked == [] and host.mcp_calls == [] and host.llm_calls == []


def test_voice_keeps_the_last_three_samples_and_clear_empties_them():
    host = with_brand()
    brand_before = dict(host.state_get("brand"))
    for n in range(1, 5):
        assert voice(host, f"Past post number {n}.").strip()
    assert samples(host) == ["Past post number 2.", "Past post number 3.", "Past post number 4."]
    after = host.state_get("brand")
    assert {k: v for k, v in after.items() if k != "voice_samples"} == \
        {k: v for k, v in brand_before.items() if k != "voice_samples"}
    assert Path(after["logo_path"]).is_file()
    voice(host, "clear")
    assert samples(host) == []
    assert len(host.llm_calls) == 1  # only the launch drafted; voice calls nothing


def test_stored_voice_reaches_the_draft():
    host = with_brand()
    voice(host, "Pet pages got themes. Ask and you shall receive.")
    host.choices.extend(["feature", "x", "1"])
    start(host, args="dark mode for pet pages", get=no_get)
    assert "Ask and you shall receive." in host.llm_calls[-1][1]


def test_relaxed_rules_add_one_note_to_the_phase1_reply():
    plain_host, styled = with_brand(), with_brand()
    voice(styled, "Themes — finally — are here.")
    for h in (plain_host, styled):
        h.choices.extend(["feature", "x", "1"])
        h.llm_replies.append(CLEAN)
    a = start(plain_host, args="dark mode for pet pages", get=no_get)
    b = start(styled, args="dark mode for pet pages", get=no_get)
    extra = [line for line in plain(b) if line and line not in plain(a)]
    assert len(extra) == 1


# ------------------------------------------------------------------ screenshot reuse

def test_reused_screenshot_is_named_in_progress_and_reply(tmp_path):
    shot = tmp_path / "pricing-page.png"
    Image.new("RGB", (1600, 900), "white").save(shot)
    host = make_host(PHASE1)
    host.state_set("screenshot", str(shot))
    reply = start(host)
    assert any(shot.name in line for line in host.said)
    assert sum(shot.name in line for line in plain(reply)) == 1
    none = make_host(PHASE1)
    assert "pricing-page" not in start(none)


@pytest.mark.parametrize("choice", ["approve", "discard"])
def test_screenshot_is_forgotten_after_approve_or_discard(tmp_path, choice):
    shot = tmp_path / "shot.png"
    Image.new("RGB", (1600, 900), "white").save(shot)
    host = make_host(PHASE1)
    host.state_set("screenshot", str(shot))
    start(host)
    assert host.state_get("screenshot") == str(shot)
    resume(host, choice)
    assert host.state_get("screenshot") is None
    assert shot.is_file()  # the founder's file stays where it was


# ------------------------------------------------------------------ media delivery

def test_media_lines_come_from_host_media_path():
    host = make_host(PHASE1)
    given = []

    def media_path(src, label):
        given.append((Path(src), label))
        return f"/media-cache/{label}-{len(given)}.png"

    host.media_path = media_path
    first = start(host)
    assert [str(m) for m in media(first)] == ["/media-cache/preview-1.png", "/media-cache/card-2.png"]
    p = pending(host)
    assert given == [(Path(p["preview"]), "preview"), (Path(p["card"]), "card")]
    assert all(src.is_file() for src, _ in given)

    changed = resume(host, "hook", "3")
    assert [str(m) for m in media(changed)] == ["/media-cache/preview-3.png", "/media-cache/card-4.png"]

    done = resume(host, "approve")
    pack_dir = packs(host)[0].parent
    assert [str(m) for m in media(done)] == ["/media-cache/card-5.png", "/media-cache/preview-6.png"]
    assert [src.parent for src, _ in given[4:]] == [pack_dir, pack_dir]
    assert all(src.is_file() for src, _ in given[4:])


def _shot(tmp_path, name):
    shot = tmp_path / name
    Image.new("RGB", (1600, 900), "white").save(shot)
    return str(shot)


@pytest.mark.parametrize("choice", ["approve", "discard"])
def test_newer_screenshot_survives_approve_or_discard(tmp_path, choice):
    host = make_host(PHASE1)
    host.state_set("screenshot", _shot(tmp_path, "old.png"))
    start(host)
    newer = _shot(tmp_path, "new.png")
    host.state_set("screenshot", newer)
    resume(host, choice)
    assert host.state_get("screenshot") == newer


def _expired_draft(host, monkeypatch, tmp_path):
    host.state_set("screenshot", _shot(tmp_path, "old.png"))
    start(host)
    later = wizard._now() + wizard.PENDING_TTL_S + 60
    monkeypatch.setattr(wizard, "_now", lambda: later)


def test_dropping_an_expired_draft_clears_its_screenshot(tmp_path, monkeypatch):
    host = make_host(PHASE1)
    _expired_draft(host, monkeypatch, tmp_path)
    assert wizard._load_pending(host) is None
    assert host.state_get("screenshot") is None


def test_dropping_an_expired_draft_keeps_a_newer_screenshot(tmp_path, monkeypatch):
    host = make_host(PHASE1)
    _expired_draft(host, monkeypatch, tmp_path)
    newer = _shot(tmp_path, "new.png")
    host.state_set("screenshot", newer)
    assert wizard._load_pending(host) is None
    assert host.state_get("screenshot") == newer


# ------------------------------------------------------------------ two screenshots and layouts

def _render_spy(monkeypatch):
    seen = []
    real = wizard.render_card

    def spy(brand, screenshots, headline, size, out, **kw):
        seen.append({"shots": [str(s) for s in screenshots], "headline": headline, **kw})
        return real(brand, screenshots, headline, size, out, **kw)

    monkeypatch.setattr(wizard, "render_card", spy)
    return seen


def test_two_attached_screenshots_make_a_split_card_named_in_the_reply(tmp_path, monkeypatch):
    seen = _render_spy(monkeypatch)
    a, b = _shot(tmp_path, "home.png"), _shot(tmp_path, "settings.png")
    host = make_host(PHASE1)
    host.state_set("screenshot", [a, b])
    reply = start(host)
    assert seen[0]["shots"] == [a, b] and seen[0]["layout"] == "split"
    assert pending(host)["screenshots"] == [a, b] and pending(host)["layout"] == "split"
    named = [line for line in plain(reply) if "home.png" in line]
    assert len(named) == 1 and "settings.png" in named[0]


@pytest.mark.parametrize("state,layout", [(None, "statement"), (1, "hero"), (2, "split")])
def test_phase1_layout_follows_the_attached_screenshots(tmp_path, monkeypatch, state, layout):
    seen = _render_spy(monkeypatch)
    host = make_host(PHASE1)
    if state:
        host.state_set("screenshot", [_shot(tmp_path, f"{i}.png") for i in range(state)])
    start(host)
    assert seen[0]["layout"] == layout == pending(host)["layout"]


@pytest.mark.parametrize("state,layout,shots", [(0, "statement", 0), (1, "hero", 1), (2, "split", 2)])
def test_approved_pack_describes_the_card_that_was_drawn(tmp_path, state, layout, shots):
    host = make_host(PHASE1)
    if state:
        host.state_set("screenshot", [_shot(tmp_path, f"{i}.png") for i in range(state)])
    start(host)
    resume(host, "approve", "no")
    [pack_json] = packs(host)
    assert json.loads(pack_json.read_text())["card"] == {"layout": layout, "screenshots": shots}


def test_missing_screenshot_files_are_skipped(tmp_path, monkeypatch):
    seen = _render_spy(monkeypatch)
    a = _shot(tmp_path, "a.png")
    host = make_host(PHASE1)
    host.state_set("screenshot", [str(tmp_path / "gone.png"), a])
    start(host)
    assert seen[0]["shots"] == [a] and seen[0]["layout"] == "hero"


def test_change_hook_rerenders_with_the_layout_the_new_hook_gets(tmp_path, monkeypatch):
    seen = _render_spy(monkeypatch)
    asked = []

    def choose(shots, hook):
        asked.append(hook)
        return "statement" if hook == CLEAN["hooks"][0] else "hero"

    monkeypatch.setattr(wizard, "choose_layout", choose)
    host = make_host(PHASE1)
    host.state_set("screenshot", [_shot(tmp_path, "a.png")])
    start(host)
    resume(host, "hook", "3")
    assert asked == [CLEAN["hooks"][0], CLEAN["hooks"][2]]
    assert [s["layout"] for s in seen] == ["statement", "hero"]
    assert pending(host)["layout"] == "hero"


@pytest.mark.parametrize("choice", ["approve", "discard"])
def test_forget_clears_only_the_screenshots_the_draft_used(tmp_path, choice):
    a, b, c = (_shot(tmp_path, n) for n in ("a.png", "b.png", "c.png"))
    host = make_host(PHASE1)
    host.state_set("screenshot", [a, b])
    start(host)
    host.state_set("screenshot", [b, c])  # c attached after the draft: a dropped as the oldest
    resume(host, choice)
    assert host.state_get("screenshot") == [c]


def test_forget_keeps_a_screenshot_attached_after_a_one_screenshot_draft(tmp_path):
    a, b = _shot(tmp_path, "a.png"), _shot(tmp_path, "b.png")
    host = make_host(PHASE1)
    host.state_set("screenshot", [a])
    start(host)
    host.state_set("screenshot", [a, b])
    resume(host, "approve")
    assert host.state_get("screenshot") == [b]
    assert Path(a).is_file() and Path(b).is_file()


def test_forget_clears_both_when_the_draft_used_both(tmp_path):
    a, b = _shot(tmp_path, "a.png"), _shot(tmp_path, "b.png")
    host = make_host(PHASE1)
    host.state_set("screenshot", [a, b])
    start(host)
    resume(host, "discard")
    assert not host.state_get("screenshot")


def test_a_draft_saved_by_an_older_version_still_forgets_its_screenshot(tmp_path):
    a = _shot(tmp_path, "a.png")
    host = make_host(PHASE1)
    host.state_set("screenshot", a)
    start(host)
    data = pending(host)
    data.pop("screenshots")
    data.pop("layout")
    data["screenshot"] = a  # the one-screenshot shape older versions stored
    host.state_set("pending", data)
    resume(host, "discard")
    assert not host.state_get("screenshot")


# ------------------------------------------------------------------ check-ins after approve

def checkin_questions(host):
    return questions(host, "yes")


def test_approve_then_yes_schedules_two_check_ins_for_the_pack():
    host = make_host(PHASE1)
    start(host)
    reply = resume(host, "approve", "yes")
    [pack_json] = packs(host)
    name = pack_json.parent.name
    assert len(checkin_questions(host)) == 1
    assert [j["schedule"] for j in host.scheduled] == ["in 24h", "in 72h"]
    assert all(j["name"] == f"Launch Desk check-in: {name}" and name in j["prompt"] for j in host.scheduled)
    assert "job1" in reply and "job2" in reply  # how to cancel names each job
    assert json.loads(pack_json.read_text())["intent_url"] in reply


@pytest.mark.parametrize("answer", ["no", None])
def test_approve_without_a_yes_schedules_nothing(answer):
    host = make_host(PHASE1)
    start(host)
    reply = resume(host, "approve", *([answer] if answer else []))
    assert len(checkin_questions(host)) == 1
    assert host.scheduled == []
    assert packs(host) and "job1" not in reply


def test_a_check_in_is_asked_only_after_the_pack_is_written():
    host = make_host(PHASE1)
    start(host)
    seen = []
    ask = host.choose

    def choose(question, choices):
        if any(c["value"] == "yes" for c in choices):
            seen.append(len(packs(host)))
        return ask(question, choices)
    host.choose = choose
    resume(host, "approve", "no")
    assert seen == [1]


def test_check_in_failures_keep_the_pack_and_never_raise(caplog):
    host = make_host(PHASE1, schedule_replies=[RuntimeError("scheduler down")])
    start(host)
    reply = resume(host, "approve", "yes")
    assert packs(host) and "Traceback" not in reply
    assert json.loads(packs(host)[0].read_text())["intent_url"] in reply


def test_a_refused_check_in_is_reported_and_not_retried():
    host = make_host(PHASE1, schedule_replies=[{"success": False, "error": "nope"},
                                               {"success": False, "error": "nope"}])
    start(host)
    reply = resume(host, "approve", "yes")
    assert len(host.scheduled) == 1 and "job1" not in reply and packs(host)


@pytest.mark.parametrize("kw", [{"unattended": True}, {"surface": "cron"}])
def test_unattended_runs_never_ask_about_check_ins_or_schedule(kw):
    host = make_host(PHASE1, **kw)
    start(host)
    resume(host, "approve", "yes")
    assert host.asked == [] and host.scheduled == []


def test_offer_check_ins_never_asks_when_unattended():
    host = make_host(["yes"], unattended=True)
    assert wizard._offer_checkins(host, "some-pack") == []
    assert host.asked == [] and host.scheduled == []


def test_pack_keeps_the_unused_hooks_and_the_references_not_shown():
    host = make_host(PHASE1)
    start(host)
    shown = set(pending(host)["shown"])
    resume(host, "approve", "no")
    data = json.loads(packs(host)[0].read_text())
    assert data["hooks"] == CLEAN["hooks"]
    more = data["more_references"]
    assert more and all(r["id"] not in shown for r in more)
    assert all(r["shape"] for r in more)


def test_unreadable_search_results_still_approve_without_extra_references():
    host = make_host(PHASE1)
    start(host)
    (Path(pending(host)["dir"]) / "items.json").write_text('{"not": "a list"}')
    reply = resume(host, "approve", "no")
    data = json.loads(packs(host)[0].read_text())
    assert data["more_references"] == [] and data["intent_url"] in reply



@pytest.mark.parametrize("surface", ["cli", "tui", "local", "desktop", "acp"])
def test_cancel_on_a_local_client_uses_the_cron_slash_command(surface):
    host = make_host(PHASE1, surface=surface)
    start(host)
    reply = resume(host, "approve", "yes")
    assert "/cron remove job1" in reply and "/cron remove job2" in reply


@pytest.mark.parametrize("surface", ["telegram", "discord", "slack", "some-new-app"])
def test_cancel_in_a_chat_app_never_offers_the_cli_only_slash_command(surface):
    host = make_host(PHASE1, surface=surface)
    start(host)
    reply = resume(host, "approve", "yes")
    assert "/cron" not in reply
    assert "hermes cron remove" in reply
    assert "job1" in reply and "job2" in reply


def test_a_refused_create_logs_the_raw_reply(caplog):
    caplog.set_level(logging.WARNING)
    host = make_host(PHASE1, schedule_replies=[{"success": False, "detail": "raw-marker-xyz"}])
    start(host)
    resume(host, "approve", "yes")
    assert "raw-marker-xyz" in caplog.text
