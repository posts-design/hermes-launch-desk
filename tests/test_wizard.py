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
from launch_desk.setup_check import ADD_SERVER

FIX = Path(__file__).parent / "fixtures"
URL = "https://pawr.link"
ARGS = f"{URL} custom themes for pet profile pages -- people kept asking to match their own site"
DETAIL = "people kept asking to match their own site"
SEARCH = json.loads((FIX / "search_references.json").read_text(encoding="utf-8"))
PACK_IDS = {e["id"] for e in json.loads(
    (Path(wizard.__file__).parent / "sample_pack.json").read_text(encoding="utf-8"))}

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


def make_host(choices, llm=(CLEAN,), access=None, search=SEARCH, **kw):
    replies = {"get_my_access": access if access is not None else {"allowed": True}}
    if search is not None:
        replies["search_references"] = search
    return FakeHost(choices=list(choices), llm_replies=list(llm), mcp_replies=replies, **kw)


def packs(host):
    return list((host.data_dir() / "packs").glob("*/pack.json"))


def media(reply):
    return [Path(line[len("MEDIA:"):].strip()) for line in reply.splitlines() if line.startswith("MEDIA:")]


def questions(host, choice_value):
    """Questions whose choices include this value."""
    return [q for q, ch in host.asked if any(c["value"] == choice_value for c in ch)]


HAPPY = ["feature", "x", "keep", "1", "approve"]


def tool_names(host):
    return [t for t, _ in host.mcp_calls]


# ------------------------------------------------------------------ stops before work

@pytest.mark.parametrize("kw", [{"unattended": True}, {"surface": "cron"}])
def test_unattended_run_returns_without_asking(kw):
    host = make_host(HAPPY, **kw)
    reply = wizard.run_launch(host, ARGS, get=no_get)
    assert reply.strip()
    assert host.asked == [] and host.mcp_calls == [] and host.llm_calls == []
    assert packs(host) == []


@pytest.mark.parametrize("args", ["", "   ", "help", f"{URL}", f"{URL} -- {DETAIL}", "brand"])
def test_missing_args_returns_usage(args):
    host = make_host(HAPPY)
    reply = wizard.run_launch(host, args, get=no_get)
    assert "/launch" in reply
    assert host.asked == [] and host.mcp_calls == [] and host.llm_calls == []


def test_first_run_without_brand_or_url_stops_with_usage():
    host = make_host(HAPPY)
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


# ------------------------------------------------------------------ the happy path

def test_happy_path_writes_pack_and_returns_media_lines():
    calls = []
    host = make_host(HAPPY)
    reply = wizard.run_launch(host, ARGS, get=fake_get(calls))

    assert calls and calls[0] == URL
    [pack_json] = packs(host)
    data = json.loads(pack_json.read_text())
    assert data["hook"] == CLEAN["hooks"][0]
    assert data["brief"]["what"] == "custom themes for pet profile pages"
    assert data["brief"]["product_url"] == URL
    assert data["brief"]["network"] == "x"
    assert data["intent_url"] in reply
    assert str(pack_json.parent) in reply

    shown = media(reply)
    assert len(shown) == 2
    assert all(p.is_file() and p.parent == pack_json.parent for p in shown)
    assert Image.open(pack_json.parent / data["files"]["card_x"]).size == (1200, 675)
    assert (pack_json.parent / data["files"]["preview"]).is_file()

    # the founder's detail reached the drafter as a fact
    assert DETAIL in host.llm_calls[0][1]
    # the brand was confirmed once and is now stored for next time
    assert len(questions(host, "keep")) == 1
    stored = host.state_get("brand")
    assert stored["url"] == URL and stored["accent"] in data["brand"]["accent"]
    # progress lines were said, and the reply leaks no traceback
    assert len(host.said) >= 3
    assert "Traceback" not in reply
    # the three references were shown in the hook question
    hook_q = questions(host, "1")[0]
    for r in data["references"]:
        assert r["url"] in hook_q and r["brand"] in hook_q


def test_second_run_reuses_stored_brand_silently():
    host = make_host(HAPPY + ["feature", "x", "1", "approve"], llm=(CLEAN, CLEAN))
    wizard.run_launch(host, ARGS, get=fake_get())
    reply = wizard.run_launch(host, "dark mode for pet pages", get=no_get)
    assert len(packs(host)) == 2
    assert len(questions(host, "keep")) == 1  # asked on the first run only
    assert len(media(reply)) == 2


