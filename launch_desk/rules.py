"""The scroll test: deterministic checks that reject AI-sounding launch copy."""
from __future__ import annotations

import ipaddress
import re
import socket
import urllib.parse
from dataclasses import dataclass
from typing import Callable, Sequence

from .models import Failure
from .references import STOPWORDS


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
    Rule("template-opener", "The first line follows the \"Your X should\" template.",
         "Open another way than \"Your X should\".", False),
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
    Rule("private-link", "The post links to an address only you can open.",
         "Use the public link people can open.", False),
    Rule("length", "The post is longer than 280 characters on X.",
         "Shorten the post to 280 characters or fewer, counting each link as 23.", False),
    Rule("copied-run", "The post copies a run of five words from a reference caption.",
         "Reword the passage so it shares no five-word run with the references.", False),
    Rule("repeats-first-line", "A later line repeats most of the first line.",
         "Make every line after the first say something new; don't restate the first line.", False),
    Rule("repeated-phrase", "Two lines say the same thing.",
         "Say the repeated phrase once; give every line something new.", False),
)
_RULE_BY_ID = {r.id: r for r in RULES}

STOCK_PHRASES: tuple[str, ...] = (
    "excited to announce", "thrilled to", "game-changer", "game changer",
    "seamless", "seamlessly", "revolutionize", "unlock", "supercharge",
    "in today's fast-paced", "glow-up", "level up", "elevate your",
    "check this out", "say hello to", "meet the new", "watch this",
)

_STOCK_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(p).replace(r"\ ", r"\s+") for p in STOCK_PHRASES) + r")(?!\w)",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_TEMPLATE_RE = re.compile(r"^your \w+(?: \w+)? should\b", re.IGNORECASE)
# A link written without its scheme: localhost, an IPv4 address or a .local name, then a port or a
# path. Without either it is a word in a sentence ("runs on localhost", "Version 10.0.0.1"), not a link.
_BARE_HOST_RE = re.compile(
    r"(?<![\w.@/:-])(?P<host>localhost|[\w-]+(?:\.[\w-]+)*\.(?:localhost|local)|\d{1,3}(?:\.\d{1,3}){3})"
    r"(?:(?P<port>:\d+)|(?=/[^\s/]|/(?:\s|$)))\S*",
    re.IGNORECASE,
)
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


def first_line_words(text: str) -> int:
    return _word_count(_first_line(text))


def style_counts(text: str) -> tuple[int, int, int]:
    """(em-dashes, emoji, hashtags) in the text."""
    return text.count("—"), _count_emoji(text), len(_HASHTAG_RE.findall(text))


def has_link(text: str) -> bool:
    return bool(_URL_RE.search(text))


def _bare_introducing(line: str) -> bool:
    if not re.match(r"introducing\b", line, re.IGNORECASE):
        return False
    rest = line[len("introducing"):].strip().rstrip(".!")
    if re.search(r"[,:;—–]| - ", rest):
        return False
    return len(rest.split()) <= 4


def _first_line_short(text: str) -> bool:
    return _word_count(_first_line(text)) > 12


def _find_stock(text: str) -> str:
    text = text.replace("\u2019", "'")
    m = _STOCK_RE.search(text)
    if m:
        return " ".join(m.group(0).split())
    first = _first_line(text)
    return first if _bare_introducing(first) else ""


def _stock(text: str) -> bool:
    return bool(_find_stock(text))


def _find_template(text: str) -> str:
    m = _TEMPLATE_RE.match(_first_line(text))
    return m.group(0) if m else ""


def _private_host(host: str) -> bool:
    """localhost, a loopback, private, link-local or unspecified address, or a .local name."""
    host = host.lower().rstrip(".")
    if host == "localhost" or host.endswith((".localhost", ".local")):
        return True
    try:
        addr = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        if not re.fullmatch(r"[0-9.]+", host):
            return False
        try:  # the short and decimal forms a browser also opens: 127.1, 2130706433
            addr = ipaddress.ip_address(socket.inet_aton(host))
        except OSError:
            return False
    return addr.is_loopback or addr.is_private or addr.is_link_local or addr.is_unspecified


