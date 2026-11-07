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


def test_preview_scale_multiplies_both_sides(tmp_path):
    card = render_card(KIT, _shot(tmp_path), "Custom themes are live.", (1200, 675), tmp_path / "card.png")
    caption = "Custom themes are live.\n\nPick a look."
    one = render_feed_preview(KIT, caption, card, tmp_path / "p1.png")
    two = render_feed_preview(KIT, caption, card, tmp_path / "p2.png", scale=2)
    with Image.open(one) as a, Image.open(two) as b:
        assert b.size == (a.size[0] * 2, a.size[1] * 2)


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


def test_light_grey_logo_gets_the_dark_plate():
    from launch_desk.render import _mark_tile

    dark = BrandKit(**{**KIT.__dict__, "bg": "#0B0B10", "ink": "#F5F5F5"})
    tile = _mark_tile(dark, _round_mark((0xD0, 0xD0, 0xD0, 255)), 120, _rgb(dark.accent))
    colours = _inside_colours(tile)
    assert _rgb(dark.bg) in colours and (255, 255, 255) not in colours


# ------------------------------------------------------------------ layouts

DARK = BrandKit(**{**KIT.__dict__, "bg": "#0B0B10", "ink": "#F5F5F5"})
SIZES = [(1200, 675), (1080, 1350)]


def _solid(tmp_path: Path, size, colour, name) -> Path:
    p = tmp_path / name
    Image.new("RGB", size, colour).save(p)
    return p


def test_choose_layout_by_usable_screenshots(tmp_path):
    from launch_desk.render import choose_layout

    a, b, c = (_shot(tmp_path, name=n) for n in ("a.png", "b.png", "c.png"))
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    long_hook = "Custom themes are live for every page you build on pawr link"
    short_hook = "Themes are live."
    assert choose_layout([a, b], long_hook) == "split"
    assert choose_layout([a, b], short_hook) == "split"
    assert choose_layout([a, b, c], short_hook) == "split"
    assert choose_layout([a], long_hook) == "hero"
    assert choose_layout([a], short_hook) == "hero"  # the founder's screenshot always shows
    assert choose_layout([bad, a], short_hook) == "hero"
    assert choose_layout([], short_hook) == "statement"
    assert choose_layout([], long_hook) == "statement"
    assert choose_layout([bad, tmp_path / "missing.png"], long_hook) == "statement"


def test_choose_layout_is_deterministic(tmp_path):
    from launch_desk.render import choose_layout

    shots = [_shot(tmp_path, name="a.png"), _shot(tmp_path, name="b.png")]
    assert len({choose_layout(shots[:n], "Ship it") for n in (0, 1, 2) for _ in range(3)}) == 3


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("layout", ["hero", "statement", "split"])
def test_every_layout_has_exact_size(tmp_path, size, layout):
    shots = [_shot(tmp_path, name="a.png"), _shot(tmp_path, (1170, 2532), name="b.png")]
    out = render_card(KIT, shots, HEADLINE_60, size, tmp_path / "c.png", layout=layout)
    with Image.open(out) as im:
        assert im.size == size


@pytest.mark.parametrize("layout", ["hero", "statement", "split"])
@pytest.mark.parametrize("kit", [KIT, DARK])
def test_every_layout_uses_brand_background_at_corner_pixel(tmp_path, layout, kit):
    shots = [_shot(tmp_path, name="a.png"), _shot(tmp_path, (900, 1600), name="b.png")]
    for size in SIZES:
        out = render_card(kit, shots, "Custom themes are live.", size, tmp_path / "c.png", layout=layout)
        with Image.open(out) as im:
            assert im.convert("RGB").getpixel((2, 2)) == _rgb(kit.bg)


def _bbox_of(im: Image.Image, colour) -> tuple[int, int, int, int] | None:
    """Bounding box of the pixels within a few levels of colour."""
    r, g, b = (im.getchannel(i).point(lambda v, c=c: 255 if abs(v - c) <= 6 else 0)
               for i, c in enumerate(colour))
    from PIL import ImageChops

    return ImageChops.multiply(ImageChops.multiply(r, g), b).getbbox()


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("shapes", [((2400, 900), (900, 1950)), ((3000, 400), (500, 2600)),
                                    ((800, 800), (1920, 1080))])