def test_picked_accent_is_used_and_logo_lives_in_brand_dir():
    host = make_host(["feature", "x", PRESET_ACCENTS[2], "1", "approve"])
    wizard.run_launch(host, ARGS, get=fake_get())
    data = json.loads(packs(host)[0].read_text())
    assert data["brand"]["accent"] == PRESET_ACCENTS[2]
    stored = host.state_get("brand")
    assert stored["accent"] == PRESET_ACCENTS[2]
    assert Path(stored["logo_path"]).parent == host.data_dir() / "brand"
    assert Path(stored["logo_path"]).is_file()
    [accent_q] = questions(host, "keep")
    values = [c["value"] for c in host.asked[[q for q, _ in host.asked].index(accent_q)][1]]
    assert set(PRESET_ACCENTS) <= set(values)


def test_brand_subcommand_rereads_and_stores():
    host = make_host([PRESET_ACCENTS[1]])
    reply = wizard.run_launch(host, f"brand {URL}", get=fake_get())
    assert host.state_get("brand")["accent"] == PRESET_ACCENTS[1]
    assert host.state_get("brand")["url"] == URL
    assert host.mcp_calls == [] and host.llm_calls == [] and packs(host) == []
    assert reply.strip()


def test_new_url_rereads_brand():
    host = make_host(HAPPY + ["feature", "x", "keep", "1", "approve"], llm=(CLEAN, CLEAN))
    wizard.run_launch(host, ARGS, get=fake_get())
    calls = []
    wizard.run_launch(host, "https://other.test themes", get=fake_get(calls))
    assert calls and calls[0] == "https://other.test"
    assert host.state_get("brand")["url"] == "https://other.test"


# ------------------------------------------------------------------ stops and failures

@pytest.mark.parametrize("answered", range(len(HAPPY)))
def test_none_choice_stops_and_saves_nothing(answered):
    host = make_host(HAPPY[:answered])
    reply = wizard.run_launch(host, ARGS, get=fake_get())
    assert reply.strip() and "Traceback" not in reply
    assert packs(host) == []
    assert host.state_get("brand") is None
    assert not any(p.is_file() for p in host.data_dir().rglob("*"))


def test_discard_saves_nothing():
    host = make_host(["feature", "x", "keep", "1", "discard"])
    wizard.run_launch(host, ARGS, get=fake_get())
    assert packs(host) == [] and host.state_get("brand") is None
    assert not any(p.is_file() for p in host.data_dir().rglob("*"))


def test_no_question_is_asked_before_the_first_choice_in_oneshot():
    host = make_host([])
    wizard.run_launch(host, ARGS, get=no_get)
    assert host.mcp_calls == [] and len(host.asked) == 1


def test_draft_error_gives_plain_message_and_saves_nothing(caplog):
    host = make_host(HAPPY, llm=("not json", "still not json"))
    with caplog.at_level(logging.ERROR, logger=wizard.__name__):
        reply = wizard.run_launch(host, ARGS, get=fake_get())
    assert "Traceback" not in reply and len(reply.splitlines()) == 1
    assert packs(host) == []
    assert any(r.exc_info for r in caplog.records)


def test_unexpected_exception_never_reaches_the_gateway(caplog):
    host = make_host(HAPPY)

    def boom(question, choices):
        raise KeyError("internal")

    host.choose = boom
    with caplog.at_level(logging.ERROR, logger=wizard.__name__):
        reply = wizard.run_launch(host, ARGS, get=fake_get())
    assert reply.strip() and "KeyError" not in reply and "Traceback" not in reply
    assert any(r.exc_info for r in caplog.records)


def test_mcp_down_uses_sample_pack_and_says_so():
    host = make_host(HAPPY, access=SourceUnavailable("MCP server 'posts' is not connected"))
    reply = wizard.run_launch(host, ARGS, get=fake_get())
    assert "search_references" not in tool_names(host)
    data = json.loads(packs(host)[0].read_text())
    assert {r["id"] for r in data["references"]} <= PACK_IDS
    assert sum(ADD_SERVER in line for line in host.said) == 1
    assert len(media(reply)) == 2


def test_unreachable_tries_live_then_falls_back_to_sample_pack():
    host = make_host(HAPPY, access=SourceUnavailable("transport is down", kind="timeout"),
                     search=SourceUnavailable("timeout", kind="timeout"))
    wizard.run_launch(host, ARGS, get=fake_get())
    assert "search_references" in tool_names(host)
    data = json.loads(packs(host)[0].read_text())
    assert {r["id"] for r in data["references"]} <= PACK_IDS


