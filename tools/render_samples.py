"""Render sample cards and feed previews for two real brands, to look at by eye.

Usage: python tools/render_samples.py ASSETS_DIR [OUT_DIR]

ASSETS_DIR holds files fetched once by hand (not committed):
  pawr-og.png, posts-og.png         each site's og:image, used as the screenshot
  pawr-logo.png, posts-favicon.ico  each site's logo (the ICO exercises the largest-frame path)
A tall phone-shaped screenshot is drawn here so the phone-column layout is covered too.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from launch_desk.models import BrandKit  # noqa: E402
from launch_desk.render import render_card, render_feed_preview  # noqa: E402

X, LINKEDIN = (1200, 675), (1080, 1350)


def kits(assets: Path) -> list[tuple[str, BrandKit, str, str]]:
    def logo(name: str) -> str | None:
        p = assets / name
        return str(p) if p.exists() else None

    pawr = BrandKit(
        name="pawr.link", url="https://pawr.link",
        tagline="One pawrful page for everything you make, sell and share.",
        bg="#FAFAFA", ink="#111111", accent="#6A3CFF", font_family="Inter",
        logo_path=logo("pawr-logo.png"),
    )
    posts = BrandKit(
        name="posts.design", url="https://posts.design",
        tagline="Find the launch posts that worked, and why.",
        bg="#F5F5F5", ink="#0F1419", accent="#1D9BF0", font_family="Inter",
        logo_path=logo("posts-favicon.ico"),
    )
    dark = BrandKit(
        name="Quiet Hours", url="https://quiethours.example",
        tagline="Focus timers that stay out of your way.",
        bg="#0E0F13", ink="#F2F2F2", accent="#F5A524", font_family=None, logo_path=None,
    )
    return [
        ("dark", dark, "Quiet Hours 2.0: timers that follow you across every device you own \U0001F680",
         "Quiet Hours 2.0 is out \U0001F680\n\nYour timers now follow you from laptop to phone.\n\nhttps://quiethours.example"),
        ("pawr", pawr, "Custom themes are live.",
         "Custom themes are live on pawr.link.\n\nPick a look, tweak the colours, done in a minute.\n\nhttps://pawr.link"),
        ("posts", posts, "Search 40,000 launch posts by what actually worked",
         "We just shipped search on posts.design.\n\nType what you launched and see the posts that worked for it.\n\nhttps://posts.design"),
    ]


def phone_shot(path: Path, accent: str) -> Path:
    """A plain phone-shaped app screen: status bar, title, a few cards."""
    w, h = 1170, 2532
    im = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, w, 140), fill=(248, 248, 248))
    d.rounded_rectangle((60, 220, 640, 300), 20, fill=(30, 30, 30))
    y = 380
    for i in range(7):
        d.rounded_rectangle((60, y, w - 60, y + 260), 40, fill=(244, 244, 246))
        d.ellipse((100, y + 60, 240, y + 200), fill=accent if i == 0 else (220, 220, 226))
        d.rounded_rectangle((280, y + 80, 820, y + 120), 20, fill=(60, 60, 60))
        d.rounded_rectangle((280, y + 150, 700, y + 180), 15, fill=(180, 180, 186))
        y += 300
    im.save(path)
    return path


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    assets = Path(argv[1])
    out = Path(argv[2]) if len(argv) > 2 else assets / "renders"
    out.mkdir(parents=True, exist_ok=True)
    for key, kit, headline, caption in kits(assets):
        og = assets / f"{'posts' if key == 'dark' else key}-og.png"
        phone = phone_shot(out / f"_{key}-phone-src.png", kit.accent)
        x = render_card(kit, og, headline, X, out / f"{key}-x.png")
        render_card(kit, og, headline, LINKEDIN, out / f"{key}-linkedin.png")
        render_card(kit, phone, headline, X, out / f"{key}-x-phone.png")
        render_card(kit, phone, headline, LINKEDIN, out / f"{key}-linkedin-phone.png")
        render_card(kit, None, headline, X, out / f"{key}-x-text.png")
        render_card(kit, None, headline, LINKEDIN, out / f"{key}-linkedin-text.png")
        render_feed_preview(kit, caption, x, out / f"{key}-preview.png")
        phone.unlink()
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
