"""Draft the launch post: one model call, code-side checks, at most one redraft.

The model writes a small part of the result. The prompt hands it the facts, the
founder's voice, three real launch posts and two hook patterns; the scroll test
in rules.py decides whether the draft is good enough.

Failures on a returned Draft: the caption's own failures keep their plain
rule id ("em-dash"). A hook's failures carry the hook's 1-based position in the
rule id ("hook2:em-dash") and the message is prefixed "Hook 2: ".

Besides the scroll test, unsupported_details() flags numbers and time or effort
phrases that are not in the facts (rule id "unsupported-detail", one per post).
"""
from __future__ import annotations

import json
import re
from typing import Any, Sequence

from .host import Host
from .models import BrandKit, Brief, Draft, Failure, HookShape, Reference
from .rules import RULES, STOCK_PHRASES, check, relaxed_rules

DRAFT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "hooks": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 3},
        "caption": {"type": "string"},
    },
    "required": ["hooks", "caption"],
}

# Rules that make sense for a single first line.
HOOK_RULES: frozenset[str] = frozenset({
    "first-line-short", "stock-phrase", "em-dash", "emoji", "placeholder",
    "link-first-line", "copied-run",
})

# Words small models reach for that make a post read as written by a model.
# Prompt-only: the scroll test does not check these.
_ALSO_AVOID: tuple[str, ...] = (
    "introducing", "powerful", "effortless", "stunning", "transform", "empower",
    "next-level", "cutting-edge", "say goodbye", "dive in", "journey", "imagine",
)

_NETWORK = {"x": "X", "linkedin": "LinkedIn"}

REF_CHARS = 400
VOICE_CHARS = 300
VOICE_COUNT = 3
REDRAFT_CAPTION_CHARS = 700
PROMPT_BUDGET = 5900  # instructions + input; Hermes adds the schema (~300) on top
HOOK_CHARS = 160

UNSUPPORTED = "unsupported-detail"
_UNSUPPORTED_MAX = 5

