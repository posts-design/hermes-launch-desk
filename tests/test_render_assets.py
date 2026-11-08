"""The asset tool's sample posts are live runs; they must keep passing every check."""
import runpy
from pathlib import Path

import pytest

TOOL = runpy.run_path(str(Path(__file__).resolve().parent.parent / "tools" / "render_assets.py"),
                      run_name="render_assets")


@pytest.mark.parametrize("kit,brief,post,card", [
    ("posts_kit", "POSTS_BRIEF", "POSTS_POST", "POSTS_CARD"),
    ("brookhour_kit", "BROOKHOUR_BRIEF", "BROOKHOUR_POST", "BROOKHOUR_CARD"),
    ("fernlight_kit", "FERNLIGHT_BRIEF", "FERNLIGHT_POST", "FERNLIGHT_CARD"),
])
def test_every_sample_post_and_card_line_passes(tmp_path, kit, brief, post, card):
    rows = TOOL["scroll_test"](TOOL[kit](tmp_path), TOOL[brief], TOOL[post], TOOL[card])
    assert len(rows) == 4
