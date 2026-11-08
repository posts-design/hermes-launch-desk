# Launch Desk

A posts.design plugin for Hermes Agent.

Tell it what you just shipped. It finds three real launch posts, drafts yours, and builds a card from your own screenshot and brand.
You get a pack with the text, the card, a feed preview and a link to post on X. It never posts for you.

## How it works

The model writes about 20% of this. The rest is code and your own material:

- Your words lead. The detail you give after ` -- ` is offered as the first line, word for word, marked as yours. The model's hooks come after it, each offered only if it adds no failure of its own to the post, and no two built the same way.
- References are real launch posts from posts.design, chosen in code by what you shipped.
- The draft is checked in code against AI-sounding copy and against details you never gave it, and is redrafted when it fails. You see the checks: a line says what the first draft was caught on, and the draft comes with a checklist of the post, one line per check.
- The card is composed from your screenshot, colours and logo. It is never generated. Its headline names your product and what shipped (9 words at most, checked like the post), so it never repeats the first line of your post. It reads your site the way a visitor sees it: dark if the site is dark, the accent your site names or puts on its buttons, and a headline face close to yours (a serif, a rounded sans, or Inter).
- Your results feed the next launch. Once you record sign-ups for a pack, the next `/launch` opens with one line on your best launch so far, and that post is reference 1: the draft borrows its shape, never its words.

Because the checks and the design do the heavy lifting, it works on small models.

## Install

```
hermes plugins install posts-design/hermes-launch-desk
```

Once the plugin is listed in the Hermes catalog, you will be able to install it by name.

## Setup

For real references from posts.design, run these once:

```
hermes mcp add posts --url https://posts.design/mcp --auth oauth
hermes config set plugins.entries.launch-desk.mcp_allowlist '["posts"]'
hermes mcp login posts
```

Without them, `/launch` runs on a built-in sample pack of launch posts and tells you so.

## Use

```
/launch https://yoursite.com dark mode for the editor -- people kept asking for it
/launch a new onboarding flow
```

The text after ` -- ` is one true detail, such as who asked for it. The draft treats it as a fact, and it is hook 1, word for word. If a line of the draft would repeat it, that line comes out when you pick it. Add your site's link the first time; it is remembered. A brand saved by an older version, before dark sites and headline faces were read, is read again once on your next `/launch`.

It takes two steps. The first run ends with the draft, the card and a feed preview. Send `/launch` again to approve, change the hook, swap references or discard. `/launch brand <url>` reads your site's colours and logo again, and `/launch help` shows the usage.

To make drafts sound like you, send `/launch voice: <a past post of yours>`. It keeps your last three; `/launch voice: clear` forgets them. If your posts use more em-dashes, emoji or hashtags than the checks allow, the draft may too, and the reply says so.

After you approve, `/launch` asks whether to check in tomorrow and in 3 days. Say Yes and Hermes' own scheduler runs two check-ins that post to the same chat. Each posts one message composed in code from your pack and run through the same copy checks: it asks how many sign-ups each channel brought and gives up to two next-post ideas (a reply with your detail, a hook you did not use, or the shape of a reference you did not see). Reply with your numbers and the agent records them in the pack with `launch_desk_record_results`. The reply names each job. To cancel one in the terminal chat, send `/cron remove <id>`; in a chat app, ask the agent to remove check-in `<id>`, or run `hermes cron remove <id>` where Hermes runs. There is also an automation blueprint, `launch-desk:follow-up`, for one more check-in on a pack from `/blueprint`.

The next `/launch` reads those numbers back. Of the packs for the same site, the one with the most sign-ups (the most recent one on a tie) gives one line, such as: Your last launch brought 12 sign-ups on X with "Pet pages can wear your colours now." Its post is listed first among the references as your own launch, with its sign-ups and pack name, and the draft borrows its shape. It is yours, so the check for five copied words leaves it out, and your detail for this launch never counts as copied either. A later answer for a channel replaces the earlier one, since each check-in asks for the count so far. Packs without results, and packs for another site, change nothing.

To put your product on the card, send a screenshot and the agent attaches it with `launch_desk_attach_screenshot`. Send two and the card shows them side by side; with none, the card sets your hook large as a quote. The plugin also ships a skill, `launch-desk:launch-desk`, that teaches the agent the launch workflow, so it steers you well even when you do not type the command.

## Hermes Desktop

