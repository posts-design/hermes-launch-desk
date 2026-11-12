# Launch Desk: agent guide

A Hermes plugin that prepares a launch from what posts.design learned: a post in the founder's own
words, a share card, and on request a short film. Other people install it from the Hermes catalog,
and nothing is ever posted for them. `README.md` describes what it does; `plugin.yaml` is what
Hermes shows before install.

What is easy to break here:

- **This repository is public.** Nothing about the server it is developed on, other projects or
  anyone's private data goes into a file or a commit message. Pushing is publishing: push only when
  asked.
- **The promises to users.** `plugin.yaml` and the README ("What goes where") say exactly what is
  sent to posts.design, what is fetched, what is stored and what runs on the user's machine. A
  change to any of that changes both texts in the same commit, and widening it is the owner's
  decision.
- **Releases reach users.** A change in behaviour takes a new `version` in `plugin.yaml`, named in
  the commit subject, for example `(0.2.3)`.
- **The post checks** (README, "What it checks") are code with tests in `tests/`. A new check gets a
  test and a README line.

Tests: `pytest`, configured in `pytest.ini`.
