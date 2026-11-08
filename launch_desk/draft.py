"""Draft the launch post: one model call, code-side checks, at most one redraft.

The model writes a small part of the result. The prompt hands it the facts, the
founder's voice, three real launch posts and two hook patterns; the scroll test
in rules.py decides whether the draft is good enough.

Failures on a returned Draft: the caption's own failures keep their plain
rule id ("em-dash"). A hook's failures carry the hook's 1-based position in the
rule id ("hook2:em-dash") and the message is prefixed "Hook 2: ".

Besides the scroll test, unsupported_details() flags numbers and time or effort
phrases that are not in the facts (rule id "unsupported-detail", one per post).

The model also returns a use case: one concrete use of what shipped, from the facts.
The caption must carry it ("use-case-missing" otherwise), and the use case
itself gets unsupported_details() ("use-case:unsupported-detail"). The prompt asks
the model to borrow the shape of the top reference (see references.shape_note).
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Iterable, Sequence

from .host import Host
from .models import BrandKit, Brief, CheckLine, Draft, Failure, HookOption, HookShape, Reference
from .references import BRAND_CHARS, STOPWORDS, shape_note
from .rules import (RULES, STOCK_PHRASES, check, first_line_words, has_link, is_private_link, relaxed_rules,
                    echo_lines, style_counts, x_length)

DRAFT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "use_case": {"type": "string"},
        "hooks": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 3},
        "caption": {"type": "string"},
        "card_line": {"type": "string"},
    },
    "required": ["use_case", "hooks", "caption", "card_line"],
}

# Rules that make sense for a single first line.
HOOK_RULES: frozenset[str] = frozenset({
    "first-line-short", "stock-phrase", "template-opener", "lower-start", "em-dash", "emoji", "placeholder",
    "link-first-line", "copied-run",
})

# Rules about the founder's own input (their link): the redraft prompt never asks the model to fix them.
MODEL_CANNOT_FIX: frozenset[str] = frozenset({"private-link"})

# Words small models reach for that make a post read as written by a model.
# Prompt-only: the scroll test does not check these.
_ALSO_AVOID: tuple[str, ...] = (
    "introducing", "powerful", "effortless", "stunning", "transform", "empower",
    "next-level", "cutting-edge", "say goodbye", "dive in", "journey", "imagine",
)

# Stock phrases the prompt leaves out because a listed one already says it ("game-changer").
_SAME_AS_LISTED = frozenset({"game changer", "seamlessly"})

_NETWORK = {"x": "X", "linkedin": "LinkedIn"}

REF_CHARS = 250
VOICE_CHARS = 200
VOICE_COUNT = 3
REDRAFT_CAPTION_CHARS = 700
REDRAFT_CAPTION_MIN = 80  # the last caption is shown at least this long, even over budget
REDRAFT_SHORT_LINE = 40  # hook and use case lines when the redraft is far over budget
PROMPT_BUDGET = 5900  # instructions + input; Hermes adds the schema (~300) on top
HOOK_CHARS = 160

UNSUPPORTED = "unsupported-detail"
PHRASE_SEP = ", "  # between the phrases in an unsupported-detail Failure.found
_UNSUPPORTED_MAX = 5
USE_CASE_MISSING = "use-case-missing"
USE_CASE_CHARS = 140
_CARRIED = 0.4  # share of the use case's words the caption must contain

# Longest first so "one tap" wins over "one". A bare "one" or "half" counts only when a word
# follows it ("one link", "half the steps"), so "Small one:" or "pick one." pass.
_DETAIL_RE = re.compile(
    r"(?<!\w)(?:"
    r"one tap|one click|in seconds|in minutes|no hands|instantly"
    r"|today|tonight|this week|every day"
    r"|(?:minute|second|hour|day|week|month|year)s?"
    r"|a dozen|dozens?|hundreds?|thousands?|millions?|billions?"
    r"|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve"
    r"|(?:one|half)(?=\s+[a-z])"
    r"|\d+(?:[.,:]\d+)*"
    r")(?!\w)",
    re.IGNORECASE,
)
_NUMBER_WORDS = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
                 "eleven", "twelve")
# A number in the facts counts in either form: "three" or "3".
_SAME_NUMBER = {**{w: str(n) for n, w in enumerate(_NUMBER_WORDS, 1)},
                **{str(n): w for n, w in enumerate(_NUMBER_WORDS, 1)}}
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


class DraftError(Exception):
    """The model gave no usable draft after one retry."""

    def __init__(self, message: str = "The model did not return a usable draft. "
                                      "Run /launch again, or try a larger model."):
        super().__init__(message)


def _in_facts(phrase: str, facts: str) -> bool:
    forms = {phrase, phrase[:-1]} if phrase.endswith("s") and len(phrase) > 2 else {phrase}
    if phrase in _SAME_NUMBER:
        forms.add(_SAME_NUMBER[phrase])
    return any(re.search(r"(?<!\w)" + re.escape(f) + r"(?!\w)", facts) for f in forms)


def _details(text: str) -> list[str]:
    """The number, time and effort phrases in the text, lowercased, in order, once each; links ignored."""
    found: list[str] = []
    for m in _DETAIL_RE.finditer(_URL_RE.sub(" ", text or "")):
        if m.group(0).lower() not in found:
            found.append(m.group(0).lower())
    return found


def unsupported_details(text: str, facts: str) -> list[Failure]:
    """One "unsupported-detail" Failure when the text has numbers or time and effort
    phrases ("took a minute", "one tap", "instantly") that the facts do not contain
    (case-insensitive); [] otherwise. Links are ignored. The message and the fix
    list the phrases (at most five)."""
    facts_l = (facts or "").lower()
    found = [p for p in _details(text) if not _in_facts(p, facts_l)]
    if not found:
        return []
    listed = ", ".join(f'"{p[:20]}"' for p in found[:_UNSUPPORTED_MAX])
    return [Failure(UNSUPPORTED, f"The post adds details that are not in the facts: {listed}.",
                    f"Remove {listed}, or replace with something from FACTS.",
                    PHRASE_SEP.join(p[:20] for p in found[:_UNSUPPORTED_MAX]))]


def _stems(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z0-9]+", (text or "").lower().replace("\u2019", "'")):
        if len(w) < 3 or w in STOPWORDS:
            continue
        out.add(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w)
    return out


def use_case_carried(use_case: str, caption: str) -> bool:
    """True when the caption has at least 40% of the use case's content words (stopwords
    out, a plural "s" dropped). Lexical and Latin-script only: words are [a-z0-9] runs, so
    a faithful paraphrase can miss, and text in other scripts has no words and passes."""
    words = _stems(use_case)
    return not words or len(words & _stems(caption)) >= _CARRIED * len(words)


def site_details(text: str, brief: Brief, brand: BrandKit) -> list[str]:
    """The detail phrases in the text that only the site's own description supplies (not the
    founder's brief), so the checklist can say where they came from."""
    tagline = (brand.tagline or "").lower()
    given = " ".join((brand.name or "", brief.what or "", brief.detail or "", brief.product_url or "")).lower()
    return [p for p in _details(text) if _in_facts(p, tagline) and not _in_facts(p, given)]


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
        "first-line-short": "Every hook and the caption's first line: 12 words or fewer. Count them.",
        "stock-phrase": "No hype words. Never write: " + ", ".join(
            p for p in STOCK_PHRASES + _ALSO_AVOID if p not in _SAME_AS_LISTED) + ".",
        "template-opener": "Never open with \"Your ... should\".",
        "private-link": None,  # the link is the founder's; the model only copies it
        "em-dash": "No long dash (—); use a full stop or a comma.",
        "emoji": "No emoji, or one at most.",
        "hashtags": "No hashtags.",
        "placeholder": "No brackets [ ] { } or TODO; fill every gap with real words.",
        "link-first-line": f"Put the link {_one_line(url, 120)} once, on the last line. Never in the first line.",
        "length": "The caption is 280 characters or fewer, the link counting as 23; aim for 200.",
        "repeats-first-line": "Each line says something new, no phrase twice. Don't restate the first line.",
        "repeated-phrase": None,  # said in the repeats-first-line line
        "lower-start": None,  # said in rule 1
        "copied-run": "Never copy 5 words in a row from the example posts.",
    }
    lines = []
    for rule in RULES:
        if rule.id in relaxed:
            continue
        if rule.id == "length" and brief.network != "x":
            lines.append("Keep the whole caption under 700 characters.")
            continue
        if words[rule.id]:
            lines.append(words[rule.id])
    return lines


