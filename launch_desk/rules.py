"""The scroll test: deterministic checks that reject AI-sounding launch copy."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Sequence

from .models import Failure


@dataclass(frozen=True)
class Rule:
    id: str
    message: str
    fix: str
    relaxable: bool


RULES: tuple[Rule, ...] = (
    Rule("first-line-short", "The first line is longer than 12 words.",
         "Cut the first line to 12 words or fewer.", False),
    Rule("stock-phrase", "The post uses a stock launch phrase.",
         "Replace the stock phrase with a plain statement of what changed.", False),
    Rule("em-dash", "The post uses more than one em-dash.",
         "Use at most one em-dash; prefer a full stop or a comma.", True),
    Rule("emoji", "The post uses more than one emoji.",
         "Use at most one emoji, or none.", True),
    Rule("hashtags", "The post uses more than two hashtags.",
         "Use at most two hashtags, or none.", True),
    Rule("placeholder", "The post contains a placeholder such as [link], {name} or TODO.",
         "Replace every placeholder with the real text, or remove it.", False),
    Rule("link-first-line", "The first line contains a link.",
         "Move the link out of the first line.", False),
    Rule("length", "The post is longer than 280 characters on X.",
         "Shorten the post to 280 characters or fewer, counting each link as 23.", False),
    Rule("copied-run", "The post copies a run of five words from a reference caption.",
         "Reword the passage so it shares no five-word run with the references.", False),
)
_RULE_BY_ID = {r.id: r for r in RULES}

STOCK_PHRASES: tuple[str, ...] = (
    "excited to announce", "thrilled to", "game-changer", "game changer",
    "seamless", "seamlessly", "revolutionize", "unlock", "supercharge",
    "in today's fast-paced", "glow-up", "level up", "elevate your",
)

_STOCK_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(p).replace(r"\ ", r"\s+") for p in STOCK_PHRASES) + r")(?!\w)",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_HASHTAG_RE = re.compile(r"(?<!\w)#\w+")
_PLACEHOLDER_RE = re.compile(r"\[[^\]\n]*\]|\{[^}\n]*\}|\bTODO\b")
_WORD_RE = re.compile(r"\w+(?:['’]\w+)*")

# Extended_Pictographic ranges, written out (stdlib re has no \p{...}).
_PICT = (
    "©®‼⁉™ℹ↔-↙↩↪⌚⌛⌨⎈⏏"
    "⏩-⏳⏸-⏺Ⓜ▪▫▶◀◻-◾☀-★☇-☒"
    "☔-⚅⚐-✅✈-✒✔✖✝✡✨✳✴❄❇"
    "❌❎❓-❕❗❣-❧➕-➗➡➰➿⤴⤵"
    "⬅-⬇⬛⬜⭐⭕〰〽㊗㊙"
    "\U0001f000-\U0001f0ff\U0001f10d-\U0001f10f\U0001f12f\U0001f16c-\U0001f171\U0001f17e\U0001f17f"
    "\U0001f18e\U0001f191-\U0001f19a\U0001f1ad-\U0001f1e5\U0001f201-\U0001f20f\U0001f21a\U0001f22f"
    "\U0001f232-\U0001f23a\U0001f23c-\U0001f23f\U0001f249-\U0001f3fa\U0001f400-\U0001f53d"
    "\U0001f546-\U0001f64f\U0001f680-\U0001f6ff\U0001f774-\U0001f77f\U0001f7d5-\U0001f7ff"
    "\U0001f80c-\U0001f80f\U0001f848-\U0001f84f\U0001f85a-\U0001f85f\U0001f888-\U0001f88f"
    "\U0001f8ae-\U0001f8ff\U0001f90c-\U0001f93a\U0001f93c-\U0001f945\U0001f947-\U0001faff"
    "\U0001fc00-\U0001fffd"
)
_MOD = "[\ufe0e\ufe0f\U0001f3fb-\U0001f3ff⃣]*"
_EMOJI_RE = re.compile(
    r"[\U0001f1e6-\U0001f1ff]{2}"  # flags
    r"|[" + _PICT + r"]" + _MOD + r"(?:\u200d[" + _PICT + r"]" + _MOD + r")*"
)
_EMOJI_ONLY_RE = re.compile(r"^(?:[\W_]|" + _EMOJI_RE.pattern + r")+$")


def x_length(text: str) -> int:
    """Length as X counts it, approximately: each http(s):// URL is 23
    characters; everything else is counted by Unicode code points (X's own
    weighting of CJK and emoji differs slightly)."""
    total, last = 0, 0
    for m in _URL_RE.finditer(text):
        total += len(text[last:m.start()]) + 23
        last = m.end()
    return total + len(text[last:])


def _first_line(text: str) -> str:
    return text.split("\n", 1)[0].strip()


def _word_count(line: str) -> int:
    return sum(1 for tok in line.split() if any(c.isalnum() for c in tok))


def _words(text: str) -> list[str]:
    return [w.lower().replace("’", "'") for w in _WORD_RE.findall(text)]


def _count_emoji(text: str) -> int:
    return len(_EMOJI_RE.findall(text))


def _bare_introducing(line: str) -> bool:
    if not re.match(r"introducing\b", line, re.IGNORECASE):
        return False
    rest = line[len("introducing"):].strip().rstrip(".!")
    if re.search(r"[,:;—–]| - ", rest):
        return False
    return len(rest.split()) <= 4


def _first_line_short(text: str) -> bool:
    return _word_count(_first_line(text)) > 12


def _stock(text: str) -> bool:
    return bool(_STOCK_RE.search(text)) or _bare_introducing(_first_line(text))


def _em_dash(text: str) -> bool:
    return text.count("—") > 1


def _emoji(text: str) -> bool:
    return _count_emoji(text) > 1


def _hashtags(text: str) -> bool:
    return len(_HASHTAG_RE.findall(text)) > 2


def _placeholder(text: str) -> bool:
    return bool(_PLACEHOLDER_RE.search(text))


def _link_first(text: str) -> bool:
    return bool(_URL_RE.search(_first_line(text)))


_PREDICATES: dict[str, Callable[[str], bool]] = {
    "first-line-short": _first_line_short,
    "stock-phrase": _stock,
    "em-dash": _em_dash,
    "emoji": _emoji,
    "hashtags": _hashtags,
    "placeholder": _placeholder,
    "link-first-line": _link_first,
}


def relaxed_rules(voice_samples: Sequence[str]) -> set[str]:
    """Relaxable rules that at least one voice sample breaks on its own."""
    relaxed: set[str] = set()
    for rule in RULES:
        if not rule.relaxable:
            continue
        pred = _PREDICATES[rule.id]
        if any(pred(s) for s in voice_samples):
            relaxed.add(rule.id)
    return relaxed


def _copied_run(text: str, references: Sequence[str]) -> bool:
    words = _words(text)
    if len(words) < 5:
        return False
    runs = {tuple(words[i:i + 5]) for i in range(len(words) - 4)}
    for ref in references:
        rw = _words(ref)
        for i in range(len(rw) - 4):
            if tuple(rw[i:i + 5]) in runs:
                return True
    return False


def check(text: str, *, network: str, reference_captions: Sequence[str] = (),
          voice_samples: Sequence[str] = ()) -> list[Failure]:
    relaxed = relaxed_rules(voice_samples)
    failed: list[str] = []
    for rule in RULES:
        if rule.id in relaxed:
            continue
        if rule.id == "length":
            hit = network == "x" and x_length(text) > 280
        elif rule.id == "copied-run":
            hit = _copied_run(text, reference_captions)
        else:
            hit = _PREDICATES[rule.id](text)
        if hit:
            failed.append(rule.id)
    return [Failure(i, _RULE_BY_ID[i].message, _RULE_BY_ID[i].fix) for i in failed]