def test_split_with_mismatched_shapes_stays_inside_and_aligned(tmp_path, size, shapes):
    red, green = (230, 20, 20), (20, 200, 60)
    left = _solid(tmp_path, shapes[0], red, "left.png")
    right = _solid(tmp_path, shapes[1], green, "right.png")
    out = render_card(KIT, [left, right], HEADLINE_60, size, tmp_path / "c.png", layout="split")
    with Image.open(out) as im:
        im = im.convert("RGB")
        a, b = _bbox_of(im, red), _bbox_of(im, green)
    assert a and b
    W, H = size
    for box in (a, b):
        assert box[0] >= 16 and box[1] >= 16 and box[2] <= W - 16 and box[3] <= H - 16
    side_by_side = a[2] <= b[0]
    stacked = a[3] <= b[1]
    assert side_by_side or stacked  # never overlapping
    if side_by_side:
        assert abs((a[3] - a[1]) - (b[3] - b[1])) <= 2 and abs(a[1] - b[1]) <= 2
    else:
        assert abs((a[2] - a[0]) - (b[2] - b[0])) <= 2 and abs(a[0] - b[0]) <= 2


@pytest.mark.parametrize("size", SIZES)
def test_split_keeps_the_newest_two_in_attach_order(tmp_path, size):
    colours = [(230, 20, 20), (20, 200, 60), (30, 60, 230)]
    shots = [_solid(tmp_path, (1600, 900), c, f"{i}.png") for i, c in enumerate(colours)]
    out = render_card(KIT, shots, "Ship it", size, tmp_path / "c.png", layout="split")
    with Image.open(out) as im:
        im = im.convert("RGB")
        old, mid, new = (_bbox_of(im, c) for c in colours)
    assert old is None and mid and new
    assert mid[0] < new[0] or mid[1] < new[1]


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("kit", [KIT, DARK])
def test_statement_sets_a_60_character_hook_whole_inside_the_margins(tmp_path, size, kit):
    from launch_desk.render import MARGIN, _Card, _statement_text

    assert len(HEADLINE_60) == 60
    lines, font_size = _statement_text(_Card(kit, size, None), HEADLINE_60)
    assert " ".join(lines) == HEADLINE_60  # nothing cut, no ellipsis
    out = render_card(kit, [], HEADLINE_60, size, tmp_path / "c.png", layout="statement")
    with Image.open(out) as im:
        im = im.convert("RGB")
        ink = _bbox_of(im, _rgb(kit.ink))
    W, H = size
    edge = MARGIN // 2
    assert ink and ink[0] >= edge and ink[1] >= edge and ink[2] <= W - edge and ink[3] <= H - edge


def test_a_forced_layout_without_the_screenshots_it_needs_falls_back(tmp_path):
    one = [_solid(tmp_path, (1600, 900), (230, 20, 20), "one.png")]
    split_one = render_card(KIT, one, "Ship it", (1200, 675), tmp_path / "a.png", layout="split")
    hero_none = render_card(KIT, [], "Ship it", (1200, 675), tmp_path / "b.png", layout="hero")
    with Image.open(split_one) as a:
        assert _bbox_of(a.convert("RGB"), (230, 20, 20))
    with Image.open(hero_none) as b:
        assert b.size == (1200, 675)


def test_statement_leaves_a_screenshot_out(tmp_path):
    shot = [_solid(tmp_path, (1600, 900), (230, 20, 20), "one.png")]
    out = render_card(KIT, shot, "Ship it", (1200, 675), tmp_path / "c.png", layout="statement")
    with Image.open(out) as im:
        assert _bbox_of(im.convert("RGB"), (230, 20, 20)) is None


@pytest.mark.parametrize("size", SIZES)
def test_split_labels_are_optional_and_stay_inside(tmp_path, size):
    red, green = (230, 20, 20), (20, 200, 60)
    shots = [_solid(tmp_path, (1600, 900), red, "a.png"), _solid(tmp_path, (900, 1600), green, "b.png")]
    long_label = "A label far too long to sit under one screenshot without being cut " * 2
    out = render_card(KIT, shots, "Ship it", size, tmp_path / "c.png", layout="split",
                      labels=["Before", long_label])
    with Image.open(out) as im:
        im = im.convert("RGB")
        assert im.size == size
        boxes = [_bbox_of(im, red), _bbox_of(im, green)]
    W, H = size
    assert all(b and b[0] >= 16 and b[2] <= W - 16 and b[3] <= H - 16 for b in boxes)


@pytest.mark.parametrize("size", SIZES)
def test_split_with_one_undecodable_file_falls_back_to_hero_at_the_exact_size(tmp_path, size):
    good = _solid(tmp_path, (1600, 900), (30, 60, 230), "good.png")
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    split = render_card(KIT, [good, bad], "Ship it", size, tmp_path / "split.png", layout="split")
    hero = render_card(KIT, [good], "Ship it", size, tmp_path / "hero.png", layout="hero")
    with Image.open(split) as a, Image.open(hero) as b:
        assert a.size == b.size == size
        assert a.convert("RGB").tobytes() == b.convert("RGB").tobytes()