# Longest first so "one tap" wins over "one". A bare "one" counts only when a word
# follows it ("one link"), so "Small one:" or "pick one." pass.
_DETAIL_RE = re.compile(
    r"(?<!\w)(?:"
    r"one tap|one click|in seconds|no hands|instantly"
    r"|(?:minute|second|hour|day|week|month|year)s?"
    r"|a dozen|dozens?|hundreds?|thousands?|millions?|billions?"
    r"|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve"
    r"|one(?=\s+[a-z])"
    r"|\d+(?:[.,:]\d+)*"
    r")(?!\w)",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


class DraftError(Exception):
    """The model gave no usable draft after one retry."""

    def __init__(self, message: str = "The model did not return a usable draft. "
                                      "Run /launch again, or try a larger model."):
        super().__init__(message)


def _in_facts(phrase: str, facts: str) -> bool:
    forms = {phrase, phrase[:-1]} if phrase.endswith("s") and len(phrase) > 2 else {phrase}
    return any(re.search(r"(?<!\w)" + re.escape(f) + r"(?!\w)", facts) for f in forms)


def unsupported_details(text: str, facts: str) -> list[Failure]:
    """One "unsupported-detail" Failure when the text has numbers or time and effort
    phrases ("took a minute", "one tap", "instantly") that the facts do not contain
    (case-insensitive); [] otherwise. Links are ignored. The message and the fix
    list the phrases (at most five)."""
    facts_l = (facts or "").lower()
    found: list[str] = []
    for m in _DETAIL_RE.finditer(_URL_RE.sub(" ", text or "")):
        phrase = m.group(0).lower()
        if phrase not in found and not _in_facts(phrase, facts_l):
            found.append(phrase)
    if not found:
        return []
    listed = ", ".join(f'"{p[:20]}"' for p in found[:_UNSUPPORTED_MAX])
    return [Failure(UNSUPPORTED, f"The post adds details that are not in the facts: {listed}.",
                    f"Remove {listed}, or replace with something from FACTS.")]


def facts_text(brief: Brief, brand: BrandKit) -> str:
    """What counts as a fact: product name, tagline, what shipped, the founder's detail, link."""
    return " ".join((brand.name or "", brand.tagline or "", brief.what or "", brief.detail or "",
                     brief.product_url or ""))


def _trim(text: str, limit: int) -> str:
    text = re.sub(r"\n{3,}", "\n\n", (text or "").strip())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(None, 1)[0] if " " in text[:limit] else text[:limit]
    return cut.rstrip(" ,.;:") + "..."


def _one_line(text: str, limit: int) -> str:
    return _trim(re.sub(r"\s+", " ", text or ""), limit)


def _indent(text: str) -> str:
    return "\n".join("   " + line if line else "" for line in text.split("\n"))


def _rule_lines(brief: Brief, relaxed: set[str]) -> list[str]:
    """The scroll test as plain instructions, one per non-relaxed rule."""
    url = brief.product_url
    words = {
        "first-line-short": "Every hook, and the first line of the caption, is 12 words or fewer. Count them.",
        "stock-phrase": "No hype words. Never write: " + ", ".join(STOCK_PHRASES + _ALSO_AVOID) + ".",
        "em-dash": "Do not use the long dash (—). Use a full stop or a comma.",
        "emoji": "No emoji, or one at most.",
        "hashtags": "No hashtags.",
        "placeholder": "No brackets [ ] or { } and no TODO. Fill every gap with real words.",
        "link-first-line": f"Put the link {_one_line(url, 120)} once, on the last line. Never in the first line.",
        "length": "The whole caption is 280 characters or fewer, the link counting as 23. Aim for about 200.",
        "copied-run": "Never copy from the example posts. Not even 5 words in a row.",
    }
    lines = []
    for rule in RULES:
        if rule.id in relaxed:
            continue
        if rule.id == "length" and brief.network != "x":
            lines.append("Keep the whole caption under 700 characters.")
            continue
        lines.append(words[rule.id])
    return lines


def _instructions(brief: Brief, relaxed: set[str]) -> str:
    network = _NETWORK.get(brief.network, brief.network)
    rules = [
        "Write like the founder talking to one friend: plain words, short sentences, first person (I or we).",
        "Say what changed and why someone would care.",
        "Every detail must come from FACTS. Never describe what the founder or a user did beyond what FACTS says. "
        "Never add pets, people, times, steps, counts, results or UI actions that are not in FACTS.",
        "No exclamation marks. No questions to the reader unless a hook pattern asks for one. No \"it's not just X, it's Y\" lines.",
        "Use 3 to 5 short lines, with a blank line between them.",
        "Each line says something new. Don't restate the first line.",
        *_rule_lines(brief, relaxed),
    ]
    numbered = "\n".join(f"{i}. {r}" for i, r in enumerate(rules, 1))
    return (
        f"You write one launch post for {network}.\n\n"
        f"Rules:\n{numbered}\n\n"
        "Reply with JSON only, no other text:\n"
        '{"hooks": ["first line A", "first line B", "first line C"], "caption": "the whole post"}\n'
        "- hooks: three different first lines for this post. Hook 1 follows pattern A, "
        "hook 2 follows pattern B, hook 3 is your own.\n"
        "- caption: the whole post, starting with your best hook as its first line. "
        "Use \\n for line breaks."
    )


def _facts(brief: Brief, brand: BrandKit) -> str:
    lines = [f"Product: {_one_line(brand.name, 80)}"]
    if brand.tagline:
        lines.append(f"What it is: {_one_line(brand.tagline, 200)}")
    lines.append(f"What just shipped ({brief.shipped}): {_one_line(brief.what, 300)}")
    if brief.detail:
        lines.append(f"A true detail from the founder: {_one_line(brief.detail, 300)}")
    lines.append(f"Link: {_one_line(brief.product_url, 120)}")
    return "FACTS\n" + "\n".join(lines)


def _voice(brand: BrandKit) -> str:
    samples = [s for s in brand.voice_samples if s and s.strip()][:VOICE_COUNT]
    if not samples:
        return "THE FOUNDER'S VOICE\nNo past posts. Write plainly, like a builder telling a peer."
    body = "\n".join(f"Post {i}:\n{_indent(_trim(s, VOICE_CHARS))}" for i, s in enumerate(samples, 1))
    return ("THE FOUNDER'S VOICE (tone only; its details are not facts about this launch)\n" + body)


def _references(refs: Sequence[Reference]) -> str:
    if not refs:
        return ""
    body = "\n".join(f"{i}. {_one_line(r.brand, 40) or 'A brand'}:\n{_indent(_trim(r.caption, REF_CHARS))}"
                     for i, r in enumerate(refs[:3], 1))
    return ("REAL LAUNCH POSTS THAT WORKED (learn the shape, never copy wording)\n" + body)


def _shapes(shapes: Sequence[HookShape], when: bool = True) -> str:
    body = "\n".join(f"{label}. {_one_line(s.shape, 160)}"
                     + (f"\n   When: {_one_line(s.when, 160)}" if when else "")
                     for label, s in zip("AB", shapes))
    return ("HOOK PATTERNS (fill each [ ] from FACTS. If FACTS cannot fill a [ ], "
            "write that hook your own way instead of inventing a detail)\n" + body)


def build_brief_prompt(brief: Brief, brand: BrandKit, refs: Sequence[Reference],
                       shapes: Sequence[HookShape], relaxed: set[str]) -> tuple[str, str]:
    """(instructions, input) for the first draft; together under 6,000 characters."""
    parts = [_facts(brief, brand), _voice(brand), _references(refs), _shapes(shapes)]
    return _instructions(brief, relaxed), "\n\n".join(p for p in parts if p)


def build_redraft_prompt(brief: Brief, brand: BrandKit, refs: Sequence[Reference],
                         shapes: Sequence[HookShape], relaxed: set[str],
                         hooks: Sequence[str], caption: str,
                         failures: Sequence[Failure]) -> tuple[str, str]:
    """(instructions, input) for the one redraft. The previous draft carries the shape,
    so the references and the voice block are left out; the facts and hook patterns
    stay. Fix lines are grouped: one line per rule and fix, naming every place it
    failed ("- (em-dash, hook2:em-dash: <fix>)"). Under 6,000 characters together."""
    instructions = _instructions(brief, relaxed)
    previous = "\n".join(f"Hook {n}: {_one_line(h, HOOK_CHARS)}" for n, h in enumerate(hooks, 1))
    groups: dict[tuple[str, str], list[str]] = {}
    for f in failures:
        base = f.rule_id.split(":", 1)[-1]
        groups.setdefault((base, f.fix), []).append(f.rule_id)
    fixes = "\n".join(f"- ({', '.join(ids)}: {fix})" for (_, fix), ids in groups.items())
    head = "\n\n".join(p for p in (_facts(brief, brand), _shapes(shapes, when=False)) if p)
    tail = f"\n\nYour draft broke these rules. Fix exactly these:\n{fixes}\nKeep everything else the same."
    room = PROMPT_BUDGET - len(instructions) - len(head) - len(tail) - len(previous) - 40
    cap = _trim(caption, max(120, min(REDRAFT_CAPTION_CHARS, room)))
    return instructions, f"{head}\n\nYOUR LAST DRAFT\n{previous}\nCaption:\n{cap}{tail}"


_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)


