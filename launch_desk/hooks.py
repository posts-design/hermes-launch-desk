"""The six hook shapes and which two to use for each kind of launch."""
from __future__ import annotations

import re

from .models import HookShape

HOOK_SHAPES: dict[str, HookShape] = {
    h.id: h
    for h in (
        HookShape("identity-news", "Who I am and what just happened",
                  "A founder or builder showing something new, or announcing a raise or a launch.",
                  "I'm [name] from [company], and today we [the news]."),
        HookShape("time-jump", "Two weeks ago… now",
                  "Progress is the story: a ship update, a rebuild, a fast turnaround.",
                  "[N] weeks ago, [where it started]. Now, [what exists], and here it is."),
        HookShape("audience-call-out", "Are you a ___? Stop",
                  "A narrow buyer you can name. Smaller reach, better fit.",
                  "Are you a [specific role]? Stop [the painful thing they do]."),
        HookShape("demo-promise", "A constraint stated up front",
                  "The product acts live and the constraint is surprising.",
                  "Watch me [do the task] with [the constraint: no hands, one prompt, no cuts]."),
        HookShape("bold-contrast", "A claim against the usual way",
                  "A crowded category where your difference is clear.",
                  "Every [category] does [the usual way]. [Product] does [the different way]."),
        HookShape("you-asked", "You asked, it's here",
                  "Only when the founder's detail says people asked for it or requested it.",
                  "You asked for [the thing]. It's here."),
    )
}

_BY_KIND: dict[str, tuple[str, str]] = {
    "feature": ("bold-contrast", "audience-call-out"),
    "product": ("identity-news", "bold-contrast"),
    "rebrand": ("time-jump", "bold-contrast"),
    "partnership": ("identity-news", "audience-call-out"),
}


# The founder's detail says people asked for it: "asked", "a top request", "everyone wanted".
_ASKED_RE = re.compile(r"\b(?:ask(?:s|ed|ing)?|request(?:s|ed)?|wanted)\b", re.IGNORECASE)


def pick_hook_shapes(shipped: str, detail: str = "") -> list[HookShape]:
    """Two hook shapes for a kind of launch; unknown kinds use the feature pair. A feature
    whose detail says people asked for it gets you-asked in place of audience-call-out."""
    kind = shipped if shipped in _BY_KIND else "feature"
    first, second = _BY_KIND[kind]
    if kind == "feature" and _ASKED_RE.search(detail or ""):
        second = "you-asked"
    return [HOOK_SHAPES[first], HOOK_SHAPES[second]]