# --- the headline face (Task 20) ---------------------------------------------

from dataclasses import replace  # noqa: E402

from launch_desk.render import FACE_FONTS, head_font_path  # noqa: E402


@pytest.mark.parametrize("face", ["serif", "rounded", "sans"])
def test_each_face_is_a_bundled_variable_font_with_its_licence(face):
    path = FACE_FONTS[face]
    assert path.is_file()
    licences = list(path.parent.glob("*OFL*.txt"))
    assert any(path.stem.split("[")[0].lower() in p.name.lower() for p in licences) or (
        face == "sans" and (path.parent / "OFL.txt").is_file())
    axes = load_font(str(path), 64, 700).get_variation_axes()
    weight = next(a for a in axes if str(a.get("name", b"")).lower().find("weight") >= 0)
    assert weight["minimum"] <= 700 <= weight["maximum"]


def test_bundled_faces_stay_under_one_and_a_half_megabytes():
    added = sum(FACE_FONTS[f].stat().st_size for f in ("serif", "rounded"))
    assert added < 1.5 * 1024 * 1024


@pytest.mark.parametrize("face,family,expected", [
    ("serif", None, "serif"), ("rounded", None, "rounded"), ("sans", "Georgia", "sans"),
    ("", "Iowan Old Style", "serif"), ("", "SF Pro Rounded", "rounded"), ("", "Inter", "sans"),
    ("", None, "sans"), ("bogus", None, "sans"),
])
def test_headline_font_follows_the_brand_face(face, family, expected):
    kit = replace(KIT, font_face=face, font_family=family)
    assert head_font_path(kit) == FACE_FONTS[expected]


@pytest.mark.parametrize("face", ["serif", "rounded"])
@pytest.mark.parametrize("shots", [0, 1, 2])
@pytest.mark.parametrize("size", [(1200, 675), (1080, 1350)])
def test_card_in_each_face_and_layout_has_exact_size(tmp_path, face, shots, size):
    paths = [_shot(tmp_path, name=f"s{i}.png") for i in range(shots)]
    kit = replace(KIT, font_face=face)
    out = render_card(kit, paths, HEADLINE_60, size, tmp_path / "card.png")
    with Image.open(out) as im:
        assert im.size == size


def test_serif_face_changes_the_headline_pixels(tmp_path):
    a = render_card(replace(KIT, font_face="sans"), None, "Focus sessions now land on your calendar",
                    (1200, 675), tmp_path / "a.png")
    b = render_card(replace(KIT, font_face="serif"), None, "Focus sessions now land on your calendar",
                    (1200, 675), tmp_path / "b.png")
    with Image.open(a) as ia, Image.open(b) as ib:
        assert ia.tobytes() != ib.tobytes()


def test_only_the_headline_changes_face(tmp_path):
    """The brand name in the footer is drawn the same in every face."""
    kits = [replace(KIT, font_face=f) for f in ("sans", "serif")]
    crops = []
    for i, kit in enumerate(kits):
        out = render_card(kit, None, "Short", (1200, 675), tmp_path / f"c{i}.png")
        with Image.open(out) as im:
            crops.append(im.crop((0, 675 - 140, 1200, 675)).tobytes())
    assert crops[0] == crops[1]



@pytest.mark.parametrize("face,headline", [
    ("serif", "Новый дизайн уже здесь"),
    ("serif", "Νέο θέμα σήμερα"),
    ("rounded", "Νέο θέμα σήμερα"),
])
@pytest.mark.parametrize("shots", [0, 1])
def test_a_headline_the_face_cannot_draw_falls_back_to_inter(tmp_path, face, headline, shots):
    paths = [_shot(tmp_path)] if shots else []
    a = render_card(replace(KIT, font_face="sans"), paths, headline, (1200, 675), tmp_path / "a.png")
    b = render_card(replace(KIT, font_face=face), paths, headline, (1200, 675), tmp_path / "b.png")
    with Image.open(a) as ia, Image.open(b) as ib:
        assert ia.tobytes() == ib.tobytes()


def test_vietnamese_stays_in_the_brand_face(tmp_path):
    headline = "Chủ đề mới cho trang của bạn"
    a = render_card(replace(KIT, font_face="sans"), None, headline, (1200, 675), tmp_path / "a.png")
    b = render_card(replace(KIT, font_face="serif"), None, headline, (1200, 675), tmp_path / "b.png")
    with Image.open(a) as ia, Image.open(b) as ib:
        assert ia.tobytes() != ib.tobytes()