def _loads(text: str) -> Any:
    text = text.strip()
    candidates = [text] + _FENCE_RE.findall(text)
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start:end + 1])
    for c in candidates:
        try:
            return json.loads(c)
        except ValueError:
            continue
    return None


def parse_draft(reply: dict | str | None) -> tuple[list[str], str] | None:
    """(hooks, caption) from a dict, a JSON string, or JSON in a ``` fence; None if the
    shape is wrong (hooks not exactly 3 non-empty strings, caption missing or empty)."""
    data = _loads(reply) if isinstance(reply, str) else reply
    if not isinstance(data, dict):
        return None
    hooks, caption = data.get("hooks"), data.get("caption")
    if not isinstance(hooks, list) or len(hooks) != 3:
        return None
    if not all(isinstance(h, str) and h.strip() for h in hooks):
        return None
    if not isinstance(caption, str) or not caption.strip():
        return None
    return [h.strip() for h in hooks], caption.strip()


# Errors a model call can raise at runtime (provider, network, decoding). Anything
# else, such as an AttributeError, is a bug and propagates.
_CALL_ERRORS = (RuntimeError, ValueError, OSError)


class _Asker:
    def __init__(self, host: Host):
        self.host = host
        self.last_error: BaseException | None = None

    def __call__(self, instructions: str, text: str) -> tuple[list[str], str] | None:
        try:
            reply = self.host.complete_json(instructions, text, DRAFT_SCHEMA)
        except _CALL_ERRORS as exc:
            self.last_error = exc
            return None
        return parse_draft(reply)


def _failures(hooks: Sequence[str], caption: str, brief: Brief, refs: Sequence[Reference],
              voice: Sequence[str], facts: str) -> list[Failure]:
    captions = [r.caption for r in refs]
    found = list(check(caption, network=brief.network, reference_captions=captions, voice_samples=voice))
    found += unsupported_details(caption, facts)
    for n, hook in enumerate(hooks, 1):
        hook_fails = [f for f in check(hook, network=brief.network, reference_captions=captions,
                                       voice_samples=voice) if f.rule_id in HOOK_RULES]
        hook_fails += unsupported_details(hook, facts)
        found += [Failure(f"hook{n}:{f.rule_id}", f"Hook {n}: {f.message}", f.fix) for f in hook_fails]
    return found


def draft_post(host: Host, brief: Brief, brand: BrandKit, refs: Sequence[Reference],
               shapes: Sequence[HookShape]) -> Draft:
    """Draft, check, and redraft once if anything fails; keep the draft with fewer
    failures (a tie keeps the redraft). Unparseable first replies get one retry with
    the same prompt, then DraftError (chained to the last call error, if any). An
    unparseable redraft keeps the first draft. Draft.redrafted is True when the
    returned text is the redraft. Failures include unsupported_details()."""
    voice = tuple(brand.voice_samples)
    relaxed = relaxed_rules(voice)
    facts = facts_text(brief, brand)
    ask = _Asker(host)
    instructions, text = build_brief_prompt(brief, brand, refs, shapes, relaxed)
    first = ask(instructions, text) or ask(instructions, text)
    if first is None:
        raise DraftError() from ask.last_error
    hooks, caption = first
    failures = _failures(hooks, caption, brief, refs, voice, facts)
    if not failures:
        return Draft(tuple(hooks), caption, (), False)
    r_instructions, r_text = build_redraft_prompt(brief, brand, refs, shapes, relaxed,
                                                  hooks, caption, failures)
    second = ask(r_instructions, r_text)
    if second is not None:
        hooks2, caption2 = second
        failures2 = _failures(hooks2, caption2, brief, refs, voice, facts)
        if len(failures2) <= len(failures):
            return Draft(tuple(hooks2), caption2, tuple(failures2), True)
    return Draft(tuple(hooks), caption, tuple(failures), False)
