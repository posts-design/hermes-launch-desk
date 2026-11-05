import json
import os
from pathlib import Path

import pytest

from launch_desk import followup
from launch_desk.pack import JUDGE_BY_CONVERSION
from launch_desk.rules import check

MESSAGE_RULES = {"em-dash", "emoji", "placeholder", "stock-phrase"}

PACK = "2026-11-05-pawr-link-custom-themes"
POST = "Pet pages can wear your colours now.\n\nPick a theme.\n\nhttps://pawr.link"
DETAIL = "people kept asking to match their own site"


def make_pack(root: Path, name=PACK, detail=DETAIL, **extra) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    data = {
        "brief": {"shipped": "feature", "network": "x", "what": "custom themes", "detail": detail,
                  "product_url": "https://pawr.link"},
        "brand": {"name": "Pawr", "url": "https://pawr.link"},
        "hook": "Pet pages can wear your colours now.",
        "hooks": ["Pet pages can wear your colours now.", "You asked for themes. Here they are.",
                  "Your pet's page, your theme."],
        "caption": POST,
        "references": [{"id": "r1", "brand": "Acme", "url": "https://x.com/a/1", "views": 900}],
        "more_references": [
            {"id": "r4", "brand": "Linear", "url": "https://x.com/l/4", "views": 5000,
             "shape": "opens with a question, 3 short lines"},
            {"id": "r5", "brand": "Raycast", "url": "https://x.com/r/5", "views": None,
             "shape": "opens with what changed, 2 short lines, a list"},
        ],
        "intent_url": "https://x.com/intent/post?text=abc",
        "files": {"post": "post-x.txt", "pack": "pack.json"},
    }
    data.update(extra)
    (folder / "pack.json").write_text(json.dumps(data), encoding="utf-8")
    return folder


