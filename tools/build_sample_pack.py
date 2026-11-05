"""Build launch_desk/sample_pack.json from saved raw search_references results.

Usage: python tools/build_sample_pack.py DIR [OUT]

DIR holds raw call_mcp envelopes (or the parsed result), one file per search,
named <category>-<anything>.json, e.g. announcement-1.json, feature-update-2.json.
Keeps Posts with a caption and recorded views, at most 2 per brand, highest
views first, mixing both categories. No media URLs are written.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from launch_desk.references import _brand, _id, _views, _why  # noqa: E402

CATEGORIES = ("announcement", "feature-update")
TARGET = 30
PER_BRAND = 2


def load_items(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "ok" in data and "result" in data:
        if not data["ok"]:
            return []
        data = json.loads(data["result"])
    groups = [data.get("items")] + [a.get("items") for a in data.get("also") or []]
    return [i for g in groups for i in g or [] if isinstance(i, dict)]


def build(directory: Path) -> list[dict]:
    by_category: dict[str, dict[str, dict]] = {c: {} for c in CATEGORIES}
    for path in sorted(directory.glob("*.json")):
        category = next((c for c in CATEGORIES if path.name.startswith(c)), None)
        if not category:
            continue
        for it in load_items(path):
            caption = (it.get("caption") or "").strip()
            views, brand, id_ = _views(it), _brand(it), _id(it)
            url = str(it.get("url") or "")
            if it.get("kind", "post") != "post" or not (caption and views and brand and id_):
                continue
            if not url.startswith("https://posts.design/"):
                continue
            by_category[category][id_] = {
                "id": id_, "brand": brand, "url": url, "caption": caption,
                "views": views, "category": category, "why": _why(True, views),
            }
    ranked = {c: sorted(v.values(), key=lambda e: -e["views"]) for c, v in by_category.items()}
    chosen: list[dict] = []
    used: set[str] = set()
    per_brand: dict[str, int] = {}
    cursor = {c: 0 for c in CATEGORIES}
    # Alternate between categories, best views first, so both are well represented.
    while len(chosen) < TARGET and any(cursor[c] < len(ranked[c]) for c in CATEGORIES):
        for c in CATEGORIES:
            while cursor[c] < len(ranked[c]):
                entry = ranked[c][cursor[c]]
                cursor[c] += 1
                key = entry["brand"].lower()
                if entry["id"] not in used and per_brand.get(key, 0) < PER_BRAND:
                    used.add(entry["id"])
                    per_brand[key] = per_brand.get(key, 0) + 1
                    chosen.append(entry)
                    break
            if len(chosen) == TARGET:
                break
    return sorted(chosen, key=lambda e: (e["category"], -e["views"]))


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    out = Path(argv[1]) if len(argv) > 1 else ROOT / "launch_desk" / "sample_pack.json"
    pack = build(Path(argv[0]))
    out.write_text(json.dumps(pack, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(pack)} entries -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
