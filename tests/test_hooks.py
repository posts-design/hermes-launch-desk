from dataclasses import fields

import pytest

from launch_desk.hooks import HOOK_SHAPES, pick_hook_shapes
from launch_desk.models import HookShape

KINDS = ("feature", "product", "rebrand", "partnership")
ASKED = "people kept asking to match their pet's page to their own site"


def test_eight_shapes_with_matching_ids_and_no_extra_fields():
    assert len(HOOK_SHAPES) == 8
    assert all(k == s.id for k, s in HOOK_SHAPES.items())
    assert [f.name for f in fields(HookShape)] == ["id", "title", "when", "shape"]
    assert all(s.title and s.when and s.shape for s in HOOK_SHAPES.values())
    assert {"reader-thought", "you-asked"} <= set(HOOK_SHAPES)


def test_new_shapes_are_fill_in_shapes():
    for key in ("reader-thought", "you-asked"):
        assert "[" in HOOK_SHAPES[key].shape and "]" in HOOK_SHAPES[key].shape


@pytest.mark.parametrize("detail", ["", ASKED])
def test_every_shipped_kind_gets_two_distinct_shapes(detail):
    for kind in KINDS:
        picked = pick_hook_shapes(kind, detail)
        assert len(picked) == 2
        assert picked[0].id != picked[1].id


@pytest.mark.parametrize("detail", ["", ASKED])
def test_unknown_kind_falls_back_to_feature(detail):
    assert pick_hook_shapes("nonsense", detail) == pick_hook_shapes("feature", detail)


def test_feature_without_detail_uses_reader_thought_and_check_this_out():
    assert [s.id for s in pick_hook_shapes("feature")] == ["reader-thought", "check-this-out"]


@pytest.mark.parametrize("detail", [ASKED, "Users ASKED for it", "you can ask now", "a top request",
                                    "it was requested twice", "everyone Wanted this"])
def test_feature_with_an_asking_detail_uses_you_asked(detail):
    assert [s.id for s in pick_hook_shapes("feature", detail)] == ["reader-thought", "you-asked"]


@pytest.mark.parametrize("detail", ["built it over a weekend", "a task runner", "unrequested polish", "I want it"])
def test_feature_with_another_detail_keeps_check_this_out(detail):
    assert [s.id for s in pick_hook_shapes("feature", detail)] == ["reader-thought", "check-this-out"]


@pytest.mark.parametrize("kind,ids", [("product", ["identity-news", "bold-contrast"]),
                                      ("rebrand", ["time-jump", "bold-contrast"]),
                                      ("partnership", ["identity-news", "audience-call-out"])])
def test_other_kinds_are_unchanged_by_the_detail(kind, ids):
    assert [s.id for s in pick_hook_shapes(kind)] == ids
    assert [s.id for s in pick_hook_shapes(kind, ASKED)] == ids
