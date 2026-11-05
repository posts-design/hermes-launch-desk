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


@dataclass(frozen=True)
class Brief:
    shipped: Literal["feature", "product", "rebrand", "partnership"]
    network: Literal["x", "linkedin"]
    what: str
    product_url: str


@dataclass(frozen=True)
class Reference:
    id: str
    brand: str
    url: str
    caption: str
    views: int | None
    category: str | None
    why: str


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


@dataclass(frozen=True)
class Draft:
    hooks: tuple[str, ...]
    caption: str
    failures: tuple[Failure, ...]
    redrafted: bool
