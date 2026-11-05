import io
import json
import logging
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
    "hooks": ["Pet pages can wear your colours now.",
              "Check this out: your pet's page in your colours.",
              "Your pet's page, your theme."],
    "caption": "Pet pages can wear your colours now.\n\nPick a theme in settings and the whole page follows."
               "\n\nhttps://pawr.link",
}
OTHER = {
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


def test_remaining_failures_are_shown_in_the_phase1_reply():
    bad = {"hooks": ["Pet pages — themes — are here.", "Themes.", "Your theme."],
           "caption": "Pet pages — themes — are here.\n\nPick one.\n\nhttps://pawr.link"}
    host = make_host(PHASE1, llm=(bad, bad))
    reply = start(host)
    flagged = [line for line in reply.splitlines() if EM_DASH_MESSAGE in line]
    assert len(flagged) == 1


def test_linkedin_soon_is_said_in_the_reply_and_continues_with_x():
    x_host = make_host(PHASE1)
    li_host = make_host(["feature", "linkedin", "keep", "1"])
    x_reply, li_reply = start(x_host), start(li_host)
    assert pending(li_host)["brief"]["network"] == "x"
    plain = lambda r: [line for line in r.splitlines() if not line.startswith("MEDIA:")]
    extra = [line for line in plain(li_reply) if line not in plain(x_reply)]
    assert len(extra) == 1 and extra[0] in li_host.said


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

    def spy(brand, screenshot, headline, size, out):
        seen.append((screenshot, headline))
        return real(brand, screenshot, headline, size, out)

    monkeypatch.setattr(wizard, "render_card", spy)
    host = make_host(PHASE1)
    host.state_set("screenshot", str(shot))
    start(host)
    assert len(seen) == 1
    assert (seen[0][0] is not None) == exists and seen[0][1] == CLEAN["hooks"][0]


def test_long_hook_falls_back_to_what_for_card_headline(monkeypatch):
    long = dict(CLEAN, hooks=["Pet pages can wear your colours now, and so can every link page on it today",
                              *CLEAN["hooks"][1:]])
    seen = []
    real = wizard.render_card

    def spy(brand, screenshot, headline, size, out):
        seen.append(headline)
        return real(brand, screenshot, headline, size, out)

    monkeypatch.setattr(wizard, "render_card", spy)
    start(make_host(PHASE1, llm=(long, long)))
    assert seen == ["custom themes for pet profile pages"]


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
