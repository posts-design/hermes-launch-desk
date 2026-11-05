import json
import urllib.parse
from datetime import date

from PIL import Image

from launch_desk.models import BrandKit, Brief, Draft, Reference
from launch_desk.pack import CHECKLIST, alt_text, write_pack, x_intent_url


def _brand(**kw):
    base = dict(name="Pawr Link", url="https://pawr.link", tagline="Your links, one page",
                bg="#ffffff", ink="#111111", accent="#ff6600", font_family=None, logo_path=None,
                voice_samples=("PRIVATE-VOICE-SAMPLE-ONE", "PRIVATE-VOICE-SAMPLE-TWO"))
    base.update(kw)
    return BrandKit(**base)


def _brief(**kw):
    base = dict(shipped="feature", network="x", what="Link analytics dashboard",
                product_url="https://pawr.link")
    base.update(kw)
    return Brief(**base)


def _refs():
    return [Reference("r1", "Acme", "https://x.com/acme/status/1", "caption text", 12000, "launch", "why"),
            Reference("r2", "Beta", "https://x.com/beta/status/2", "other", None, None, "why")]


def _write(tmp_path, brief=None, brand=None, hook="We shipped link analytics", caption=None):
    card = tmp_path / "src" / "card.png"
    card.parent.mkdir(exist_ok=True)
    Image.new("RGB", (1200, 675), "white").save(card)
    caption = caption or (hook + "\n\nSee who clicks what.\n\nhttps://pawr.link")
    draft = Draft((hook, "b", "c"), caption, (), False)
    out = tmp_path / "packs"
    return write_pack(out, brief or _brief(), brand or _brand(), draft, hook, {"x": card}, _refs()), out


def test_intent_url_round_trips_text():
    text = "Shipped \U0001F680 A&B = C?\nline two #tag https://x.y/z?a=1&b=2"
    url = x_intent_url(text)
    assert url.startswith("https://x.com/intent/post?text=")
    assert urllib.parse.unquote(url.split("text=", 1)[1]) == text
    assert "&" not in url.split("text=", 1)[1]


def test_pack_contains_expected_files(tmp_path):
    pack, out = _write(tmp_path)
    assert pack.parent == out
    names = {p.name for p in pack.iterdir()}
    assert {"post-x.txt", "card-x.png", "alt-text.txt", "checklist.md", "references.md", "pack.json"} <= names
    assert pack.name.startswith(date.today().isoformat() + "-pawr-link-link-analytics")
    assert Image.open(pack / "card-x.png").size == (1200, 675)
    assert (pack / "post-x.txt").read_text().startswith("We shipped link analytics")
    refs = (pack / "references.md").read_text()
    assert "Acme" in refs and "12000" in refs.replace(",", "") and "https://x.com/beta/status/2" in refs
    assert len(CHECKLIST) >= 5


def test_pack_folder_gets_numeric_suffix_if_exists(tmp_path):
    first, out = _write(tmp_path)
    second, _ = _write(tmp_path)
    third, _ = _write(tmp_path)
    assert second.name == first.name + "-2" and third.name == first.name + "-3"


def test_chosen_hook_replaces_first_line_of_post(tmp_path):
    pack, _ = _write(tmp_path, hook="Chosen hook line", caption="Other first line\n\nBody here")
    text = (pack / "post-x.txt").read_text()
    assert text.splitlines()[0] == "Chosen hook line" and "Body here" in text
    data = json.loads((pack / "pack.json").read_text())
    assert urllib.parse.unquote(data["intent_url"].split("text=", 1)[1]) == text.strip()


def test_pack_json_has_no_secrets_or_host_paths_outside_pack(tmp_path):
    pack, _ = _write(tmp_path)
    raw = (pack / "pack.json").read_text()
    data = json.loads(raw)
    assert "PRIVATE-VOICE-SAMPLE" not in raw
    assert "voice_samples" not in raw
    assert str(tmp_path) not in raw
    for f in data["files"].values():
        assert not f.startswith("/") and (pack / f).exists()
    assert data["brand"]["accent"] == "#ff6600" and data["brand"]["name"] == "Pawr Link"
    assert data["references"][0] == {"id": "r1", "brand": "Acme", "url": "https://x.com/acme/status/1",
                                     "views": 12000}
    assert data["hook"] and data["caption"] and data["brief"]["what"]
    for p in pack.rglob("*"):
        if p.suffix in (".txt", ".md", ".json"):
            body = p.read_text()
            assert "PRIVATE-VOICE-SAMPLE" not in body
            assert str(tmp_path) not in body and str(pack) not in body


def test_alt_text_is_bounded_and_mentions_screenshot_only_when_present():
    long = _brief(what="x" * 3000)
    assert len(alt_text(long, _brand(), True)) <= 1000
    assert "screenshot" in alt_text(_brief(), _brand(), True).lower()
    assert "screenshot" not in alt_text(_brief(), _brand(), False).lower()
    assert "Pawr Link" in alt_text(_brief(), _brand(), False)


def test_card_key_cannot_escape_pack_and_long_brand_slug_is_capped(tmp_path):
    card = tmp_path / "c.png"
    Image.new("RGB", (10, 10)).save(card)
    draft = Draft(("h", "b", "c"), "h\nbody", (), False)
    pack = write_pack(tmp_path / "out", _brief(), _brand(name="Very Long Brand " * 10), draft, "h",
                      {"../../evil": card}, [])
    assert {p.parent for p in tmp_path.rglob("card-*.png") if "out" in p.parts} == {pack}
    assert not (tmp_path / "evil.png").exists()
    assert len(pack.name.split("-", 3)[3]) <= 40 + 1 + 40


def test_preview_is_copied_and_listed(tmp_path):
    card = tmp_path / "c.png"
    Image.new("RGB", (1200, 675)).save(card)
    preview = tmp_path / "p.png"
    Image.new("RGB", (390, 900)).save(preview)
    draft = Draft(("h", "b", "c"), "h\nbody", (), False)
    pack = write_pack(tmp_path / "out", _brief(), _brand(), draft, "h", {"x": card}, [], preview=preview)
    data = json.loads((pack / "pack.json").read_text())
    assert (pack / data["files"]["preview"]).is_file()
    assert Image.open(pack / data["files"]["preview"]).size == (390, 900)


def test_alt_text_names_the_card_headline_when_given(tmp_path):
    card = tmp_path / "c.png"
    Image.new("RGB", (1200, 675)).save(card)
    draft = Draft(("h", "b", "c"), "h\nbody", (), False)
    pack = write_pack(tmp_path / "out", _brief(), _brand(), draft, "h", {"x": card}, [],
                      headline="Pet pages can wear your colours now")
    assert "Pet pages can wear your colours now" in (pack / "alt-text.txt").read_text()
