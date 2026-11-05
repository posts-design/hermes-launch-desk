from pathlib import Path

import pytest
from PIL import Image

from launch_desk.models import BrandKit
from launch_desk.render import (
    FONT_PATH,
    fit_text,
    load_font,
    render_card,
    render_feed_preview,
)

KIT = BrandKit(
    name="pawr.link",
    url="https://pawr.link",
    tagline="One pawrful page for everything you make.",
    bg="#FAFAFA",
    ink="#111111",
    accent="#6A3CFF",
    font_family="Inter",
    logo_path=None,
)
HEADLINE_60 = "Custom themes are live for every page you build on pawr link"


def _rgb(hex_: str) -> tuple[int, int, int]:
    h = hex_.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _shot(tmp_path: Path, size=(1600, 900), name="shot.png") -> Path:
    p = tmp_path / name
    Image.new("RGB", size, (40, 120, 200)).save(p)
    return p


@pytest.mark.parametrize("size", [(1200, 675), (1080, 1350)])
def test_card_has_exact_size(tmp_path, size):
    out = render_card(KIT, _shot(tmp_path), "Custom themes are live.", size, tmp_path / "card.png")
    with Image.open(out) as im:
        assert im.size == size


@pytest.mark.parametrize("size", [(1200, 675), (1080, 1350)])
def test_card_without_screenshot_renders(tmp_path, size):
    out = render_card(KIT, None, "Custom themes are live.", size, tmp_path / "card.png")
    with Image.open(out) as im:
        assert im.size == size


@pytest.mark.parametrize("shot_size", [(1170, 2532), (300, 200), (900, 900)])
def test_card_accepts_any_screenshot_shape(tmp_path, shot_size):
    out = render_card(KIT, _shot(tmp_path, shot_size), "Ship it", (1200, 675), tmp_path / "c.png")
    with Image.open(out) as im:
        assert im.size == (1200, 675)


def test_undecodable_screenshot_and_logo_fall_back(tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    kit = BrandKit(**{**KIT.__dict__, "logo_path": str(bad)})
    out = render_card(kit, bad, "Ship it", (1200, 675), tmp_path / "c.png")
    with Image.open(out) as im:
        assert im.size == (1200, 675)


def test_ico_logo_renders(tmp_path):
    ico = tmp_path / "favicon.ico"
    Image.new("RGBA", (48, 48), (29, 155, 240, 255)).save(ico, sizes=[(16, 16), (48, 48)])
    kit = BrandKit(**{**KIT.__dict__, "logo_path": str(ico)})
    out = render_card(kit, None, "Ship it", (1200, 675), tmp_path / "c.png")
    assert out.exists()


def test_long_headline_fits_box():
    box = (936, 230)
    lines, size = fit_text(HEADLINE_60, str(FONT_PATH), box, 64, 36, 700)
    assert 1 <= len(lines) <= 3
    assert 36 <= size <= 64
    font = load_font(str(FONT_PATH), size, 700)
    for line in lines:
        assert font.getlength(line) <= box[0]
    assert " ".join(lines) == HEADLINE_60


def test_text_too_long_at_min_size_is_truncated_not_overflowing():
    box = (300, 100)
    text = "word " * 200
    lines, size = fit_text(text, str(FONT_PATH), box, 64, 36, 700)
    assert size == 36
    assert 1 <= len(lines) <= 3
    assert lines[-1].endswith("…")
    font = load_font(str(FONT_PATH), size, 700)
    assert all(font.getlength(line) <= box[0] for line in lines)


@pytest.mark.parametrize("sizes", [(36, 64), (40, 40), (1, 1), (200, 1)])
def test_fit_text_terminates_on_any_size_range(sizes):
    lines, size = fit_text("Unbreakablesupercalifragilisticword and more", str(FONT_PATH), (120, 60), *sizes, 400)
    assert size >= 1
    font = load_font(str(FONT_PATH), size, 400)
    assert all(font.getlength(line) <= 120 for line in lines)


def test_preview_is_390_wide(tmp_path):
    card = render_card(KIT, _shot(tmp_path), "Custom themes are live.", (1200, 675), tmp_path / "card.png")
    out = render_feed_preview(KIT, "Custom themes are live.\n\nPick a look.", card, tmp_path / "p.png")
    with Image.open(out) as im:
        assert im.size[0] == 390
        assert im.size[1] > 390


def test_card_uses_brand_background_at_corner_pixel(tmp_path):
    for kit in (KIT, BrandKit(**{**KIT.__dict__, "bg": "#0B0B10", "ink": "#F5F5F5"})):
        for shot in (_shot(tmp_path), None):
            out = render_card(kit, shot, "Custom themes are live.", (1200, 675), tmp_path / "c.png")
            with Image.open(out) as im:
                assert im.convert("RGB").getpixel((2, 2)) == _rgb(kit.bg)


def test_characters_the_font_cannot_draw_are_dropped():
    from launch_desk.render import _drop_missing

    assert _drop_missing("Shipped \U0001F680 today") == "Shipped today"
    assert _drop_missing("Café — “new”") == "Café — “new”"


def _round_mark(fill=(255, 255, 255, 255), size=200):
    from PIL import ImageDraw
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse((40, 40, size - 40, size - 40), fill=fill)
    return im


def _white_mark():
    return _round_mark()


def _pixels(im):
    raw = im.convert("RGBA").tobytes()
    return [tuple(raw[i:i + 4]) for i in range(0, len(raw), 4)]


def _inside_colours(tile):
    """Distinct colours of the tile's opaque pixels."""
    return {px[:3] for px in _pixels(tile) if px[3] == 255}


@pytest.mark.parametrize("bg,ink", [("#FAFAFA", "#111111"), ("#0B0B10", "#F5F5F5")])
def test_white_on_transparent_logo_stays_visible(bg, ink):
    from launch_desk.render import _mark_tile

    kit = BrandKit(**{**KIT.__dict__, "bg": bg, "ink": ink})
    tile = _mark_tile(kit, _white_mark(), 120, _rgb(kit.accent))
    colours = _inside_colours(tile)
    assert len(colours) > 1
    assert (255, 255, 255) in colours


def test_white_logo_plate_is_dark_bg_or_accent():
    from launch_desk.render import _mark_tile

    dark = BrandKit(**{**KIT.__dict__, "bg": "#0B0B10", "ink": "#F5F5F5"})
    assert _rgb(dark.bg) in _inside_colours(_mark_tile(dark, _white_mark(), 120, _rgb(dark.accent)))
    assert _rgb(KIT.accent) in _inside_colours(_mark_tile(KIT, _white_mark(), 120, _rgb(KIT.accent)))


def test_fully_transparent_logo_falls_back_to_the_initial_tile():
    from launch_desk.render import _mark_tile

    empty = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    tile = _mark_tile(KIT, empty, 120, _rgb(KIT.accent))
    initial = _mark_tile(KIT, None, 120, _rgb(KIT.accent))
    assert _pixels(tile) == _pixels(initial)


def test_dark_logo_keeps_the_white_plate():
    from launch_desk.render import _mark_tile

    im = _round_mark((20, 20, 20, 255))
    assert (255, 255, 255) in _inside_colours(_mark_tile(KIT, im, 120, _rgb(KIT.accent)))
