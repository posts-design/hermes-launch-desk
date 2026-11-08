"""The five hook shapes and which two to use for each kind of launch."""
from __future__ import annotations


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
    )
}

_BY_KIND: dict[str, tuple[str, str]] = {
    "feature": ("bold-contrast", "audience-call-out"),
    "product": ("identity-news", "bold-contrast"),
    "rebrand": ("time-jump", "bold-contrast"),
    "partnership": ("identity-news", "audience-call-out"),
}


def pick_hook_shapes(shipped: str, detail: str = "") -> list[HookShape]:
    """Two hook shapes for a kind of launch; unknown kinds use the feature pair. The detail no
    longer changes them: when there is one, the founder's own words are hook 1, and a "You asked
    for it" pattern would only echo them (R47)."""
    kind = shipped if shipped in _BY_KIND else "feature"
    first, second = _BY_KIND[kind]
    return [HOOK_SHAPES[first], HOOK_SHAPES[second]]
