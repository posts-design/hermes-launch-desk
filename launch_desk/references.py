"""Pick three real launch posts from posts.design to ground the draft."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from .host import Host
from .models import Brief, PastLaunch, Reference

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


# ------------------------------------------------------------------ shape notes
# What a reference lends, read from its caption in code: the opening move, how many
# lines and how long, and whether it carries a list or ends on a link.

_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_NUMBER_WORDS = ("one two three four five six seven eight nine ten eleven twelve "
                 "a dozen dozens hundreds thousands").split()
_AUDIENCE = re.compile(r"^(?:hey|hi|dear|attention|calling all|if you|for (?:all |every )?\w+s\b)",
                       re.IGNORECASE)
_ANNOUNCE = re.compile(
    r"^(?:introducing|announcing|meet\b|new\b|today\b|launching|just (?:shipped|launched|landed)"
    r"|product update|update:|(?:we|i)(?:'re|’re| are| just| have|'ve|’ve)?\s+"
    r"(?:shipped|launched|launching|announced|introducing|built|made|released|happy|excited|thrilled)"
    r"|(?:i'm|i’m) (?:happy|excited|thrilled))",
    re.IGNORECASE)
_LIST_ITEM = re.compile(r"^(?:[-*•→└▪>–]|\d+[.)]\s)")
_ENDS_ON_LINK = re.compile(r"(?:👉|→|↓|⬇️?|\bat|\bhere\s*-?|\bbelow|:)\s*$", re.IGNORECASE)
SHORT_CLAIM_WORDS = 8

OPENINGS = {
    "question": "opens with a question",
    "number": "opens with a number",
    "audience": "opens by calling out its audience",
    "can-now": 'opens with what you "can now" do',
    "announce": "opens by naming the launch",
    "short-claim": "opens with a short claim",
    "statement": "opens by saying what it does",
}


def _lines(caption: str) -> list[str]:
    return [ln.strip() for ln in (caption or "").splitlines()
            if ln.strip() and not _URL.fullmatch(ln.strip())]


def _bare(line: str) -> str:
    """The line without leading emoji, bullets or punctuation."""
    return re.sub(r"^[\W_]+", "", line)


def _opening(first: str) -> str:
    bare = _bare(first)
    sentence = re.split(r"(?<=[.!?])\s+", bare, maxsplit=1)[0]
    words = sentence.lower().split()
    if any(q in first for q in "?？¿"):
        return "question"
    if words and (words[0][0].isdigit() or words[0] in _NUMBER_WORDS
                  or " ".join(words[:2]) in _NUMBER_WORDS):
        return "number"
    if _AUDIENCE.match(bare):
        return "audience"
    if re.search(r"\bcan now\b", sentence, re.IGNORECASE):
        return "can-now"
    if _ANNOUNCE.match(bare):
        return "announce"
    if len(words) <= SHORT_CLAIM_WORDS:
        return "short-claim"
    return "statement"


def _is_item(line: str) -> bool:
    if _LIST_ITEM.match(line):
        return True
    first = line[:1]
    return bool(first) and not first.isalnum() and first not in "\"'“‘@#(" and ord(first) > 0x2000


def caption_shape(caption: str) -> dict:
    """The shape of a caption as data: opening move, line count, size, list, link."""
    lines = _lines(caption)
    text = " ".join(lines)
    size = "short" if len(text) < 120 else "medium" if len(text) < 240 else "long"
    return {
        "opening": _opening(lines[0]) if lines else "statement",
        "lines": len(lines),
        "size": size,
        "list": sum(1 for ln in lines[1:] if _is_item(ln)) >= 2,
        "link": bool(_URL.search(caption or "")) or bool(lines and _ENDS_ON_LINK.search(lines[-1])),
    }


def shape_note(caption: str) -> str:
    """One line on what a caption lends: "opens with a question, 3 short lines, a list"."""
    s = caption_shape(caption)
    n = s["lines"]
    count = f"{n} {'line' if n == 1 else 'lines'}"
    if s["size"] == "short":
        count = f"{n} short {'line' if n == 1 else 'lines'}"
    elif s["size"] == "long":
        count += ", long"
    parts = [OPENINGS[s["opening"]], count]
    if s["list"]:
        parts.append("a list")
    if s["link"]:
        parts.append("ends on a link")
    return ", ".join(parts)


_VIEWS_TAIL = re.compile(r",\s*[\d.]+[KM]? views$")
BRAND_CHARS = 24  # a brand name in the reference list and the draft prompt


def short_brand(brand: str) -> str:
    """The brand on one line, cut at a word to BRAND_CHARS (with "…")."""
    name = " ".join((brand or "").split())
    if len(name) <= BRAND_CHARS:
        return name
    head = name[:BRAND_CHARS - 1]
    cut = head.rsplit(" ", 1)[0] if " " in head else head
    return cut.rstrip(" ,.;:") + "…"


OWN_BRAND = "Your own launch"


def _signups_text(n: int) -> str:
    return f"{n:,} sign-up" + ("" if n == 1 else "s")


def own_reference(launch: PastLaunch) -> Reference:
    """The founder's best past launch as a reference: its post, its sign-ups, and the pack's
    name where a post would have its link."""
    return Reference(id=f"own:{launch.pack}", brand=OWN_BRAND, url=launch.pack, caption=launch.caption,
                     views=None, category=CATEGORY.get(launch.shipped), signups=launch.signups,
                     why=f"{OWN_BRAND}, {_signups_text(launch.signups)}")


def with_own(launch: PastLaunch | None, refs: list[Reference], k: int = 3) -> list[Reference]:
    """The references with the founder's own launch first (reference 1, whose shape the draft
    borrows), still k in all. Without a past launch, refs unchanged."""
    if launch is None:
        return refs
    own = own_reference(launch)
    return [own] + [r for r in refs if r.id != own.id][: k - 1]


def is_own(ref: Reference) -> bool:
    return ref.signups is not None


def reference_where(ref: Reference) -> str:
    """Where a reference can be found: its link, or for the founder's own launch its pack."""
    return f"(pack {ref.url})" if is_own(ref) else ref.url


def reference_line(ref: Reference) -> str:
    """'<brand>: <shape note>, <views> views. <fit>.' for the reference list. A reference
    without a caption has no shape note. The founder's own launch reads
    'Your own launch, 12 sign-ups: <shape note>.'"""
    if is_own(ref):
        note = shape_note(ref.caption) if (ref.caption or "").strip() else ""
        return f"{OWN_BRAND}, {_signups_text(ref.signups)}" + (f": {note}." if note else ".")
    parts = [shape_note(ref.caption)] if (ref.caption or "").strip() else []
    if ref.views:
        parts.append(f"{_short(ref.views)} views")
    line = short_brand(ref.brand) or "A brand"
    if parts:
        line += ": " + ", ".join(parts)
    fit = _VIEWS_TAIL.sub("", ref.why or "").strip()
    return f"{line}. {fit}." if fit else f"{line}."


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

