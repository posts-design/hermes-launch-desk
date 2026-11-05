# Title

plugin-catalog: add launch-desk (Launch Desk, by posts.design)

# Description

Adds `plugin-catalog/launch-desk.yaml` for [Launch Desk](https://github.com/posts-design/hermes-launch-desk), pinned to `REPLACE_WITH_SHA`.

## What it does

A founder types what they just shipped:

```
/launch https://yoursite.com dark mode for the editor -- people kept asking for it
```

Launch Desk asks two questions as buttons (what kind of launch, which network), reads the site's colours and logo, finds three real launch posts like it on posts.design (or the bundled sample pack when posts.design isn't set up), drafts the post, and lets the founder pick one of three hooks. The reply shows the draft, a share card and a feed preview. Sending `/launch` again offers Approve, Change hook, Swap references or Discard. Approve writes a pack: the post text, the card, the feed preview, alt text, the references, and a link that opens X with the post filled in. It never posts for you.

## Why it is more than a prompt

- **References are chosen in code.** One posts.design search per run (or the bundled sample pack when posts.design isn't set up); the plugin ranks the results by how closely they match the launch, then by recorded views, and shows the three it used with the reason for each.
- **The scroll test runs in code.** First line of 12 words or fewer with no link, no stock launch phrases, at most one em-dash, one emoji and two hashtags, no placeholders, 280 characters on X with links counted as 23, and no run of five words copied from a reference.
- **Invented details are caught in code.** Numbers and time or effort claims that are not in the founder's own facts are flagged. A failing draft is redrafted once with the failures listed, and the better of the two is kept; anything still failing is shown to the founder, not hidden.
- **The visuals are composed, never generated.** The card is set from the founder's screenshot (attached with the `launch_desk_attach_screenshot` tool), brand colours and logo in bundled Inter, at 1200×675. Without a screenshot it is a typographic card. The feed preview shows the post as it will sit in a feed.
- **Two-step approval.** Nothing is saved as a pack until the founder has seen the card and the preview and chosen Approve. The pending draft expires after 24 hours.
- **Works on small models.** The model writes the hooks and the caption; the code does the rest. The live runs below used deepseek-v4.1-flash.

It also ships a skill, `launch-desk:launch-desk`, so the agent steers founders to `/launch` and the screenshot tool without the command being typed.

## How it was tested

- `hermes plugins validate . --install-deps` (Hermes 0.22.7): `Validation passed.` All checks green, security scan `safe`, declared tools match registrations. One informational line: `Isolation — in-process only: launch_desk/host.py ctx.post_message()`.
- `hermes plugins doctor . --ci`: `OK: runtime discovery, manifest parsing, import, and registration passed`, `registrations: 1 tool(s), 0 hook(s)`.
- `scripts/validate_plugin_catalog.py plugin-catalog/launch-desk.yaml`: `OK: 1 file(s) valid`.
- 263 pytest tests (`pytest --collect-only -q`: 263 tests collected; all pass) on Python 3.14 (stdlib and Pillow only), covering the scroll test, ranking, drafting with junk model output, card layout limits, the two-step flow, unattended runs, and the reply on local clients and chat apps.
- Live runs on a separate Hermes 0.22.7 with deepseek-v4.1-flash and the posts.design MCP server: the interactive CLI (`hermes chat`, both steps, approve and discard) and Telegram through the gateway (buttons for every question, the card and preview sent as images, and a run without a posts.design account on the sample pack). On approve, chat apps get a Markdown link that opens X with the post filled in: verified on Telegram, where it shows as a tap-through link; other chat apps show it as Markdown. Terminal and desktop clients get the plain URL. About the images in the entry: the cards and the feed preview are rendered by the plugin's own code; the CLI image is redrawn from a real terminal capture of the run above; the Telegram image is a real capture from the live Telegram run.
- Under cron or any unattended run, `/launch` answers with one sentence and stops; a question nobody answers ends the run cleanly.

## Disclosure

- posts.design is reached only through the user's own `posts` MCP server (OAuth sign-in through Hermes; `plugins.entries.launch-desk.mcp_allowlist` must list it). Each run makes an access check (`get_my_access`) and one search, about one posts.design credit. The search sends the first 160 characters of what shipped, a short keyword phrase from it, a fixed phrase for the kind of launch and a category.
- Not sent to posts.design: the founder's detail after `--`, the site link, the draft, the brand kit and the screenshot.
- Without the server, it uses a bundled sample pack of launch posts and says so.
- Sent to the model Hermes is configured with: the brief (what shipped and the detail), the product name, tagline and link, the reference captions, and any voice samples.
- Fetched: the product site, its stylesheets and the logo it links to, at most 4 requests, each following up to 3 redirects.
- Stored locally, in the plugin data folder and Hermes plugin state: the brand kit with any voice samples; a pending draft with its card, preview and search results, kept up to 24 hours or until approve or discard; finished packs; the path of an attached screenshot (the file is not copied). Copies of the card and preview go to Hermes' image cache so chat apps can show them.
- Nothing is posted, scheduled or sent to any social network. No credentials are read, no shell commands are run, no background work, no telemetry.

## Tier

The entry is submitted as `tier: community`. We would like to ask the maintainers to consider `verified`: Launch Desk is built and maintained by posts.design itself. The repository is under the posts-design GitHub organisation, and the MCP server it integrates is posts.design's own, at https://posts.design/mcp. If you grant `verified`, we would also welcome `onboarding: true`.

Maintainer: posts.design (github.com/posts-design).
