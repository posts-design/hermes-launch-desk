from dataclasses import fields

import pytest

from launch_desk.hooks import HOOK_SHAPES, pick_hook_shapes
from launch_desk.models import HookShape

KINDS = ("feature", "product", "rebrand", "partnership")


def test_five_shapes_with_matching_ids_and_no_extra_fields():
    assert len(HOOK_SHAPES) == 5
    assert all(k == s.id for k, s in HOOK_SHAPES.items())
    assert [f.name for f in fields(HookShape)] == ["id", "title", "when", "shape"]
    assert all(s.title and s.when and s.shape for s in HOOK_SHAPES.values())
    assert "you-asked" not in HOOK_SHAPES  # the founder's words lead whenever there is a detail (R47)


def test_the_template_shapes_are_gone():
    assert not {"reader-thought", "check-this-out"} & set(HOOK_SHAPES)
    for kind in KINDS:
        assert not {"reader-thought", "check-this-out"} & {s.id for s in pick_hook_shapes(kind)}


def test_no_shape_is_a_template_or_stock_opener():
    from launch_desk.rules import check
    for s in HOOK_SHAPES.values():
        ids = {f.rule_id for f in check(s.shape, network="x")}
        assert not {"template-opener", "stock-phrase"} & ids


def test_every_shipped_kind_gets_two_distinct_shapes():
    for kind in KINDS:
        picked = pick_hook_shapes(kind)
        assert len(picked) == 2
        assert picked[0].id != picked[1].id


def test_unknown_kind_falls_back_to_feature():
    assert pick_hook_shapes("nonsense") == pick_hook_shapes("feature")


def test_the_detail_no_longer_picks_shapes():
    import inspect
    assert list(inspect.signature(pick_hook_shapes).parameters) == ["shipped"]


@pytest.mark.parametrize("kind,ids", [("feature", ["bold-contrast", "audience-call-out"]),
                                      ("product", ["identity-news", "bold-contrast"]),
                                      ("rebrand", ["time-jump", "bold-contrast"]),
                                      ("partnership", ["identity-news", "audience-call-out"])])
def test_each_kind_gets_its_pair(kind, ids):
    assert [s.id for s in pick_hook_shapes(kind)] == ids
