"""/launch film: the HyperFrames project, the renderer, the recommendations and the reply."""
import json
import os
import re
import sys
from pathlib import Path

import pytest
from PIL import Image

from fakes import FakeHost
from launch_desk import film, followup, wizard
from launch_desk.host import HermesHost, SourceUnavailable
from launch_desk.models import BrandKit, Brief, Draft, Reference
from launch_desk.pack import write_pack
from launch_desk.render import FONT_PATH, load_font

LIST = {"playbooks": [
    {"name": "founder-led-demo", "title": "Founder-led demo", "useWhen": ["A founder can show one thing"],
     "runtime": {"targetSeconds": 60, "minSeconds": 30, "maxSeconds": 90}},
    {"name": "screen-led-short", "title": "Screen-led short", "useWhen": ["Nobody needs to be on camera"],
     "runtime": {"targetSeconds": 30, "minSeconds": 15, "maxSeconds": 45}},
], "hooks": [], "setVersion": "a" * 24}
PLAYBOOK = {"name": "screen-led-short", "title": "Screen-led short", "runtime": {"targetSeconds": 30},
            "beats": [{"id": "problem", "label": "Their problem", "share": 0.2, "purpose": "p",
                       "say": "[The problem, in their words].", "show": "Their own screen."},
                      {"id": "product", "label": "The product", "share": 0.8, "purpose": "p",
                       "say": "[Name]: [what it does].", "show": "The product doing it."}]}
FREE = {"allowed": True, "tools": ["search_references", "list_playbooks", "get_my_access"]}
PRO = {"allowed": True, "tools": ["search_references", "list_playbooks", "get_playbook", "get_my_access"]}


def _png(path: Path, size=(1600, 1000), colour="#2b8a3e") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, colour).save(path, "PNG")
    return path


def make_pack(root: Path, *, hook="We built it for people who asked.", card_line="Brookhour 2.0 puts focus in your calendar.",
              name="Brookhour", shots=0, logo=False, shipped="feature", face="serif", tmp=None) -> Path:
    tmp = tmp or root.parent
    brand = BrandKit(name=name, url="https://brookhour.test/", tagline="", bg="#F6F1E7", ink="#1D1B16",
                     accent="#C2410C", font_family="Newsreader", font_face=face,
                     logo_path=str(_png(tmp / "src" / "logo.png", (64, 64), "#C2410C")) if logo else None)
    brief = Brief(shipped, "x", "focus sessions in your calendar", "https://brookhour.test/", detail="people asked")
    card = _png(tmp / "src" / "card.png", (1200, 675))
    shot_paths = [_png(tmp / "src" / f"shot{n}.png", (1600 - 400 * n, 1000)) for n in range(shots)]
    draft = Draft((hook, "b", "c"), hook + "\n\nEvery session lands on your calendar.\n\nhttps://brookhour.test", (), False)
    refs = [Reference("r1", "Acme", "https://posts.design/a", "caption", 1000, "feature-update", "why")]
    return write_pack(root, brief, brand, draft, hook, {"x": card}, refs, has_screenshot=bool(shots),
                      headline=card_line, layout="hero" if shots else "statement", screenshots=shot_paths)


def host_with(tmp_path, packs=1, mcp=None, **kw):
    host = FakeHost(data_dir=tmp_path / "data", mcp_replies=mcp if mcp is not None else {}, **kw)
    for _ in range(packs):
        make_pack(host.data_dir() / "packs", tmp=tmp_path)
    return host


def only_pack(host) -> Path:
    [p] = list((host.data_dir() / "packs").iterdir())
    return p


def writes_mp4(code=0, data=b"\x00\x00\x00\x18ftypisom" + b"x" * 100):
    def run(argv, cwd, env, timeout):
        out = Path(argv[-1])
        if data:
            out.write_bytes(data)
        return code, "rendering\nframe 420/420\ndone"
    return run


def media(reply):
    return [Path(line[len("MEDIA:"):]) for line in reply.splitlines() if line.startswith("MEDIA:")]