def _find_private_link(text: str) -> str:
    """The first link (host and port, as written) that only the founder's machine or network can open."""
    for m in _URL_RE.finditer(text):
        try:
            parts = urllib.parse.urlsplit(m.group(0).rstrip(".,;:!?)]}'\""))
            host = parts.hostname or ""
        except ValueError:
            continue
        if host and _private_host(host):
            return parts.netloc
    for m in _BARE_HOST_RE.finditer(_URL_RE.sub(" ", text)):
        if _private_host(m.group("host")):
            return m.group("host") + (m.group("port") or "")
    return ""


def is_private_link(url: str) -> bool:
    return bool(_find_private_link(url or ""))


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


def _stem(w: str) -> str:
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def _content_words(line: str) -> set[str]:
    return set(_content_seq(line))


def _content_seq(line: str, skip: frozenset[str] | set[str] = frozenset()) -> list[str]:
    """The line's content words in order: lowercase, stopwords out, a plural "s" dropped."""
    words = re.findall(r"[a-z0-9]+", line.lower().replace("'", "").replace("\u2019", ""))
    return [s for s in (_stem(w) for w in words if w not in STOPWORDS) if s not in skip]


# Words too common to count as a repeat on their own ("you can", "get", "one").
_FILLER = frozenset(
    "can will get got has have had was were not but all any more most what when who which there here "
    "they them then than also about one every each some own out up do does did make makes made me my "
    "us he she his her yours way".split()
)


def _triples(seq: list[str]) -> list[tuple[str, str, str]]:
    """Ordered triples of content words that sit within four words of each other ("deep work
    sits next" holds "deep work next"), in reading order."""
    out: dict[tuple[str, str, str], None] = {}
    for i in range(len(seq)):
        for j in range(i + 1, min(i + 3, len(seq))):
            for k in range(j + 1, min(i + 4, len(seq))):
                out.setdefault((seq[i], seq[j], seq[k]))
    return list(out)


def echo_lines(text: str, product: str = "") -> list[int]:
    """Indexes of the lines after the first that repeat it: most of its words (repeating_lines)
    or a three-word phrase of it."""
    lines = [_URL_RE.sub(" ", ln).strip() for ln in text.split("\n")]
    skip = set(_content_seq(product)) | _FILLER
    first = set(_triples(_content_seq(lines[0], skip))) if lines else set()
    echoed = {i for i, ln in enumerate(lines[1:], 1) if ln and first & set(_triples(_content_seq(ln, skip)))}
    return sorted(echoed | set(repeating_lines(text)))


def _find_repeated_phrase(text: str, product: str = "") -> str:
    """What lines repeat: a phrase of two content words that two lines after the first both have
    ("next ... meeting"), a phrase of three content words of line 1 that a later line says again
    (near each other: "deep work ... next"), else a content word in three or more lines. The
    product's own name never counts, and link-only lines are skipped. "" if nothing repeats."""
    skip = set(_content_seq(product)) | _FILLER
    lines = [_URL_RE.sub(" ", ln).strip() for ln in text.split("\n")]
    seqs = [_content_seq(ln, skip) for ln in lines if ln]
    first_seen: dict[tuple[str, str], int] = {}
    for i, seq in enumerate(seqs[1:], 1):
        for pair in dict.fromkeys(zip(seq, seq[1:])):
            if first_seen.setdefault(pair, i) != i:
                return " ".join(pair)
    if seqs:
        first = _triples(seqs[0])
        for seq in seqs[1:]:
            shared = set(first) & set(_triples(seq))
            if shared:
                return " ".join(next(t for t in first if t in shared))
    lines_with: dict[str, int] = {}
    for seq in seqs:
        for w in dict.fromkeys(seq):
            lines_with[w] = lines_with.get(w, 0) + 1
    return next((w for seq in seqs for w in seq if lines_with[w] >= 3), "")