In Hermes Desktop, Launch Desk adds a Launch Desk page to the sidebar (also in the command palette as Open Launch Desk). It shows every pack, newest first: the card and the feed preview, the post with your own words marked, the checks, the three reference posts with their views, the check-ins and the sign-ups you recorded. Copy post copies the text; Open X opens X with it filled in, in your browser. The status bar shows when the next check-in runs, or how many never ran.

The Desktop half ships off. Turn on Launch Desk in Capabilities → Plugins. If Desktop and Hermes run on different machines, install it from Desktop with Install from Git and the Desktop part checked, or with this link: `hermes://plugin/install?repo=posts-design/hermes-launch-desk`.

The page only reads. Its backend (`dashboard/plugin_api.py`, loaded when the plugin is enabled in Hermes) reads this plugin's own packs folder and, from Hermes' `cron/jobs.json`, the ids, state and times of the check-in jobs `/launch` created. It writes nothing and sends nothing anywhere. Links on the page open only if they are web (https) links.

## What it checks

- The first line is 12 words or fewer, with no link in it.
- No stock launch phrases such as "excited to announce", "check this out", "watch this" or "game-changer".
- No "Your X should" opener, the template that gives a post away as written by a model.
- At most one em-dash, one emoji and two hashtags.
- No placeholders such as [link], {name} or TODO.
- No link that only opens on your machine or network: localhost, a loopback or private address, or a .local name.
- At most 280 characters on X, counting each link as 23.
- No run of five words copied from a reference (your own past post and your own detail don't count).
- No line that just restates the first, and no phrase said twice: a later line saying three words of the first again, two lines sharing a phrase ("next to meetings"), or a word other than your product's name in three lines.
- Every line starts with a capital letter, unless it starts with a link, a handle or your product's name as you write it. If your own past posts start lower case, this is relaxed.
- No numbers, counts or time and effort claims ("three things", "today", "in seconds") that are not in your facts. Ones that only your site's description gives are named in the checklist as from your site.

The reply shows this list for your post, each line marked pass or fail, and the pack keeps it in `pack.json` and `checklist.md`. A hook the model wrote is offered only if it adds no failure of its own; if none does, the least bad one is offered marked "(has issues)". Your own words are always offered, and if they break a rule (say, over 12 words) the checklist says so. If picking your words drops the only line that said what shipped, the checklist says that too.

## What goes where

Sent to posts.design, through your own account:

- The first 160 characters of what you shipped, as the search text.
- A short keyword phrase from it, a fixed phrase for the kind of launch, and a category.
- An access check (`get_my_access`) on each run.

Not sent to posts.design: the detail after ` -- `, your site link, the draft, your brand kit and your screenshots.

Sent to the model Hermes is configured with, which may be a hosted provider: the brief (what you shipped and your detail), your product name, tagline and link, the reference captions (including your own past post with the most sign-ups, when you have recorded some), and your voice samples if any. A check-in also sends it the pack it reads: the post, its X link, the unused hooks, your detail, the brand, link and shape of references you did not see, and any sign-ups you recorded.

Fetched from the internet: your site, its stylesheets and the logo it links to (at most 4 requests, each following up to 3 redirects).

Stored on your machine, in the plugin's data folder and Hermes plugin state:

- The brand kit: name, site link, tagline, colours, font name and face, logo file and voice samples.
- A pending draft with its card, its preview and the search results it came from (items.json), kept up to 24 hours or until you approve or discard it.
- Finished packs, kept until you delete them, with when they were made and the sign-ups per channel you report after a check-in (the last 50). The next `/launch` reads them from there.
- Copies of the card and preview in Hermes' image cache, so chat apps can show them. Hermes clears them after about a day.
- The paths of up to two attached screenshots, until you approve or discard the draft that used them. The files are not copied; the screenshot tool reads each image where it is and leaves it there.

Scheduled, only if you say Yes after approving: two check-ins on Hermes' own scheduler, a day and three days later. They run as ordinary Hermes cron jobs with the plugin's own tools and no MCP server, post to the same chat, and are listed by `hermes cron list`. Nothing else is scheduled.

Nothing is posted or sent to any social network. No credentials are read, no shell commands are run, no telemetry.

## Credits

One search per run, about one posts.design credit. Free accounts get 100 credits a month. References come from posts.design. Pro adds full playbooks.

## License

MIT. The bundled fonts are under the SIL Open Font License 1.1, each with its licence beside it in `launch_desk/fonts/`: Inter (headlines and all small text), and Newsreader and Nunito (headlines for serif and rounded brands), from [google/fonts](https://github.com/google/fonts).

Built by [posts.design](https://posts.design).
