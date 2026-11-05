from launch_desk.hooks import HOOK_SHAPES, pick_hook_shapes

KINDS = ("feature", "product", "rebrand", "partnership")


def test_six_shapes_with_matching_ids():
    assert len(HOOK_SHAPES) == 6
    assert all(k == s.id for k, s in HOOK_SHAPES.items())


def test_every_shipped_kind_gets_two_distinct_shapes():
    for kind in KINDS:
        picked = pick_hook_shapes(kind)
        assert len(picked) == 2
        assert picked[0].id != picked[1].id


def test_unknown_kind_falls_back_to_feature():
    assert pick_hook_shapes("nonsense") == pick_hook_shapes("feature")