# ------------------------------------------------------------------ the project

def _refs(html: str) -> list[str]:
    found = re.findall(r'\b(?:src|href)="([^"]*)"', html)
    found += re.findall(r'url\(\s*["\']?([^"\')]+)["\']?\s*\)', html)
    return found


@pytest.mark.parametrize("shots,logo", [(0, False), (1, True), (2, True)])
def test_project_files_exist_and_every_reference_is_local(tmp_path, shots, logo):
    pack = make_pack(tmp_path / "packs", shots=shots, logo=logo)
    data = json.loads((pack / "pack.json").read_text())
    plan = film.plan_for(pack, data)
    project = film.write_project(pack, plan)
    html = (project / "index.html").read_text()
    refs = _refs(html)
    assert refs, "the film uses its fonts and images"
    for ref in refs:
        assert not re.match(r"[a-z][a-z0-9+.-]*:|//|/", ref, re.I), ref  # no scheme, no host, no root path
        target = (project / ref).resolve()
        assert target.is_file() and project.resolve() in target.parents, ref
    assert "<script" not in html.lower()
    assert (project / "assets" / "mark.png").is_file()
    assert any(p.suffix == ".ttf" for p in (project / "assets" / "fonts").iterdir())
    assert any(p.name.endswith("OFL.txt") for p in (project / "assets" / "fonts").iterdir())
    assert (project / "assets" / "shot.png").is_file() == bool(shots)


def test_root_composition_declares_the_film_size_and_length(tmp_path):
    pack = make_pack(tmp_path / "packs")
    project = film.write_project(pack, film.plan_for(pack, json.loads((pack / "pack.json").read_text())))
    html = (project / "index.html").read_text()
    root = re.search(r'<div id="root"[^>]*>', html).group(0)
    assert 'data-composition-id="main"' in root and 'data-start="0"' in root
    assert f'data-duration="{film.SECONDS}"' in root and 'data-width="1920"' in root and 'data-height="1080"' in root
    assert "data-no-timeline" in root  # CSS-only motion: the renderer has no timeline to wait for
    for anim in re.findall(r"animation:\s*([^;]+);", html):
        assert " 1 both" in anim, anim  # one finite iteration, filled both ways
    assert "infinite" not in html


def test_beats_cover_the_film_without_gaps(tmp_path):
    assert film.BEATS[0][1] == 0 and film.BEATS[-1][2] == film.SECONDS
    for (_a, _s, end), (_b, start, _e) in zip(film.BEATS, film.BEATS[1:]):
        assert end == start
    assert all(start < end for _id, start, end in film.BEATS)
    pack = make_pack(tmp_path / "packs")
    rows = film.beat_rows(film.plan_for(pack, json.loads((pack / "pack.json").read_text())))
    assert [(r["id"], r["start"], r["end"]) for r in rows] == list(film.BEATS)


def test_founder_text_is_escaped(tmp_path):
    hook = 'We said <script>alert("x")</script> & meant it.'
    pack = make_pack(tmp_path / "packs", hook=hook, card_line="Tom & Jerry <b>ship</b>", name="A&B <i>")
    project = film.write_project(pack, film.plan_for(pack, json.loads((pack / "pack.json").read_text())))
    html = (project / "index.html").read_text()
    assert "<script" not in html and "<b>" not in html and "<i>" not in html
    assert "&lt;script&gt;" in html and "&amp;" in html


def test_long_card_line_shrinks_inside_the_box(tmp_path):
    long = "Brookhour 2.0 puts every focus session, every deep work block and every meeting in one calendar view"
    pack = make_pack(tmp_path / "packs", card_line=long, name="An Extremely Long Product Name For A Small Team")
    plan = film.plan_for(pack, json.loads((pack / "pack.json").read_text()))
    fitted = film.layout(plan)
    card = fitted["card"]
    assert card.size < 112 and 1 <= len(card.lines) <= 3
    font = load_font(str(plan.head_font), card.size, 700)
    width = film.WIDTH - 2 * film.MARGIN_X
    assert all(font.getlength(line) <= width * film.SLACK for line in card.lines)
    assert " ".join(card.lines).split() == long.split()  # nothing dropped
    name = fitted["end_name"]
    name_font = load_font(str(plan.head_font), name.size, 700)
    assert all(name_font.getlength(line) <= 1500 * film.SLACK for line in name.lines)
    html = film.build_html(plan, None)
    assert f"font-size: {card.size}px" in html


