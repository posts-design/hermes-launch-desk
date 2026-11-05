"""Pick three real launch posts from posts.design to ground the draft."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from .host import Host
from .models import Brief, Reference

PACK_PATH = Path(__file__).parent / "sample_pack.json"

CATEGORY = {
    "feature": "feature-update",
    "product": "announcement",
    "rebrand": "rebrand",
    "partnership": "partner",
}

ALSO = {
    "feature": "new feature announcement",
    "product": "product launch",
    "rebrand": "rebrand reveal",
    "partnership": "partnership announcement",
}

STOPWORDS = frozenset(
    "a an and are as at be by for from how i in into is it its of on or our so that the "
    "their this to we with you your just now new".split()
)


def keyword_phrase(what: str, limit: int = 4) -> str:
    words = [w for w in re.findall(r"[a-z0-9]+", what.lower()) if w not in STOPWORDS]
    return " ".join(words[:limit])


def _stems(text: str) -> set[str]:
    """Lowercase words of three letters or more, stopwords out, a plural "s" dropped."""
    out = set()
    for w in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if len(w) < 3 or w in STOPWORDS:
            continue
        out.add(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w)
    return out


def brief_terms(brief: Brief) -> set[str]:
    """The words a sample-pack caption is matched on: the keyword phrase and the words of what."""
    return _stems(keyword_phrase(brief.what) + " " + brief.what)


def _overlap(item: dict, terms: set[str]) -> int:
    return len(terms & _stems(item.get("caption") or ""))


def build_search_args(brief: Brief) -> dict:
    also = [p for p in (keyword_phrase(brief.what), ALSO[brief.shipped]) if p]
    return {
        "query": brief.what[:160],
        "alsoQueries": list(dict.fromkeys(also)),
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


def _why(kind: str, views: int | None) -> str:
    text = {"close": "Close match for your launch", "same": "Same kind of launch",
            "pack": "Close match from the sample pack"}.get(kind, "A related launch")
    return f"{text}, {_short(views)} views" if views else text


def _tier(item: dict) -> float | None:
    """Fit by origin: main-query exact 3, main looser 2, also-query exact 1.5, also looser 1."""
    origin = item.get("_origin")
    if origin is None:
        return None
    loose = bool(item.get("_loose"))
    if origin == "main":
        return 2.0 if loose else 3.0
    return 1.0 if loose else 1.5


def _score(item: dict, wanted: str, terms: set[str]) -> float:
    views = _views(item) or 0
    tier = _tier(item)
    if tier is None:  # sample pack item: shared words first, then category, then views
        return (10 * _overlap(item, terms) + (3 if item.get("category") == wanted else 0)
                + min(math.log10(views + 1), 11) / 4)
    return tier - 0.05 * int(item.get("_pos") or 0) + math.log10(views + 1) / 4


def _reference(item: dict, wanted: str, terms: set[str]) -> Reference:
    views = _views(item)
    tier = _tier(item)
    if tier is None:
        if _overlap(item, terms):
            kind = "pack"
        else:
            kind = "same" if item.get("category") == wanted else "other"
    else:
        kind = "close" if tier >= 2.0 else "same"
    category = item.get("category")
    return Reference(
        id=_id(item), brand=_brand(item), url=str(item.get("url") or ""),
        caption=(item.get("caption") or "").strip(), views=views, category=category,
        why=_why(kind, views),
    )


def rank_references(items: list[dict], brief: Brief, k: int = 3,
                    exclude: set[str] = frozenset()) -> list[Reference]:
    wanted = CATEGORY[brief.shipped]
    terms = brief_terms(brief)
    pool = [i for i in items if _id(i) and _id(i) not in exclude and (i.get("caption") or "").strip()]
    pool.sort(key=lambda i: (_score(i, wanted, terms), _views(i) or 0), reverse=True)
    picked: list[Reference] = []
    brands: set[str] = set()
    for it in pool:
        brand = _brand(it).lower() or _id(it)
        if brand in brands:
            continue
        brands.add(brand)
        picked.append(_reference(it, wanted, terms))
        if len(picked) == k:
            break
    return picked


class McpSource:
    def __init__(self, host: Host):
        self.host = host

    def search(self, args: dict) -> list[dict]:
        data = self.host.mcp("search_references", args)
        groups = [("main", data.get("items"))] + [
            ("also", a.get("items")) for a in (data.get("also") or []) if isinstance(a, dict)]
        seen: set[str] = set()
        out: list[dict] = []
        for origin, group in groups:
            for pos, it in enumerate(group or []):
                if not isinstance(it, dict) or it.get("kind", "post") != "post":
                    continue
                if not _id(it) or _id(it) in seen:
                    continue
                seen.add(_id(it))
                out.append({**it, "_origin": origin, "_pos": pos, "_loose": bool(it.get("matched"))})
        return out


class SamplePackSource:
    def __init__(self, path: Path = PACK_PATH):
        self.entries: list[dict] = json.loads(Path(path).read_text(encoding="utf-8"))

    def search(self, args: dict) -> list[dict]:
        wanted = args.get("category")
        found = [e for e in self.entries if e.get("category") == wanted]
        return found or list(self.entries)

