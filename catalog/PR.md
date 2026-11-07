# Title

plugin-catalog: add launch-desk (Launch Desk, by posts.design)

# Description

Adds `plugin-catalog/launch-desk.yaml` for [Launch Desk](https://github.com/posts-design/hermes-launch-desk), pinned to `REPLACE_WITH_SHA`.

## What it does

A founder types what they just shipped:

```
/launch https://yoursite.com dark mode for the editor -- people kept asking for it
```

Launch Desk asks two questions as buttons (what kind of launch, which network), reads the site's colours and logo, and finds three real launch posts like it on posts.design (or the bundled sample pack when posts.design isn't set up). Each reference comes with a shape note that says what it lends, such as `opens with what you "can now" do, 2 lines`. The plugin drafts the post in the shape of the top reference, and the founder picks the hook. Hook 1 is the founder's own detail, word for word, marked "(your words)"; the model's hooks fill the other places, and only ones that pass the checks with the rest of the post are offered. The reply shows the draft, a share card and a feed preview. The card has three layouts: hero (the headline with one screenshot), split (two screenshots side by side) and statement (with no screenshot, the hook set large as a quote, with the brand). Sending `/launch` again offers Approve, Change hook, Swap references or Discard. Approve writes a pack: the post text, the card, the feed preview, alt text for the card's layout, the references, and a link that opens X with the post filled in. It never posts for you.

After approve, it asks whether to check in a day and three days later. Only on Yes, it creates two check-ins on Hermes' own scheduler. Each asks how many sign-ups each channel brought, suggests a next post from the pack, and records the numbers the founder replies with.

## Why it is more than a prompt