def test_no_screenshot_shows_the_product_name_instead(tmp_path):
    pack = make_pack(tmp_path / "packs", shots=0)
    plan = film.plan_for(pack, json.loads((pack / "pack.json").read_text()))
    project = film.write_project(pack, plan)
    html = (project / "index.html").read_text()
    product = re.search(r'<section id="beat-product".*?</section>', html, re.S).group(0)
    assert "Brookhour" in product and "<img" not in product
    assert plan.shot is None and not (project / "assets" / "shot.png").exists()


def test_screenshot_is_the_widest_the_card_used_and_fits_the_frame(tmp_path):
    pack = make_pack(tmp_path / "packs", shots=2)
    data = json.loads((pack / "pack.json").read_text())
    assert data["files"]["screenshots"] == ["screenshot-1.png", "screenshot-2.png"]
    plan = film.plan_for(pack, data)
    assert plan.shot == pack / "screenshot-1.png"  # 1600x1000 is wider than 1200x1000
    project = film.write_project(pack, plan)
    w, h = (int(x) for x in re.search(r'id="shot-frame".*?width="(\d+)" height="(\d+)"', (project / "index.html").read_text(), re.S).groups())
    assert w <= film.SHOT_BOX[0] and h <= film.SHOT_BOX[1]


def test_pack_keeps_the_face_screenshots_and_logo_for_later(tmp_path):
    pack = make_pack(tmp_path / "packs", shots=1, logo=True, face="rounded")
    data = json.loads((pack / "pack.json").read_text())
    assert data["brand"]["font_face"] == "rounded"
    assert (pack / data["files"]["logo"]).is_file() and (pack / data["files"]["screenshots"][0]).is_file()
    plan = film.plan_for(pack, data)
    assert plan.head_font.name == "Nunito.ttf" and plan.logo == pack / data["files"]["logo"]


def test_an_older_pack_takes_face_and_logo_from_the_stored_brand_of_the_same_site(tmp_path):
    pack = make_pack(tmp_path / "packs")
    data = json.loads((pack / "pack.json").read_text())
    del data["brand"]["font_face"], data["brand"]["font_family"]  # as packs before Task 24
    logo = _png(tmp_path / "brand" / "logo.png", (32, 32))
    same = {"url": "https://www.brookhour.test/app", "font_face": "rounded", "logo_path": str(logo)}
    other = {"url": "https://other.test", "font_face": "rounded", "logo_path": str(logo)}
    assert film.plan_for(pack, data, same).head_font.name == "Nunito.ttf"
    assert film.plan_for(pack, data, same).logo == logo
    plan = film.plan_for(pack, data, other)
    assert plan.logo is None and plan.head_font.name == "Inter.ttf"


def test_beat_sheet_lists_the_four_beats_with_their_text(tmp_path):
    pack = make_pack(tmp_path / "packs")
    plan = film.plan_for(pack, json.loads((pack / "pack.json").read_text()))
    sheet = film.beat_sheet(plan)
    assert sheet.count("\n## ") == 4
    assert plan.line1 in sheet and plan.card_line in sheet


# ------------------------------------------------------------------ the renderer

def test_config_beats_path_and_neither_means_no_render():
    which = {"hyperframes": "/usr/local/bin/hyperframes"}.get
    lane = ["sudo", "/usr/local/bin/lane", "render", "{project}", "{out}"]
    assert film.renderer(lane, which) == (lane, "config")
    assert film.renderer(None, which) == (["/usr/local/bin/hyperframes", "render", "{project}", "-o", "{out}"], "path")
    assert film.renderer(None, lambda _n: None) is None
    assert film.renderer([], lambda _n: None) is None


