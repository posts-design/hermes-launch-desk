"""Build launch_desk/sample_pack.json from saved raw search_references results.

Usage: python tools/build_sample_pack.py DIR [OUT] [--base PACK] [--pick ID,ID,...]

DIR holds raw call_mcp envelopes (or the parsed result), one file per search,
named <category>-<anything>.json, e.g. announcement-1.json, feature-update-2.json.
Keeps Posts with a caption and recorded views, at most 2 per brand, highest
views first, mixing both categories. No media URLs are written.

--base PACK keeps the entries of an existing pack (the current one, say) and adds to it.
--pick takes only these ids from DIR, in place of the best-views fill.
Brands and ids in EXCLUDE_BRANDS / EXCLUDE_IDS never make it in, from DIR or from the base.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from launch_desk.references import _brand, _id, _views, _why  # noqa: E402

CATEGORIES = ("announcement", "feature-update")
TARGET = 32
PER_BRAND = 2

# Not launch posts a product founder learns from: government, coins, consoles; and
# xAI's accounts beyond one entry (Grok Build stays).
EXCLUDE_BRANDS = frozenset({"white house", "united states mint", "playstation", "grok bot", "spacex ai"})
EXCLUDE_IDS = frozenset({"x-twitter-xai-2060102590122385460-grok-cli-update-card"})


def excluded(entry: dict) -> bool:
    return entry["brand"].strip().lower() in EXCLUDE_BRANDS or entry["id"] in EXCLUDE_IDS


def load_items(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "ok" in data and "result" in data:
        if not data["ok"]:
            return []
        data = json.loads(data["result"])
    groups = [data.get("items")] + [a.get("items") for a in data.get("also") or []]
    return [i for g in groups for i in g or [] if isinstance(i, dict)]


def _entry(it: dict, category: str) -> dict | None:
    caption = (it.get("caption") or "").strip()
    views, brand, id_ = _views(it), _brand(it), _id(it)
    url = str(it.get("url") or "")
    if it.get("kind", "post") != "post" or not (caption and views and brand and id_):
        return None
    if not url.startswith("https://posts.design/"):
        return None
    # A pack entry always matches its own category: "same kind of launch".
    return {"id": id_, "brand": brand, "url": url, "caption": caption,
            "views": views, "category": category, "why": _why("same", views)}


def _raw(directory: Path) -> dict[str, dict[str, dict]]:
    by_category: dict[str, dict[str, dict]] = {c: {} for c in CATEGORIES}
    if directory is None:
        return by_category
    for path in sorted(directory.glob("*.json")):
        category = next((c for c in CATEGORIES if path.name.startswith(c)), None)
        if not category:
            continue
        for it in load_items(path):
            entry = _entry(it, category)
            if entry is not None and not excluded(entry):
                by_category[category][entry["id"]] = entry
    return by_category


def build(directory: Path | None, base: list[dict] = (), pick: list[str] | None = None,
          target: int = TARGET) -> list[dict]:
    chosen: list[dict] = []
    used: set[str] = set()
    per_brand: dict[str, int] = {}

    def take(entry: dict) -> bool:
        key = entry["brand"].lower()
        if entry["id"] in used or per_brand.get(key, 0) >= PER_BRAND or excluded(entry):
            return False
        used.add(entry["id"])
        per_brand[key] = per_brand.get(key, 0) + 1
        chosen.append(entry)
        return True

    for entry in base:
        take(dict(entry, why=_why("same", entry.get("views"))))

    raw = _raw(directory)
    if pick is not None:
        found = {id_: e for c in CATEGORIES for id_, e in raw[c].items()}
        for id_ in pick:
            if id_ in found:
                take(found[id_])
        return sorted(chosen, key=lambda e: (e["category"], -e["views"]))

    ranked = {c: sorted(v.values(), key=lambda e: -e["views"]) for c, v in raw.items()}
    cursor = {c: 0 for c in CATEGORIES}
    # Alternate between categories, best views first, so both are well represented.
    while len(chosen) < target and any(cursor[c] < len(ranked[c]) for c in CATEGORIES):
        for c in CATEGORIES:
            while cursor[c] < len(ranked[c]):
                entry = ranked[c][cursor[c]]
                cursor[c] += 1
                if take(entry):
                    break
            if len(chosen) == target:
                break
    return sorted(chosen, key=lambda e: (e["category"], -e["views"]))


def main(argv: list[str]) -> int:
    base_path, pick = None, None
    rest: list[str] = []
    it = iter(argv)
    for arg in it:
        if arg == "--base":
            base_path = Path(next(it))
        elif arg == "--pick":
            pick = [p for p in next(it).split(",") if p]
        else:
            rest.append(arg)
    if not rest:
        print(__doc__)
        return 2
    out = Path(rest[1]) if len(rest) > 1 else ROOT / "launch_desk" / "sample_pack.json"
    base = json.loads(base_path.read_text(encoding="utf-8")) if base_path else []
    pack = build(Path(rest[0]), base, pick)
    out.write_text(json.dumps(pack, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(pack)} entries -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