def read(root: Path, name=PACK) -> dict:
    return json.loads((root / name / "pack.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ the scheduled jobs

def test_two_job_specs_one_day_and_three_days_out():
    specs = followup.job_specs(PACK)
    assert [s["schedule"] for s in specs] == ["in 24h", "in 72h"]
    for spec in specs:
        assert spec["action"] == "create"
        assert spec["name"] == f"Launch Desk check-in: {PACK}"
        assert "launch_desk_followup" in spec["prompt"] and PACK in spec["prompt"]
        assert followup.SKILL in spec["skills"]
        # No deliver target: Hermes then posts to the chat the job was created from
        # (or keeps it local when there is none). Never another chat.
        assert "deliver" not in spec
        assert spec["attach_to_session"] is True
        assert spec["enabled_toolsets"] == ["launch_desk"]


def test_job_prompt_is_fixed_apart_from_the_pack_name():
    a, b = followup.job_specs("pack-a")[0]["prompt"], followup.job_specs("pack-b")[0]["prompt"]
    assert a.replace("pack-a", "X") == b.replace("pack-b", "X")


# ------------------------------------------------------------------ launch_desk_followup

def test_followup_returns_the_pack_and_suggestions_from_its_own_data(tmp_path):
    make_pack(tmp_path)
    out = followup.followup(tmp_path, PACK)
    data = read(tmp_path)
    assert out["ok"] is True and out["pack"] == PACK
    assert out["post"] == POST and out["intent_url"] == data["intent_url"]
    assert out["unused_hooks"] == data["hooks"][1:]
    assert out["more_references"] == data["more_references"]
    assert out["after_post_check"] == {"id": "judge-by-conversion", "text": JUDGE_BY_CONVERSION}
    assert out["results"] == []

    kinds = [s["kind"] for s in out["suggestions"]]
    assert kinds == ["reply-with-detail", "unused-hook", "reference-shape"]
    by_kind = {s["kind"]: s for s in out["suggestions"]}
    assert by_kind["reply-with-detail"]["detail"] == DETAIL
    assert by_kind["unused-hook"]["hook"] in data["hooks"][1:]
    ref = by_kind["reference-shape"]
    assert {k: ref[k] for k in ("brand", "url", "shape")} in [
        {k: r[k] for k in ("brand", "url", "shape")} for r in data["more_references"]]


def test_suggestions_never_carry_fields_the_pack_does_not_have(tmp_path):
    make_pack(tmp_path)
    allowed = {"reply-with-detail": {"kind", "detail"}, "unused-hook": {"kind", "hook"},
               "reference-shape": {"kind", "brand", "url", "shape", "views"}}
    for s in followup.followup(tmp_path, PACK)["suggestions"]:
        assert set(s) <= allowed[s["kind"]]


def test_suggestions_skip_what_the_pack_lacks(tmp_path):
    make_pack(tmp_path, detail="", more_references=[])
    out = followup.followup(tmp_path, PACK)
    assert [s["kind"] for s in out["suggestions"]] == ["unused-hook"]
    assert len(out["suggestions"]) <= 3


def test_followup_reads_a_pack_written_before_check_ins(tmp_path):
    folder = make_pack(tmp_path)
    data = read(tmp_path)
    for key in ("hooks", "more_references"):
        data.pop(key)
    (folder / "pack.json").write_text(json.dumps(data), encoding="utf-8")
    out = followup.followup(tmp_path, PACK)
    assert out["ok"] is True
    assert out["unused_hooks"] == [] and out["more_references"] == []
    assert [s["kind"] for s in out["suggestions"]] == ["reply-with-detail"]


@pytest.mark.parametrize("name", ["../outside", "..", ".", "/etc", "a/b", "", "x" * 300, "nope",
                                  PACK + "/../" + PACK, "~/x", None, 5])
def test_followup_refuses_names_outside_the_packs_folder(tmp_path, name):
    root = tmp_path / "packs"
    make_pack(root)
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "pack.json").write_text("{}", encoding="utf-8")
    out = followup.followup(root, name)
    assert out["ok"] is False and out.get("error")


def test_followup_refuses_a_symlink_that_leaves_the_packs_folder(tmp_path):
    root = tmp_path / "packs"
    make_pack(root)
    other = make_pack(tmp_path / "elsewhere", name="secret")
    os.symlink(other, root / "linked")
    assert followup.followup(root, "linked")["ok"] is False


def test_followup_on_an_unreadable_pack_is_an_error_not_a_raise(tmp_path):
    folder = tmp_path / PACK
    folder.mkdir()
    (folder / "pack.json").write_text("not json", encoding="utf-8")
    assert followup.followup(tmp_path, PACK)["ok"] is False


# ------------------------------------------------------------------ launch_desk_record_results

def test_record_results_appends_to_the_pack(tmp_path):
    make_pack(tmp_path)
    first = followup.record_results(tmp_path, PACK, "X", 12)
    second = followup.record_results(tmp_path, PACK, "  Hacker   News ", 0)
    assert first["ok"] is True and second["ok"] is True
    results = read(tmp_path)["results"]
    assert [(r["channel"], r["signups"]) for r in results] == [("X", 12), ("Hacker News", 0)]
    assert all(r["recorded_at"] for r in results)
    assert second["results"] == results
    assert followup.followup(tmp_path, PACK)["results"] == results
    assert read(tmp_path)["hook"] == "Pet pages can wear your colours now."  # the rest is kept


@pytest.mark.parametrize("channel,signups", [
    ("", 3), ("   ", 3), ("x" * 41, 3), ("X\nY", 3), (None, 3), (7, 3),
    ("X", -1), ("X", 1_000_001), ("X", 2.5), ("X", True), ("X", "lots"), ("X", None),
])
def test_record_results_rejects_bad_input_and_changes_nothing(tmp_path, channel, signups):
    make_pack(tmp_path)
    before = (tmp_path / PACK / "pack.json").read_bytes()
    out = followup.record_results(tmp_path, PACK, channel, signups)
    assert out["ok"] is False and out.get("error")
    assert (tmp_path / PACK / "pack.json").read_bytes() == before


def test_record_results_accepts_the_limits_and_a_number_as_text(tmp_path):
    make_pack(tmp_path)
    assert followup.record_results(tmp_path, PACK, "x" * 40, 1_000_000)["ok"] is True
    assert followup.record_results(tmp_path, PACK, "LinkedIn", "7")["ok"] is True
    assert [r["signups"] for r in read(tmp_path)["results"]] == [1_000_000, 7]


def test_record_results_refuses_traversal(tmp_path):
    root = tmp_path / "packs"
    make_pack(root)
    make_pack(tmp_path, name="outside")
    out = followup.record_results(root, "../outside", "X", 1)
    assert out["ok"] is False
    assert "results" not in read(tmp_path, "outside")


# ------------------------------------------------------------------ the message, composed in code

def message_failures(text):
    return [f.rule_id for f in check(text, network="x") if f.rule_id in MESSAGE_RULES]


PACK_VARIANTS = [
    {},
    {"detail": ""},
    {"more_references": []},
    {"detail": "", "more_references": [], "hooks": ["Pet pages can wear your colours now."]},
    {"detail": "people asked \u2014 a lot \U0001F436 [link]"},
    {"hooks": ["Pet pages can wear your colours now.", "Your page \u2014 your theme \U0001F3A8\U0001F3A8",
               "Excited to announce themes \u2014 a game-changer {name}"]},
    {"more_references": [{"id": "r", "brand": "beehiiv \U0001F41D\U0001F41D", "url": "https://x.com/b/1",
                          "views": 4, "shape": "opens with a number \u2014 4 lines, {list}"}]},
    {"detail": "TODO", "hooks": [], "more_references": []},
]


@pytest.mark.parametrize("extra", PACK_VARIANTS)
def test_message_passes_the_copy_checks_for_any_pack(tmp_path, extra):
    make_pack(tmp_path, **extra)
    out = followup.followup(tmp_path, PACK)
    assert out["ok"] is True
    assert PACK in out["message"]
    assert message_failures(out["message"]) == []


def test_message_carries_the_first_suggestions_from_the_pack(tmp_path):
    make_pack(tmp_path)
    out = followup.followup(tmp_path, PACK)
    assert DETAIL in out["message"]
    assert out["unused_hooks"][0] in out["message"]


def test_message_without_suggestions_still_asks(tmp_path):
    make_pack(tmp_path, detail="", hooks=[], more_references=[])
    out = followup.followup(tmp_path, PACK)
    assert out["suggestions"] == [] and PACK in out["message"]


@pytest.mark.parametrize("name", [PACK, "../outside", None, "nope"])
def test_a_refusal_also_has_a_ready_message(tmp_path, name):
    out = followup.followup(tmp_path, name)  # no packs at all
    assert out["ok"] is False
    assert out["message"].strip() and "\n" not in out["message"].strip()
    assert message_failures(out["message"]) == []
    if name == PACK:
        assert PACK in out["message"]
    if name == "../outside":
        assert "../outside" not in out["message"]


# ------------------------------------------------------------------ minors

def test_a_pack_name_with_a_trailing_newline_is_refused(tmp_path):
    make_pack(tmp_path)
    assert followup.followup(tmp_path, PACK + "\n")["ok"] is False
    assert followup.record_results(tmp_path, PACK + "\n", "X", 1)["ok"] is False


def test_a_pack_json_that_is_a_symlink_is_refused(tmp_path):
    root = tmp_path / "packs"
    make_pack(tmp_path / "elsewhere", name="real")
    folder = root / PACK
    folder.mkdir(parents=True)
    os.symlink(tmp_path / "elsewhere" / "real" / "pack.json", folder / "pack.json")
    assert followup.followup(root, PACK)["ok"] is False
    assert followup.record_results(root, PACK, "X", 1)["ok"] is False
    assert "results" not in read(tmp_path / "elsewhere", "real")


@pytest.mark.parametrize("signups", ["\uff11\uff12", "12345678", "9" * 5000, "1 2", "+5", "-0", "١٢"])
def test_signups_as_text_must_be_up_to_seven_ascii_digits(tmp_path, signups):
    make_pack(tmp_path)
    out = followup.record_results(tmp_path, PACK, "X", signups)
    assert out["ok"] is False and "results" not in read(tmp_path)


def test_seven_ascii_digits_are_accepted(tmp_path):
    make_pack(tmp_path)
    assert followup.record_results(tmp_path, PACK, "X", "0001234")["ok"] is True
    assert read(tmp_path)["results"][0]["signups"] == 1234


def test_results_keep_the_last_fifty(tmp_path):
    make_pack(tmp_path)
    for n in range(55):
        followup.record_results(tmp_path, PACK, "X", n)
    results = read(tmp_path)["results"]
    assert len(results) == 50
    assert [r["signups"] for r in results] == list(range(5, 55))
