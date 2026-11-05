"""Pick three real launch posts from posts.design to ground the draft."""
from __future__ import annotations

import json
import math
from pathlib import Path

from .host import Host, SourceUnavailable
from .models import Brief, Reference

PACK_PATH = Path(__file__).parent / "sample_pack.json"

CATEGORY = {
    "feature": "feature-update",
    "product": "announcement",
    "rebrand": "rebrand",
    "partnership": "partner",
}

ALSO = {
    "feature": ("new feature announcement", "product update"),
    "product": ("product launch", "now available"),
    "rebrand": ("rebrand reveal", "new brand identity"),
    "partnership": ("partnership announcement", "teaming up"),
}


def build_search_args(brief: Brief) -> dict:
    return {
        "query": brief.what[:160],
        "alsoQueries": list(ALSO[brief.shipped]),
        "category": CATEGORY[brief.shipped],
        "collection": "posts",
        "limit": 10,
    }


def _views(item: dict) -> int | None:
    metrics = item.get("sourceMetrics")
    value = metrics.get("views") if isinstance(metrics, dict) else item.get("views")
    return value if isinstance(value, int) and value > 0 else None


def _brand(item: dict) -> str:
    brand = item.get("brand")
    name = brand.get("name") if isinstance(brand, dict) else brand
    if not name:
        creator = item.get("creator")
        name = creator.get("name") if isinstance(creator, dict) else None
    return str(name or "").strip()


def _id(item: dict) -> str:
    key = item.get("key")
    return str((key or {}).get("id") if isinstance(key, dict) else item.get("id") or "")


def _short(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"{n / 1_000:.1f}K".replace(".0K", "K")
    return str(n)


def _why(match: bool, views: int | None) -> str:
    text = "Same kind of launch" if match else "A related launch"
    return f"{text}, {_short(views)} views" if views else text


def _score(item: dict, wanted: str) -> float:
    views = _views(item) or 0
    return ((2 if item.get("category") == wanted else 0)
            + (1 if (item.get("caption") or "").strip() else 0)
            + math.log10(views + 1) / 2)


def _reference(item: dict, wanted: str) -> Reference:
    views = _views(item)
    category = item.get("category")
    return Reference(
        id=_id(item), brand=_brand(item), url=str(item.get("url") or ""),
        caption=(item.get("caption") or "").strip(), views=views, category=category,
        why=_why(category == wanted, views),
    )


def rank_references(items: list[dict], brief: Brief, k: int = 3,
                    exclude: set[str] = frozenset()) -> list[Reference]:
    wanted = CATEGORY[brief.shipped]
    pool = [i for i in items if _id(i) and _id(i) not in exclude]
    pool.sort(key=lambda i: (_score(i, wanted), _views(i) or 0), reverse=True)
    picked: list[Reference] = []
    brands: set[str] = set()
    for it in pool:
        brand = _brand(it).lower() or _id(it)
        if brand in brands:
            continue
        brands.add(brand)
        picked.append(_reference(it, wanted))
        if len(picked) == k:
            break
    return picked


class McpSource:
    def __init__(self, host: Host):
        self.host = host

    def search(self, args: dict) -> list[dict]:
        data = self.host.mcp("search_references", args)
        groups = [data.get("items")] + [a.get("items") for a in (data.get("also") or []) if isinstance(a, dict)]
        seen: set[str] = set()
        out: list[dict] = []
        for group in groups:
            for it in group or []:
                if not isinstance(it, dict) or it.get("kind", "post") != "post":
                    continue
                if not _id(it) or _id(it) in seen:
                    continue
                seen.add(_id(it))
                out.append({**it, "category": args.get("category")})
        return out

    def get(self, id: str) -> dict:
        return self.host.mcp("get_reference", {"id": id})


class SamplePackSource:
    def __init__(self, path: Path = PACK_PATH):
        self.entries: list[dict] = json.loads(Path(path).read_text(encoding="utf-8"))

    def search(self, args: dict) -> list[dict]:
        wanted = args.get("category")
        found = [e for e in self.entries if e.get("category") == wanted]
        return found or list(self.entries)

    def get(self, id: str) -> dict:
        for e in self.entries:
            if e["id"] == id:
                return e
        raise SourceUnavailable("not in the sample pack")


def _pick_live(host: Host, brief: Brief, exclude: set[str]) -> list[Reference]:
    source = McpSource(host)
    items = source.search(build_search_args(brief))
    by_id = {_id(i): i for i in items}
    picked = rank_references(items, brief, exclude=exclude)
    if len(picked) < 3:
        raise SourceUnavailable("too few results")
    out = []
    for ref in picked:
        if not ref.caption:
            full = source.get(ref.id)
            caption = (full.get("caption") or "").strip()
            merged = {**by_id[ref.id], "caption": caption}
            if not _views(merged) and _views(full):
                merged["sourceMetrics"] = full.get("sourceMetrics")
            ref = _reference(merged, CATEGORY[brief.shipped])
        out.append(ref)
    return out


def pick_references(host: Host, brief: Brief,
                    exclude: set[str] = frozenset()) -> tuple[list[Reference], bool]:
    try:
        return _pick_live(host, brief, set(exclude)), False
    except SourceUnavailable:
        pack = SamplePackSource()
        items = pack.search(build_search_args(brief))
        return rank_references(items, brief, exclude=set(exclude)), True