def _borrow(refs: Sequence[Reference] | None) -> str:
    """The shape rule: borrow the top reference's structure, or plain short lines without one.
    None is the redraft, which shows no references: keep the last draft's shape."""
    if refs is None:
        return "Keep the shape of your last draft."
    if not refs:
        return "Use 2 to 4 short lines, with a blank line between them."
    top = refs[0]
    return (f"Borrow the shape of post 1 ({_one_line(top.brand, BRAND_CHARS) or 'a brand'}: {shape_note(top.caption)}): "
            "its structure, never its words. Short lines, a blank line between them.")


def _instructions(brief: Brief, relaxed: set[str], refs: Sequence[Reference] | None = ()) -> str:
    network = _NETWORK.get(brief.network, brief.network)
    rules = [
        "Write like the founder talking to a friend: plain short sentences, I or we"
        + ("." if "lower-start" in relaxed else ", a capital at each line start."),
        "Say what the reader can do now; make it concrete with the use case. One line may say why it "
        "matters, if it adds no new fact.",
        "Every detail comes from FACTS: never add pets, people, names, times, steps, counts, results or "
        "UI actions it lacks, nor what anyone did beyond it.",
        "No exclamation marks. No questions to the reader unless a hook pattern asks for one. No \"it's not just X, it's Y\" lines.",
        _borrow(refs),
        *_rule_lines(brief, relaxed),
    ]
    if brief.detail:
        rules.append("Hook 1 is the founder's detail, word for word. Other lines say what shipped "
                     "and do not restate it.")
    numbered = "\n".join(f"{i}. {r}" for i, r in enumerate(rules, 1))
    return (
        f"You write one launch post for {network}.\n\n"
        f"Rules:\n{numbered}\n\n"
        "Reply with JSON only, no other text:\n"
        '{"use_case": "one concrete use", "hooks": ["first line A", "first line B", "first line C"], '
        '"caption": "the whole post", "card_line": "..."}\n'
        "- use_case: one concrete use that names what shipped, from FACTS only, never the founder's detail. "
        "Nothing beyond FACTS. A later line says it, never the words \"use case\".\n"
        "- hooks: three first lines, each built differently: hook 1 on pattern A, hook 2 on pattern B, "
        "hook 3 your own. One \"we built\" at most.\n"
        "- caption: the whole post, starting with your best hook as its first line. "
        "Use \\n for line breaks.\n"
        "- card_line: the product name and what shipped, 9 words at most, from FACTS. Not a post line."
    )