@pytest.mark.parametrize("bad", ["sudo lane {project} {out}", ["lane", "{project}"], ["lane", 3, "{out}"], {"a": 1}])
def test_a_bad_setting_is_ignored(bad):
    assert film.renderer(bad, lambda _n: None) is None
    assert film.renderer(bad, {"hyperframes": "/bin/hf"}.get)[1] == "path"


def test_render_runs_the_configured_command_with_placeholders_list_args_and_telemetry_off(tmp_path):
    host = host_with(tmp_path, config={"film_render": ["lane", "render", "--in={project}", "{out}"]},
                     on_path={"hyperframes": "/bin/hf"}, run=writes_mp4())
    reply = film.run_film(host, "")
    pack = only_pack(host)
    [call] = host.commands
    assert call["argv"][:3] == ["lane", "render", f"--in={pack / 'film'}"]
    assert Path(call["argv"][3]).parent == pack and call["argv"][3].endswith(".mp4")
    assert all(isinstance(a, str) for a in call["argv"])
    assert call["cwd"] == pack / "film" and call["timeout"] == film.RENDER_TIMEOUT_S
    assert call["env"]["HYPERFRAMES_NO_TELEMETRY"] == "1" and call["env"]["DO_NOT_TRACK"] == "1"
    assert (pack / "film.mp4").stat().st_size > 0
    assert media(reply) == [pack / "film.mp4"]
    data = json.loads((pack / "pack.json").read_text())
    assert data["film"]["mp4"] == "film.mp4" and data["film"]["renderer"] == "config"
    assert data["film"]["rendered_at"] and data["film"]["project"] == "film"
    assert not [p for p in pack.iterdir() if p.name.startswith(".film-")]


def test_render_from_path_uses_hyperframes_render_with_o(tmp_path):
    host = host_with(tmp_path, on_path={"hyperframes": "/opt/bin/hyperframes"}, run=writes_mp4())
    film.run_film(host, "")
    [call] = host.commands
    pack = only_pack(host)
    assert call["argv"][:4] == ["/opt/bin/hyperframes", "render", str(pack / "film"), "-o"]
    assert json.loads((pack / "pack.json").read_text())["film"]["renderer"] == "path"


def _timeout(argv, cwd, env, timeout):
    raise TimeoutError("too slow")


@pytest.mark.parametrize("run", [writes_mp4(code=1), writes_mp4(data=b""), _timeout])
def test_failed_render_falls_back_to_the_no_renderer_reply(tmp_path, run):
    host = host_with(tmp_path, on_path={"hyperframes": "/bin/hf"}, run=run)
    reply = film.run_film(host, "")
    pack = only_pack(host)
    assert len(host.commands) == 1
    assert not (pack / "film.mp4").exists() and not media(reply)
    assert not [p for p in pack.iterdir() if p.name.startswith(".film-")]
    assert "npx hyperframes render" in reply and str(pack / "film") in reply
    assert film.RENDER_FAILED in reply
    data = json.loads((pack / "pack.json").read_text())
    assert data["film"]["mp4"] is None and data["film"]["renderer"] is None


def test_no_renderer_writes_the_project_and_says_how_to_render_it(tmp_path):
    host = host_with(tmp_path)
    reply = film.run_film(host, "")
    pack = only_pack(host)
    assert host.commands == [] and not media(reply)
    assert (pack / "film" / "index.html").is_file() and (pack / film.BEAT_SHEET).is_file()
    assert "npx hyperframes render" in reply and "HYPERFRAMES_NO_TELEMETRY=1" in reply
    assert film.RENDER_FAILED not in reply
    assert json.loads((pack / "pack.json").read_text())["film"] == {
        "project": "film", "mp4": None, "rendered_at": None, "renderer": None, "playbook": None}