- **References are chosen in code.** One posts.design search per run (or the bundled sample pack when posts.design isn't set up). The plugin ranks the results by how closely they match the launch, then by recorded views, and shows the three it used with the reason for each.
- **Shape notes are read in code.** Each reference's caption is measured for how it opens, its line count and length, and whether it has a list or ends on a link. The founder sees that note, and the draft prompt gets the top reference's shape, not its words.
- **The founder's words lead.** The detail after `--` is hook 1 verbatim, never reworded by the model. If a line of the draft would repeat it, that line is dropped for that hook and the question says so.
- **The scroll test runs in code.** First line of 12 words or fewer with no link, no stock launch phrases (including "check this out"), no "Your X should" opener, at most one em-dash, one emoji and two hashtags, no placeholders, no link that only opens on the founder's machine (localhost, loopback, private, link-local or .local, checked with `ipaddress`), 280 characters on X with links counted as 23, no line that just restates the first, and no run of five words copied from a reference.
- **The checks are visible.** After a redraft, one line says what the first draft was caught on and quotes the phrase where it can, such as a stock "Check this out" or a `127.0.0.1` link. The draft arrives with a checklist of the post as it will be posted, one line per check with a pass or fail mark, composed in code and saved in the pack.
- **Invented details are caught in code.** Numbers and time or effort claims that are not in the founder's own facts are flagged. A failing draft is redrafted once with the failures listed, and the better of the two is kept. Anything still failing shows as a failed line in the checklist, not hidden.
- **The visuals are composed, never generated.** The card is set in bundled Inter at 1200×675 from the founder's own screenshots (attached with the `launch_desk_attach_screenshot` tool), brand colours and logo. The layout is picked in code from the screenshots that open: two make a split card, one a hero card, none a statement card. The feed preview shows the post as it will sit in a feed.
- **Two-step approval.** Nothing is saved as a pack until the founder has seen the card and the preview and chosen Approve. The pending draft expires after 24 hours.
- **Check-ins are opt-in and composed in code.** They run only after a Yes, as two ordinary jobs on Hermes' own scheduler, with the plugin's own tools and no MCP server. The check-in message is put together in code from the pack and passes the same copy checks; the job's agent posts it as it is. Sign-ups the founder reports are recorded in the pack with `launch_desk_record_results`.
- **Works on small models.** The model writes the hooks and the caption; the code does the rest. The live runs below used deepseek-v4.1-flash.

It also ships a skill, `launch-desk:launch-desk`, so the agent steers founders to `/launch` and the screenshot tool without the command being typed.

## How it was tested

- `hermes plugins validate . --install-deps` (Hermes 0.22.7): `Validation passed.` All checks green, security scan `safe`, declared tools match registrations. One informational line: `Isolation — in-process only: launch_desk/host.py ctx.post_message()`.
- `hermes plugins doctor . --ci`: `OK: runtime discovery, manifest parsing, import, and registration passed`, `registrations: 3 tool(s), 0 hook(s)`.
- `scripts/validate_plugin_catalog.py plugin-catalog/launch-desk.yaml`: `OK: 1 file(s) valid`.
- 557 pytest tests (`pytest --collect-only -q`: 557 tests collected; all pass) on Python 3.14 (stdlib and Pillow only). They cover the scroll test (including the template opener and private links), the founder words as hook 1 and the hook filter, the visible checklist, ranking and shape notes, drafting with junk model output, the three card layouts and their size limits, alt text per layout, the two-step flow, unattended runs, the reply on local clients and chat apps, and the check-ins (no job without a Yes, the job specs, the composed check-in message against the copy checks, and result recording).
- Live runs on a separate Hermes 0.22.7 with deepseek-v4.1-flash and the posts.design MCP server: the interactive CLI (`hermes chat`, both steps, approve and discard), the check-ins (created on Yes, run now, message posted as composed, results recorded), and Telegram through the gateway (buttons for every question, the card and preview sent as images, and a run without a posts.design account on the sample pack). On approve, chat apps get a Markdown link that opens X with the post filled in. This is verified on Telegram, where it shows as a tap-through link; other chat apps show it as Markdown. Terminal and desktop clients get the plain URL.
- About the images in the entry: the three cards and the feed preview are rendered by the plugin's own code (`tools/render_assets.py`, with the post from a live run). The CLI image is redrawn from a real terminal capture of a live run. The Telegram image is a real capture from the live Telegram run.
- Under cron or any unattended run, `/launch` answers with one sentence and stops; a question nobody answers ends the run cleanly.

## Disclosure

- posts.design is reached only through the user's own `posts` MCP server (OAuth sign-in through Hermes; `plugins.entries.launch-desk.mcp_allowlist` must list it). Each run makes an access check (`get_my_access`) and one search, about one posts.design credit. The search sends the first 160 characters of what shipped, a short keyword phrase from it, a fixed phrase for the kind of launch and a category.
- Not sent to posts.design: the founder's detail after `--`, the site link, the draft, the brand kit and the screenshots.
- Without the server, it uses a bundled sample pack of launch posts and says so.
- Sent to the model Hermes is configured with, which may be a hosted provider: the brief (what shipped and the detail), the product name, tagline and link, the reference captions, and any voice samples. A check-in also sends it the pack it reads: the post, its X link, the unused hooks, the detail, the brand, link and shape of references the founder did not see, and any sign-ups recorded.
- Fetched: the product site, its stylesheets and the logo it links to, at most 4 requests, each following up to 3 redirects.
- Stored locally, in the plugin data folder and Hermes plugin state:
  - the brand kit (name, site link, tagline, colours, font name, logo file and voice samples);
  - a pending draft with its card, its preview and the search results it came from, kept up to 24 hours or until approve or discard;
  - finished packs, kept until the founder deletes them, with the sign-ups per channel reported after a check-in (the last 50);
  - copies of the card and preview in Hermes' image cache, so chat apps can show them (Hermes clears them after about a day);
  - the paths of up to two attached screenshots, until the draft that used them is approved or discarded. The files are not copied.
- Scheduled, only on the founder's Yes after approve: two check-ins (a day and three days later), created as ordinary Hermes cron jobs through Hermes' own `cronjob_manage` tool. They run with the plugin's own tools and no MCP server, post to the same chat, and show in `hermes cron list`. Nothing else is scheduled.
- Nothing is posted or sent to any social network. No credentials are read, no shell commands are run, no telemetry.

## Tier

The entry is submitted as `tier: community`. We would like to ask the maintainers to consider `verified`: Launch Desk is built and maintained by posts.design itself. The repository is under the posts-design GitHub organisation, and the MCP server it integrates is posts.design's own, at https://posts.design/mcp. If you grant `verified`, we would also welcome `onboarding: true`.

Maintainer: posts.design (github.com/posts-design).
