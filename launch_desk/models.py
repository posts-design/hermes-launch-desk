from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class BrandKit:
    name: str
    url: str
    tagline: str
    bg: str
    ink: str
    accent: str
    font_family: str | None
    logo_path: str | None
    voice_samples: tuple[str, ...] = ()
    # The headline face that suits the brand: "serif", "rounded" or "sans"; "" when unknown
    # (a kit saved before faces were read), and the card then judges it from font_family.
    font_face: str = ""


@dataclass(frozen=True)
class Brief:
    shipped: Literal["feature", "product", "rebrand", "partnership"]
    network: Literal["x", "linkedin"]
    what: str
    product_url: str
    detail: str = ""  # one true detail from the founder, used as a fact


@dataclass(frozen=True)
class Reference:
    id: str
    brand: str
    url: str
    caption: str
    views: int | None
    category: str | None
    why: str
    signups: int | None = None  # set on the founder's own past launch; its url is the pack name


@dataclass(frozen=True)
class PastLaunch:
    """One of the founder's approved packs with recorded sign-ups (see followup.best_launch)."""
    pack: str  # the pack folder's name
    caption: str  # the post as approved
    first_line: str  # its first line, cut for one line of chat
    signups: int  # the total over channels
    channels: tuple[tuple[str, int], ...]  # (channel, sign-ups), most first
    shipped: str = ""
    latest: bool = False  # True when no newer pack of the same site has a post, whatever its results


@dataclass(frozen=True)
class HookShape:
    id: str
    title: str
    when: str
    shape: str


@dataclass(frozen=True)
class Failure:
    rule_id: str
    message: str
    fix: str
    found: str = ""  # the offending text, where the rule knows it (a stock phrase, a private link)


@dataclass(frozen=True)
class Draft:
    hooks: tuple[str, ...]
    caption: str
    failures: tuple[Failure, ...]
    redrafted: bool
    use_case: str = ""  # one concrete use of what shipped, from the facts; the caption carries it
    first_failures: tuple[Failure, ...] = ()  # the first draft's failures, when a redraft was asked for
    card_line: str = ""  # the model's card headline, as written; card_line() checks it


@dataclass(frozen=True)
class HookOption:
    """One hook offered to the founder, with the whole post it makes."""
    text: str
    source: Literal["founder", "model"]
    post: str
    dropped: tuple[str, ...] = ()  # body lines left out because they repeat this hook
    flagged: bool = False  # offered although it fails a check: no model hook passed
    added: tuple[str, ...] = ()  # lines put back so the post says what shipped (R44)


@dataclass(frozen=True)
class CheckLine:
    """One line of the visible checklist: the rules it covers, pass or fail, and the line."""
    rules: tuple[str, ...]
    ok: bool
    text: str