def _facts(brief: Brief, brand: BrandKit, short: bool = False) -> str:
    """The FACTS block; `short` (a redraft far over budget) cuts the long fields sooner."""
    long_ = 150 if short else 300
    lines = [f"Product: {_one_line(brand.name, 80)}"]
    if brand.tagline:
        lines.append(f"What it is: {_one_line(brand.tagline, 100 if short else 200)}")
    lines.append(f"What just shipped ({brief.shipped}): {_one_line(brief.what, long_)}")
    if brief.detail:
        lines.append(f"A true detail from the founder: {_one_line(brief.detail, long_)}")
    lines.append(f"Link: {_one_line(brief.product_url, 120)}")
    return "FACTS\n" + "\n".join(lines)


def _voice(brand: BrandKit) -> str:
    samples = [s for s in brand.voice_samples if s and s.strip()][:VOICE_COUNT]
    if not samples:
        return "THE FOUNDER'S VOICE\nNo past posts. Sound like a builder telling a peer: warm, specific, a little dry."
    body = "\n".join(f"Post {i}:\n{_indent(_trim(s, VOICE_CHARS))}" for i, s in enumerate(samples, 1))
    return ("THE FOUNDER'S VOICE (tone only; its details are not facts about this launch)\n" + body)


def _references(refs: Sequence[Reference]) -> str:
    if not refs:
        return ""
    body = "\n".join(f"{i}. {_one_line(r.brand, BRAND_CHARS) or 'A brand'} ({shape_note(r.caption)}):\n"
                     f"{_indent(_trim(r.caption, REF_CHARS))}"
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
    return _instructions(brief, relaxed, refs), "\n\n".join(p for p in parts if p)


def build_redraft_prompt(brief: Brief, brand: BrandKit, refs: Sequence[Reference],
                         shapes: Sequence[HookShape], relaxed: set[str],
                         hooks: Sequence[str], caption: str,
                         failures: Sequence[Failure], use_case: str = "") -> tuple[str, str]:
    """(instructions, input) for the one redraft. The previous draft carries the shape,
    so the references and the voice block are left out; the facts and hook patterns
    stay (far over budget, the hook patterns go and the last hooks are cut short). Fix
    lines are grouped: one line per rule and fix, naming every place it failed
    ("- (em-dash, hook2:em-dash: <fix>)"). Under 6,000 characters together."""
    instructions = _instructions(brief, relaxed, None)
    groups: dict[tuple[str, str], list[str]] = {}
    for f in failures:
        base = f.rule_id.split(":", 1)[-1]
        if base in MODEL_CANNOT_FIX:
            continue
        groups.setdefault((base, f.fix), []).append(f.rule_id)
    fixes = "\n".join(f"- ({', '.join(ids)}: {fix})" for (_, fix), ids in groups.items())
    tail = f"\n\nYour draft broke these rules. Fix exactly these:\n{fixes}\nKeep everything else the same."

    def last_draft(limit: int) -> str:
        return "\n".join([f"Use case: {_one_line(use_case, min(limit, USE_CASE_CHARS))}"]
                         + [f"Hook {n}: {_one_line(h, limit)}" for n, h in enumerate(hooks, 1)])

    previous = last_draft(HOOK_CHARS)
    head = "\n\n".join(p for p in (_facts(brief, brand), _shapes(shapes, when=False)) if p)
    room = PROMPT_BUDGET - len(instructions) - len(head) - len(tail) - len(previous) - 40
    if room < REDRAFT_CAPTION_MIN:  # far over budget: shorter last-draft lines, no hook patterns
        previous, head = last_draft(REDRAFT_SHORT_LINE), _facts(brief, brand, short=True)
        room = PROMPT_BUDGET - len(instructions) - len(head) - len(tail) - len(previous) - 40
    cap = _trim(caption, max(REDRAFT_CAPTION_MIN, min(REDRAFT_CAPTION_CHARS, room)))
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


def parse_draft(reply: dict | str | None) -> tuple[list[str], str, str, str] | None:
    """(hooks, caption, use_case, card_line) from a dict, a JSON string, or JSON in a ```
    fence; None if the shape is wrong (hooks not exactly 3 non-empty strings, caption or
    use_case missing or empty). A missing or non-string card_line is "": card_line() then
    falls back."""
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
    use_case = data.get("use_case")
    if not isinstance(use_case, str) or not use_case.strip():
        return None
    card = data.get("card_line")
    card = " ".join(card.split()) if isinstance(card, str) else ""
    return [h.strip() for h in hooks], caption.strip(), use_case.strip(), card


# Errors a model call can raise at runtime (provider, network, decoding). Anything
# else, such as an AttributeError, is a bug and propagates.
_CALL_ERRORS = (RuntimeError, ValueError, OSError)


class _Asker:
    def __init__(self, host: Host):
        self.host = host
        self.last_error: BaseException | None = None

    def __call__(self, instructions: str, text: str) -> tuple[list[str], str, str, str] | None:
        try:
            reply = self.host.complete_json(instructions, text, DRAFT_SCHEMA)
        except _CALL_ERRORS as exc:
            self.last_error = exc
            return None
        return parse_draft(reply)


def outside_captions(refs: Sequence[Reference]) -> list[str]:
    """The captions the copied-run check compares against: outside references only. The
    founder's own past launch (a reference with sign-ups) is theirs to reuse (R40)."""
    return [r.caption for r in refs if r.signups is None]


def own_words(brief: Brief) -> tuple[str, ...]:
    """The founder's words for this launch, which never count as copied (R40)."""
    return (brief.detail,) if brief.detail else ()


def _failures(hooks: Sequence[str], caption: str, use_case: str, brief: Brief,
              refs: Sequence[Reference], voice: Sequence[str], facts: str, product: str = "") -> list[Failure]:
    """Everything the model is asked to fix. A private link that is the founder's own link is
    left out: the model only copies it, and the checklist shows it."""
    captions = outside_captions(refs)
    found = list(check(caption, network=brief.network, reference_captions=captions, voice_samples=voice,
                       own_words=own_words(brief), product=product))
    if is_private_link(brief.product_url):
        found = [f for f in found if f.rule_id != "private-link"]
    found += unsupported_details(caption, facts)
    if not use_case_carried(use_case, "\n".join(caption.split("\n")[1:])):  # every hook replaces line 1
        found.append(Failure(USE_CASE_MISSING, "The caption does not say the use case after its first line.",
                             "Say the use case in a line after the first."))
    found += [Failure(f"use-case:{f.rule_id}", f"Use case: {f.message}", f.fix, f.found)
              for f in unsupported_details(use_case, facts)]
    for n, hook in enumerate(hooks, 1):
        hook_fails = [f for f in check(hook, network=brief.network, reference_captions=captions,
                                       voice_samples=voice, own_words=own_words(brief))
                      if f.rule_id in HOOK_RULES]
        hook_fails += unsupported_details(hook, facts)
        found += [Failure(f"hook{n}:{f.rule_id}", f"Hook {n}: {f.message}", f.fix, f.found) for f in hook_fails]
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
    hooks, caption, use_case, card = first
    failures = _failures(hooks, caption, use_case, brief, refs, voice, facts, brand.name)
    if not failures:
        return Draft(tuple(hooks), caption, (), False, use_case, card_line=card)
    r_instructions, r_text = build_redraft_prompt(brief, brand, refs, shapes, relaxed,
                                                  hooks, caption, failures, use_case)
    second = ask(r_instructions, r_text)
    if second is not None:
        hooks2, caption2, use_case2, card2 = second
        failures2 = _failures(hooks2, caption2, use_case2, brief, refs, voice, facts, brand.name)
        if len(failures2) <= len(failures):
            return Draft(tuple(hooks2), caption2, tuple(failures2), True, use_case2, tuple(failures), card2)
    return Draft(tuple(hooks), caption, tuple(failures), False, use_case, tuple(failures), card)


# ------------------------------------------------------------------ the card line

CARD_LINE_WORDS = 9
_CARD_RULES = frozenset({"stock-phrase", "template-opener", "placeholder", "link-first-line", "private-link",
                         "lower-start"})
_CLAUSE_RE = re.compile(r"(?<=[:;,.!?])\s+|\s+[\u2013\u2014-]\s+")


def _card_ok(line: str, first: str, facts: str, product: str = "") -> bool:
    """A card line that is short, plain, from the facts and not a restatement of line 1."""
    if not line or first_line_words(line) > CARD_LINE_WORDS or _same(line, first):
        return False
    if ({f.rule_id for f in check(line, network="x", product=product)} & _CARD_RULES
            or unsupported_details(line, facts)):
        return False
    a, b = _stems(first), _stems(line)
    return not (a and b and len(a & b) >= 0.6 * min(len(a), len(b)))


def _cap_first(text: str) -> str:
    first = text.split(" ", 1)[0]
    if any(c.isupper() for c in first[1:]) or "." in first.rstrip("."):
        return text  # iOS, posts.design stay as written
    return text[:1].upper() + text[1:]


# A card line never ends on one of these, or on a number: it would stop mid-phrase.
_BAD_END = frozenset(
    "a an the and or but nor so yet to of in on at for with from by into onto about over under via per "
    "as than then your our their its my".split()
)


def _ends_well(text: str) -> bool:
    last = text.split()[-1].lower().strip(":;,.!?") if text.split() else ""
    return bool(last) and last not in _BAD_END and last not in _NUMBER_WORDS and not last[0].isdigit()


def _product_line(brief: Brief, brand: BrandKit) -> str:
    """The product name and what shipped, from the brief, ending at a natural break: whole
    clauses only ("Fernlight: photo diagnosis, snap a leaf"), at most CARD_LINE_WORDS words,
    never ending on a number or a small word. When only the name fits ("Brookhour 2.0:" then a
    long clause), or nothing ends well, a short "<name> is live." line."""
    name = " ".join((brand.name or "").split())
    clauses = [c.strip(" :;,.") for c in _CLAUSE_RE.split(" ".join((brief.what or "").split())) if c.strip(" :;,.")]
    live = f"{name} is live." if name else ""
    if not clauses:
        return live
    named = bool(name) and name.lower() in clauses[0].lower()
    text = clauses[0] if named or not name else f"{name}: {clauses[0]}"
    only_name = named and len(clauses[0].split()) <= 2  # "Brookhour 2.0": its version may end it
    if len(text.split()) > CARD_LINE_WORDS or not (only_name or _ends_well(text)):
        return live or ""
    for clause in clauses[1:]:
        candidate = text + (": " if only_name and text == clauses[0] else ", ") + clause
        if len(candidate.split()) > CARD_LINE_WORDS or not _ends_well(candidate):
            break
        text = candidate
    if only_name and text == clauses[0]:
        return f"{text} is live."
    return _cap_first(text)


def card_line(candidate: str, post: str, brief: Brief, brand: BrandKit, use_case: str = "") -> str:
    """The card headline: the model's card line when it passes (9 words or fewer, no stock
    phrase, template opener, placeholder or link, no detail outside FACTS, not mostly the
    words of line 1), else the post's what-shipped line (a line after the first that says the
    use case) when it passes the same, else the product name and what shipped from the brief.
    Never the same sentence as line 1."""
    facts = facts_text(brief, brand)
    lines = [ln.strip() for ln in post.strip().split("\n")]
    first = lines[0] if lines else ""
    body = [ln for ln in lines[1:] if ln and not _URL_RE.fullmatch(ln)]
    shipped = [ln for ln in body if use_case and use_case_carried(use_case, ln)] or body
    for line in [" ".join((candidate or "").split()), *shipped]:
        if _card_ok(line, first, facts, brand.name):
            return line
    fallback = _product_line(brief, brand)
    return fallback if fallback and not _same(fallback, first) else " ".join((brand.name or "").split())


# ------------------------------------------------------------------ the hooks offered

_END_RE = re.compile(r"[.!?\u2026][\"'\u2019\u201d)\]]*$")


def founder_hook(detail: str) -> str:
    """The founder's detail as a first line: their words, trimmed, the first letter capitalised
    and a full stop added when it has no end punctuation. "" without a detail."""
    text = " ".join((detail or "").split())
    if not text:
        return ""
    first = text.split(" ", 1)[0].rstrip(".,;:!?")
    if not (any(c.isupper() for c in first[1:]) or "." in first):  # keep iOS, pawr.link as written
        text = text[:1].upper() + text[1:]
    return text if _END_RE.search(text) else text + "."


def post_failures(text: str, brief: Brief, brand: BrandKit, refs: Sequence[Reference]) -> list[Failure]:
    """The scroll test and unsupported_details() for a post as it will be posted."""
    found = check(text, network=brief.network, reference_captions=outside_captions(refs),
                  voice_samples=brand.voice_samples, own_words=own_words(brief), product=brand.name)
    return found + unsupported_details(text, facts_text(brief, brand))


def with_first_line(caption: str, hook: str) -> str:
    """The caption with its first line replaced by the hook."""
    lines = caption.strip().split("\n")
    lines[0] = hook.strip()
    return "\n".join(lines).strip()


def _drop_repeats(post: str, product: str = "") -> tuple[str, tuple[str, ...]]:
    """Take out the body lines that repeat the first line (most of it, or three of its words);
    a link on such a line stays, alone."""
    gone = set(echo_lines(post, product))
    if not gone:
        return post, ()
    kept, dropped = [], []
    for i, line in enumerate(post.split("\n")):
        if i not in gone:
            kept.append(line)
            continue
        dropped.append(" ".join(_URL_RE.sub(" ", line).split()))
        links = _URL_RE.findall(line)
        if links:
            kept.append(" ".join(links))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip(), tuple(dropped)


def same_line(a: str, b: str) -> bool:
    """The same line, ignoring case, spacing and end punctuation."""
    return _same(a, b)


def _same(a: str, b: str) -> bool:
    def key(s: str) -> str:
        return " ".join(s.lower().split()).rstrip(" .!?\u2026")
    return key(a) == key(b)


def _keep_what_shipped(post: str, draft: Draft, brief: Brief, brand: BrandKit,
                       refs: Sequence[Reference]) -> str:
    """The founder's words replace the caption's first line, and that line can be the only one
    that said what shipped. Then the model's own first line (checked with the draft) goes back in
    as line 2, if the post then says what shipped and fails nothing it did not fail before."""
    lines = post.strip().split("\n")
    if not draft.use_case or use_case_carried(draft.use_case, "\n".join(lines[1:])):
        return post
    model_first = draft.caption.strip().split("\n")[0].strip()
    if not model_first or _same(model_first, lines[0]):
        return post
    candidate = re.sub(r"\n{3,}", "\n\n", "\n".join([lines[0], "", model_first, "", *lines[1:]])).strip()
    if not use_case_carried(draft.use_case, "\n".join(candidate.split("\n")[1:])):
        return post
    before = {(f.rule_id, f.found) for f in post_failures(post, brief, brand, refs)}
    after = {(f.rule_id, f.found) for f in post_failures(candidate, brief, brand, refs)}
    return candidate if after <= before else post


def kept_from_style(text: str, brief: Brief, brand: BrandKit) -> set[str]:
    """The relaxed rules (from the founder's voice samples) that this post actually breaks: the
    checklist says these were kept from their style."""
    fired = {f.rule_id for f in check(text, network=brief.network, product=brand.name)}
    return relaxed_rules(brand.voice_samples) & fired


def hook_options(draft: Draft, brief: Brief, brand: BrandKit, refs: Sequence[Reference]) -> list[HookOption]:
    """The hooks to offer, each with the post it makes. With a founder's detail, option 1 is
    their words (always offered; a body line that would repeat them is dropped) and the model's
    hooks fill two more places; without one, up to three model hooks. A model hook is offered
    only when, as the first line of the post, it adds no failure of its own (one the body alone
    does not have, compared by rule and by what was caught), and only when no hook already
    offered has the same hook_shape(). If none passes and there is no founder hook, the one
    with the fewest failures is offered, flagged."""
    options: list[HookOption] = []
    words = founder_hook(brief.detail)
    if words:
        post, dropped = _drop_repeats(with_first_line(draft.caption, words), brand.name)
        post = _keep_what_shipped(post, draft, brief, brand, refs)
        options.append(HookOption(words, "founder", post, dropped))
    lines = draft.caption.strip().split("\n")
    body = {(f.rule_id, f.found) for f in post_failures("\n" + "\n".join(lines[1:]), brief, brand, refs)}
    scored: list[tuple[int, str, str]] = []
    for hook in draft.hooks:
        if any(_same(hook, o.text) for o in options) or any(_same(hook, h) for _, h, _ in scored):
            continue
        post = with_first_line(draft.caption, hook)
        extra = {(f.rule_id, f.found) for f in post_failures(post, brief, brand, refs)} - body
        scored.append((len(extra), hook, post))
    passing: list[tuple[str, str]] = []
    shapes = {hook_shape(o.text) for o in options} - {""}
    for n, hook, post in scored:
        if n or len(options) + len(passing) == 3 or hook_shape(hook) in shapes:
            continue
        passing.append((hook, post))
        shapes |= {hook_shape(hook)} - {""}
    if not passing and not options and scored:
        _, hook, post = min(scored, key=lambda s: s[0])
        return [HookOption(hook, "model", post, flagged=True)]
    return options + [HookOption(hook, "model", post) for hook, post in passing]


_SHAPES: tuple[tuple[str, re.Pattern], ...] = (
    ("we-built", re.compile(r"\b(?:we|i)(?:'ve|\u2019ve|'re| just| have)? "
                            r"(?:built|made|shipped|added|launched|launching)\b", re.I)),
    ("call-out", re.compile(r"^are you\b", re.I)),
    ("you-asked", re.compile(r"^you asked\b", re.I)),
    ("every", re.compile(r"^every\b", re.I)),
    ("now", re.compile(r"\bcan now\b|\bnow (?:you|your)\b", re.I)),
)


def hook_shape(hook: str) -> str:
    """A name for a hook's build ("we-built", "call-out", ...) when it has a recognisable one,
    else "". Two hooks offered together never share one."""
    return next((name for name, pattern in _SHAPES if pattern.search(hook)), "")


# ------------------------------------------------------------------ the visible checklist

def _quoted(f: Failure) -> str:
    return f'"{f.found}"' if f.found else ""


def quote_phrases(f: Failure) -> str:
    """What a failure caught, in quotes; each phrase of an unsupported-detail on its own."""
    parts = f.found.split(PHRASE_SEP) if f.rule_id.endswith(UNSUPPORTED) else [f.found]
    return ", ".join(f'"{p}"' for p in parts if p)


def checklist(text: str, failures: Sequence[Failure], network: str,
              use_case: str = "", from_site: Sequence[str] = (), kept: Iterable[str] = ()) -> list[CheckLine]:
    """The post's checks as lines for the founder, one per rule (the three style rules share
    one), pass or fail, from the failures post_failures() found. Given the draft's use case (the
    wizard passes it when the founder's words lead, since they replace the caption's first line),
    a post whose lines after the first no longer say it gets one more, failing line. `kept` are
    relaxed rules the post breaks (kept_from_style()); their lines say so. Composed here, never by
    the model."""
    kept = set(kept)
    by_id: dict[str, Failure] = {}
    for f in failures:
        by_id.setdefault(f.rule_id, f)

    def line(rules: tuple[str, ...], ok_text: str, fail_text: Callable[[Failure], str]) -> CheckLine:
        bad = next((by_id[r] for r in rules if r in by_id), None)
        return CheckLine(rules, bad is None, ok_text if bad is None else fail_text(bad))

    words = first_line_words(text)
    dashes, emoji, tags = style_counts(text)
    style = f"em-dashes {dashes}, emoji {emoji}, hashtags {tags}"
    length = x_length(text)
    out = [
        line(("first-line-short",), f"First line: {words} words", lambda f: f"First line: {words} words, over 12"),
        line(("stock-phrase",), "No stock launch phrases", lambda f: f"Stock phrase: {_quoted(f)}".rstrip(": ")),
        line(("template-opener",), "Doesn't open with \"Your X should\"",
             lambda f: f"Opens with the \"Your X should\" template: {_quoted(f)}".rstrip(": ")),
        line(("em-dash", "emoji", "hashtags"), style[:1].upper() + style[1:]
             + (" (kept from your style)" if kept & {"em-dash", "emoji", "hashtags"} else ""),
             lambda f: f"Too many: {style}"),
        line(("placeholder", "lower-start"), "No placeholders; " + (
            "lower-case starts kept from your style" if "lower-start" in kept else "every line starts with a capital"),
             lambda f: f"Placeholder: {_quoted(f)}".rstrip(": ") if f.rule_id == "placeholder"
             else f"Line starts lower case: {_quoted(f)}".rstrip(": ")),
        line(("link-first-line",), "No link in the first line", lambda f: "Link in the first line"),
        line(("private-link",), "Link is public" if has_link(text) else "No private links",
             lambda f: f"Link only opens on your own machine or network: {f.found}".rstrip(": ")),
        line(("length",), f"Fits X: {length}/280" if network == "x" else f"Length: {len(text)} characters",
             lambda f: f"Too long for X: {length}/280"),
        line(("copied-run",), "No five words copied from the references",
             lambda f: f"Copies five words from a reference: {_quoted(f)}".rstrip(": ")),
        line(("repeats-first-line", "repeated-phrase"), "Each line says something new",
             lambda f: "A later line repeats the first" if f.rule_id == "repeats-first-line"
             else f"Two lines repeat {_quoted(f)}"),
        line((UNSUPPORTED,), "Only details you gave" + (
            " or your site says (" + ", ".join(f'"{p}"' for p in from_site) + ")" if from_site else ""),
             lambda f: f"Details you didn't give: {quote_phrases(f)}".rstrip(": ")),
    ]
    if use_case and not use_case_carried(use_case, "\n".join(text.split("\n")[1:])):
        out.append(CheckLine((USE_CASE_MISSING,), False, "Says what shipped: no line says it now"))
    return out