def test_unattended_renders_nothing_and_runs_nothing(tmp_path):
    host = host_with(tmp_path, unattended=True, on_path={"hyperframes": "/bin/hf"}, run=writes_mp4())
    reply = wizard.run_launch(host, "film")
    pack = only_pack(host)
    assert reply == film.UNATTENDED and host.commands == [] and host.mcp_calls == []
    assert not (pack / "film").exists() and "film" not in json.loads((pack / "pack.json").read_text())


def test_hermes_host_runs_a_command_and_stops_it_on_timeout(tmp_path):
    host = HermesHost(object())
    code, out = host.run([sys.executable, "-c", "import os; print(os.environ['DO_NOT_TRACK'])"], tmp_path,
                         {**os.environ, "DO_NOT_TRACK": "1"}, 30)
    assert code == 0 and out.strip() == "1"
    with pytest.raises(TimeoutError):
        host.run([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, dict(os.environ), 0.5)


def test_hermes_host_reads_its_setting_and_survives_a_bad_ctx():
    class Ctx:
        def get_config(self, key, default=None):
            return {"film_render": ["a", "{project}", "{out}"]}.get(key, default)

    class Broken:
        def get_config(self, key, default=None):
            raise RuntimeError("config unreadable")

    assert HermesHost(Ctx()).config("film_render") == ["a", "{project}", "{out}"]
    assert HermesHost(Broken()).config("film_render") is None
    assert HermesHost(object()).config("film_render") is None


def test_video_media_goes_to_the_hermes_video_cache(tmp_path):
    class Ctx:
        class state:
            data_dir = str(tmp_path / "home" / "plugin-data" / "ns")
    src = tmp_path / "film.mp4"
    src.write_bytes(b"\x00\x00\x00\x18ftypisom")
    out = Path(HermesHost(Ctx()).media_path(src, "film"))
    assert out.parent == tmp_path / "home" / "cache" / "videos" and out.suffix == ".mp4"
    assert out.read_bytes() == src.read_bytes()


# ------------------------------------------------------------------ recommendations

def test_free_account_gets_the_playbook_list_only(tmp_path):
    host = host_with(tmp_path, mcp={"list_playbooks": LIST, "get_my_access": FREE})
    reply = film.run_film(host, "")
    tools = [t for t, _ in host.mcp_calls]
    assert "get_playbook" not in tools and tools.count("list_playbooks") == 1
    assert "Screen-led short" in reply  # feature launches get the screen-led short
    assert json.loads((only_pack(host) / "pack.json").read_text())["film"]["playbook"] == "screen-led-short"


def test_pro_account_reads_the_playbook_once_and_shows_its_beats(tmp_path):
    host = host_with(tmp_path, mcp={"list_playbooks": LIST, "get_my_access": PRO, "get_playbook": PLAYBOOK})
    reply = film.run_film(host, "")
    calls = [(t, a) for t, a in host.mcp_calls if t == "get_playbook"]
    assert calls == [("get_playbook", {"name": "screen-led-short"})]
    assert "Their problem, 6 s" in reply and "The product, 24 s" in reply  # share x target runtime
    sheet = (only_pack(host) / film.BEAT_SHEET).read_text()
    assert "Their problem, 6 s" in sheet


def test_recommendations_stay_short(tmp_path):
    many = dict(PLAYBOOK, beats=[dict(PLAYBOOK["beats"][0], id=f"b{n}", label=f"Beat {n}", share=0.125) for n in range(8)])
    host = FakeHost(data_dir=tmp_path, mcp_replies={"list_playbooks": LIST, "get_my_access": PRO, "get_playbook": many})
    lines, md, name = film.recommend(host, "feature")
    assert len(lines) <= 6 and name == "screen-led-short"
    assert sum(1 for line in md if line.startswith("- Beat")) == 8


def test_unknown_kind_or_missing_playbook_takes_the_first_listed(tmp_path):
    only = {"playbooks": [LIST["playbooks"][0]]}
    host = FakeHost(data_dir=tmp_path, mcp_replies={"list_playbooks": only, "get_my_access": FREE})
    assert film.recommend(host, "feature")[2] == "founder-led-demo"


@pytest.mark.parametrize("mcp", [
    {"list_playbooks": SourceUnavailable("signed out", kind="error")},
    {"list_playbooks": LIST, "get_my_access": SourceUnavailable("down", kind="timeout")},
    {"list_playbooks": LIST, "get_my_access": PRO, "get_playbook": SourceUnavailable("quota", kind="error")},
    {},
])
def test_posts_design_down_still_makes_the_film(tmp_path, mcp):
    host = host_with(tmp_path, mcp=mcp)
    reply = film.run_film(host, "")
    pack = only_pack(host)
    assert (pack / "film" / "index.html").is_file() and "npx hyperframes render" in reply
    assert "get_playbook" not in [t for t, _ in host.mcp_calls] or mcp.get("get_playbook")


# ------------------------------------------------------------------ which pack, and the command

def test_blank_is_the_newest_pack_and_a_label_or_folder_picks_one(tmp_path):
    host = host_with(tmp_path, packs=0)
    root = host.data_dir() / "packs"
    old = make_pack(root, name="Fernlight", tmp=tmp_path)
    data = json.loads((old / "pack.json").read_text())
    data["created_at"] = "2020-01-01T00:00:00+00:00"
    (old / "pack.json").write_text(json.dumps(data))
    new = make_pack(root, name="Brookhour", tmp=tmp_path)
    assert film.resolve(root, "") == new.name
    assert film.resolve(root, old.name) == old.name
    label = followup.pack_label(old.name, data)
    assert film.resolve(root, label) == old.name
    assert film.resolve(root, "../etc") is None and film.resolve(root, "nothing-like-it") is None


def test_launch_film_routes_to_the_film_and_a_launch_with_a_link_does_not(tmp_path):
    host = host_with(tmp_path)
    reply = wizard.run_launch(host, "film")
    assert (only_pack(host) / "film" / "index.html").is_file() and "hyperframes" in reply
    host2 = FakeHost(data_dir=tmp_path / "d2")
    assert wizard.run_launch(host2, "film presets https://x.test -- people asked") == wizard.STOPPED  # a launch: asks, gets no answer
    assert host2.asked and not (host2.data_dir() / "packs").exists()


def test_unknown_pack_name_says_so_and_makes_nothing(tmp_path):
    host = host_with(tmp_path)
    reply = wizard.run_launch(host, "film no-such-pack")
    assert not (only_pack(host) / "film").exists() and "no-such-pack" in reply and host.asked == []


def test_no_packs_yet(tmp_path):
    host = FakeHost(data_dir=tmp_path)
    assert film.run_film(host, "") == film.NO_PACK


def test_chat_app_reply_names_the_pack_not_server_paths(tmp_path):
    host = host_with(tmp_path, surface="telegram", run=writes_mp4(), on_path={"hyperframes": "/bin/hf"})
    reply = film.run_film(host, "")
    pack = only_pack(host)
    text = "\n".join(line for line in reply.splitlines() if not line.startswith("MEDIA:"))
    assert str(pack) not in text and pack.name in text and media(reply) == [pack / "film.mp4"]
    assert host.said  # the slow render is announced on a chat app


def test_a_second_film_replaces_the_project_and_keeps_an_earlier_mp4(tmp_path):
    host = host_with(tmp_path, on_path={"hyperframes": "/bin/hf"}, run=writes_mp4())
    film.run_film(host, "")
    pack = only_pack(host)
    (pack / "film" / "stale.txt").write_text("old")
    first = json.loads((pack / "pack.json").read_text())["film"]
    host._on_path = {}
    film.run_film(host, "")
    second = json.loads((pack / "pack.json").read_text())["film"]
    assert not (pack / "film" / "stale.txt").exists()
    assert second["mp4"] == "film.mp4" and second["rendered_at"] == first["rendered_at"]