# ------------------------------------------------------------------ the approve loop

def test_swap_references_reranks_without_repeats():
    host = make_host(["feature", "x", "keep", "1", "swap", "2", "approve"], llm=(CLEAN, OTHER))
    wizard.run_launch(host, ARGS, get=fake_get())
    brief = Brief("feature", "x", "custom themes for pet profile pages", URL)
    items = McpSource(FakeHost(mcp_replies={"search_references": SEARCH})).search(build_search_args(brief))
    first = {r.id for r in rank_references(items, brief)}
    data = json.loads(packs(host)[0].read_text())
    swapped = {r["id"] for r in data["references"]}
    assert swapped and not (swapped & first)
    assert tool_names(host).count("search_references") == 1  # no second search
    assert data["hook"] == OTHER["hooks"][1]
    assert len(host.llm_calls) == 2


def test_change_hook_picks_among_the_same_three():
    host = make_host(["feature", "x", "keep", "1", "hook", "3", "approve"])
    wizard.run_launch(host, ARGS, get=fake_get())
    data = json.loads(packs(host)[0].read_text())
    assert data["hook"] == CLEAN["hooks"][2]
    assert data["caption"].splitlines()[0] == CLEAN["hooks"][2]
    assert len(host.llm_calls) == 1


def test_approve_loop_runs_at_most_three_rounds():
    host = make_host(["feature", "x", "keep", "1"] + ["hook", "2"] * 5 + ["approve"])
    wizard.run_launch(host, ARGS, get=fake_get())
    approve_qs = questions(host, "approve")
    assert len(approve_qs) <= 3
    last = [ch for q, ch in host.asked if any(c["value"] == "approve" for c in ch)][-1]
    assert {c["value"] for c in last} == {"approve", "discard"}


def test_remaining_failures_are_shown_before_approve():
    bad = {"hooks": ["Pet pages — themes — are here.", "Themes.", "Your theme."],
           "caption": "Pet pages — themes — are here.\n\nPick one.\n\nhttps://pawr.link"}
    host = make_host(HAPPY, llm=(bad, bad))
    wizard.run_launch(host, ARGS, get=fake_get())
    [approve_q] = questions(host, "approve")
    assert "The post uses more than one em-dash." in approve_q


def test_linkedin_soon_continues_with_x():
    host = make_host(["feature", "linkedin", "keep", "1", "approve"])
    wizard.run_launch(host, ARGS, get=fake_get())
    assert json.loads(packs(host)[0].read_text())["brief"]["network"] == "x"


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
    host = make_host(HAPPY)
    host.state_set("screenshot", str(shot))
    wizard.run_launch(host, ARGS, get=fake_get())
    assert seen and all((s is not None) == exists for s, _ in seen)
    assert all(h == CLEAN["hooks"][0] for _, h in seen)


def test_long_hook_falls_back_to_what_for_card_headline(monkeypatch):
    long = dict(CLEAN, hooks=["Pet pages can wear your colours now, and so can every link page on it today",
                              *CLEAN["hooks"][1:]])
    seen = []
    real = wizard.render_card

    def spy(brand, screenshot, headline, size, out):
        seen.append(headline)
        return real(brand, screenshot, headline, size, out)

    monkeypatch.setattr(wizard, "render_card", spy)
    host = make_host(HAPPY, llm=(long, long))
    wizard.run_launch(host, ARGS + " " + "and more words " * 10, get=fake_get())
    assert seen and all(len(h) <= 70 for h in seen)
    assert seen[0].startswith("custom themes for pet profile pages")


def test_unreadable_site_offers_the_five_presets_and_continues():
    def down(url):
        raise TimeoutError("slow")

    host = make_host(["feature", "x", PRESET_ACCENTS[4], "1", "approve"])
    reply = wizard.run_launch(host, ARGS, get=down)
    accent_q = [ch for q, ch in host.asked if any(c["value"] == PRESET_ACCENTS[0] for c in ch)]
    assert [c["value"] for c in accent_q[0]] == list(PRESET_ACCENTS)
    assert json.loads(packs(host)[0].read_text())["brand"]["accent"] == PRESET_ACCENTS[4]
    assert len(media(reply)) == 2


def test_alt_text_names_the_headline_on_the_card():
    host = make_host(["feature", "x", "keep", "2", "approve"])
    wizard.run_launch(host, ARGS, get=fake_get())
    pack = packs(host)[0].parent
    assert CLEAN["hooks"][1] in (pack / "alt-text.txt").read_text()
