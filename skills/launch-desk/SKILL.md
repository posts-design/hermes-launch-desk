---
name: launch-desk
description: Use when a founder says they just shipped something (a feature, product, rebrand or partnership) and wants to announce it. Teaches the launch workflow and when to run /launch, which builds a checked launch post pack. Never posts.
---

# Launch Desk

Launch Desk turns "I just shipped X" into a launch pack: three real launch posts from posts.design as references, a draft checked in code, a card built from the founder's own screenshot and brand, a feed preview, and a link to post on X. It never posts anything.

## When to suggest /launch

Suggest it when the user says they shipped, released, launched or renamed something and wants to tell people. Do not wait for them to know the command.

Tell them to send:

    /launch <what shipped> [-- <one real detail>]

For example: `/launch https://yoursite.com dark mode for the editor -- people kept asking for it`

- Add the site link the first time. It is remembered after that.
- The part after ` -- ` is one true detail, like who asked for it or what it replaced. The draft treats it as a fact and uses nothing it cannot find. Ask for one if they have none; a real detail is the best thing a launch post can have.
- `/launch` always takes two steps. The first run ends with the draft, the card and a feed preview. Sending `/launch` again lets them approve, change the hook, swap references or discard.
- `/launch brand <url>` reads the site's colours and logo again. `/launch help` shows the usage.
- `/launch voice <a past post>` keeps one of the founder's own posts as a voice sample (the last three are kept); drafts follow its style. `/launch voice clear` forgets them. It needs a saved brand first: `/launch brand <url>`, or one approved launch.

## Screenshots

A card made from the real product beats any generated image. If the user wants a card with their product on it, ask for a screenshot. When they send one, call `launch_desk_attach_screenshot` with the image's file path (PNG, JPEG or WebP), then tell them to run `/launch`. Without a screenshot the card is text only, which still works.

Never generate or invent an image for the card.

## What you must not do

- Do not write the launch post yourself in place of `/launch`. The command picks references and checks the draft in code; a post written in chat skips every check.
- Never post, schedule or send anything. The founder posts from the pack.
- If the user wants edits, send them back through `/launch` (change the hook) or help them edit the pack's text by hand, keeping to the rules below.

## The launch workflow in short

Use this to steer the founder even when they are not running `/launch`.

1. Pick one viewer: the person who has the problem this solves. Write for them, not for everyone.
2. Hook first. The first line says what changed, plainly, in at most 12 words. No link in it.
3. No hype words: no "excited to announce", "game-changer", "seamless", "unlock", "supercharge". Say what it does.
4. Every detail must be true. No invented numbers, times or effort ("took a minute", "in seconds") unless the founder said so. Read the draft once and cut anything untrue.
5. The image comes from a real screenshot of the product, in the product's own colours. The card should carry the hook even with the caption covered.
6. Judge the launch by sign-ups or downloads per channel, using a tracked link, and by who comes back. Not by views.

The scroll test checks a draft for: a first line over 12 words, stock phrases, more than one em-dash, more than one emoji, more than two hashtags, placeholders like [link], a link in the first line, X length over 280 (each link counts as 23), a copied run of five words from a reference, and details that are not in the facts.

## If posts.design is not set up

`/launch` still works on a built-in sample pack of launch posts, and says so. For real references, the founder runs three commands:

    hermes mcp add posts --url https://posts.design/mcp --auth oauth
    hermes config set plugins.entries.launch-desk.mcp_allowlist '["posts"]'
    hermes mcp login posts

`/launch` names whichever one is missing. A run costs about one posts.design credit; free accounts get 100 a month. Do not ask the user to paste tokens into chat.