def repeating_lines(text: str) -> list[int]:
    """Indexes of the lines after the first that repeat most of the first line (60% of its
    content words); none when the first line has fewer than three."""
    lines = [ln.strip() for ln in text.split("\n")]
    first = _content_words(lines[0]) if lines else set()
    if len(first) < 3:
        return []
    found = []
    for i, ln in enumerate(lines[1:], 1):
        if not ln or _URL_RE.fullmatch(ln):
            continue
        if len(first & _content_words(_URL_RE.sub(" ", ln))) >= 0.6 * len(first):
            found.append(i)
    return found


def _repeats_first(text: str) -> bool:
    return bool(repeating_lines(text))


_PREDICATES: dict[str, Callable[[str], bool]] = {
    "first-line-short": _first_line_short,
    "stock-phrase": _stock,
    "template-opener": lambda t: bool(_find_template(t)),
    "em-dash": _em_dash,
    "emoji": _emoji,
    "hashtags": _hashtags,
    "placeholder": _placeholder,
    "link-first-line": _link_first,
    "private-link": lambda t: bool(_find_private_link(t)),
    "repeats-first-line": _repeats_first,
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


def _find_placeholder(text: str) -> str:
    m = _PLACEHOLDER_RE.search(text)
    return m.group(0) if m else ""


def _runs(text: str) -> list[tuple[str, ...]]:
    """Five-word runs in order. A link is not wording: runs stop at it and never include it,
    so two posts that end on the same product link share no run through it."""
    out = []
    for part in _URL_RE.split(text):
        words = _words(part)
        out += [tuple(words[i:i + 5]) for i in range(len(words) - 4)]
    return out


def _find_copied_run(text: str, references: Sequence[str], own_words: Sequence[str] = ()) -> str:
    """The first five-word run the text shares with a reference caption, lowercased; "" if none.
    A run inside the founder's own words (own_words) is theirs, never a copy (R40)."""
    runs = set(_runs(text)) - {run for words in own_words for run in _runs(words or "")}
    if not runs:
        return ""
    for ref in references:
        for run in _runs(ref):
            if run in runs:
                return " ".join(run)
    return ""


def _copied_run(text: str, references: Sequence[str]) -> bool:
    return bool(_find_copied_run(text, references))


# Rules that can name what they caught; the text goes in Failure.found.
_FINDERS: dict[str, Callable[[str], str]] = {
    "stock-phrase": _find_stock,
    "template-opener": _find_template,
    "private-link": _find_private_link,
    "placeholder": _find_placeholder,
}


def check(text: str, *, network: str, reference_captions: Sequence[str] = (),
          voice_samples: Sequence[str] = (), own_words: Sequence[str] = (),
          product: str = "") -> list[Failure]:
    relaxed = relaxed_rules(voice_samples)
    failed: list[Failure] = []
    for rule in RULES:
        if rule.id in relaxed:
            continue
        found = ""
        if rule.id == "length":
            hit = network == "x" and x_length(text) > 280
        elif rule.id == "copied-run":
            found = _find_copied_run(text, reference_captions, own_words)
            hit = bool(found)
        elif rule.id == "repeated-phrase":
            found = _find_repeated_phrase(text, product)
            hit = bool(found)
            if hit:
                failed.append(Failure(rule.id, f'Two lines repeat "{found}".',
                                      f'Say "{found}" once; give every line something new.', found))
            continue
        else:
            hit = _PREDICATES[rule.id](text)
            if hit and rule.id in _FINDERS:
                found = _FINDERS[rule.id](text)
        if hit:
            failed.append(Failure(rule.id, rule.message, rule.fix, found))
    return failed
