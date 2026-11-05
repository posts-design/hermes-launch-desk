# Launch Desk

A posts.design plugin for Hermes Agent.

Tell it what you just shipped. It finds three real launch posts, drafts yours, and builds a card from your own screenshot and brand.
You get a pack with the text, the card, a feed preview and a link to post on X. It never posts for you.

## How it works

The model writes about 20% of this. The rest is code and your own material:

- References are real launch posts from posts.design, chosen in code by what you shipped.
- The draft is checked in code against AI-sounding copy and against details you never gave it, and is redrafted when it fails.
- The card is composed from your screenshot, colours and logo. It is never generated.

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

The text after ` -- ` is one true detail, such as who asked for it. The draft treats it as a fact. Add your site's link the first time; it is remembered.

It takes two steps. The first run ends with the draft, the card and a feed preview. Send `/launch` again to approve, change the hook, swap references or discard. `/launch brand <url>` reads your site's colours and logo again, and `/launch help` shows the usage.

To make drafts sound like you, send `/launch voice: <a past post of yours>`. It keeps your last three; `/launch voice: clear` forgets them. If your posts use more em-dashes, emoji or hashtags than the checks allow, the draft may too, and the reply says so.

After you approve, `/launch` asks whether to check in tomorrow and in 3 days. Say Yes and Hermes' own scheduler runs two check-ins that post to the same chat: each asks how many sign-ups each channel brought and suggests one next post from your pack (a reply with your detail, a hook you did not use, or the shape of a reference you did not see). Reply with your numbers and the agent records them in the pack with `launch_desk_record_results`. The reply names each job; cancel one with `/cron remove <id>` in chat or `hermes cron remove <id>` in a terminal. There is also an automation blueprint, `launch-desk:follow-up`, for one more check-in on a pack from `/blueprint`.

To put your product on the card, send a screenshot and the agent attaches it with `launch_desk_attach_screenshot`. Send two and the card shows them side by side; with none, the card sets your hook large as a quote. The plugin also ships a skill, `launch-desk:launch-desk`, that teaches the agent the launch workflow, so it steers you well even when you do not type the command.

## What it checks

- The first line is 12 words or fewer, with no link in it.
- No stock launch phrases such as "excited to announce" or "game-changer".
- At most one em-dash, one emoji and two hashtags.
- No placeholders such as [link], {name} or TODO.
- At most 280 characters on X, counting each link as 23.
- No run of five words copied from a reference.
- No line that just restates the first.
- No numbers or time and effort claims that are not in your facts.

## What goes where

Sent to posts.design, through your own account:

- The first 160 characters of what you shipped, as the search text.
- A short keyword phrase from it, a fixed phrase for the kind of launch, and a category.
- An access check (`get_my_access`) on each run.

Not sent to posts.design: the detail after ` -- `, your site link, the draft, your brand kit and your screenshot.

Sent to the model Hermes is configured with, which may be a hosted provider: the brief (what you shipped and your detail), your product name, tagline and link, the reference captions, and your voice samples if any. A check-in also sends it the pack it reads: the post, its X link, the unused hooks, your detail, the brand, link and shape of references you did not see, and any sign-ups you recorded.

Fetched from the internet: your site, its stylesheets and the logo it links to (at most 4 requests, each following up to 3 redirects).

Stored on your machine, in the plugin's data folder and Hermes plugin state:

- The brand kit: name, site link, tagline, colours, font name, logo file and voice samples.
- A pending draft with its card, its preview and the search results it came from (items.json), kept up to 24 hours or until you approve or discard it.
- Finished packs, kept until you delete them, with the sign-ups per channel you report after a check-in.
- Copies of the card and preview in Hermes' image cache, so chat apps can show them. Hermes clears them after about a day.
- The paths of up to two attached screenshots, until you approve or discard the draft that used them. The files are not copied; the screenshot tool reads each image where it is and leaves it there.

Scheduled, only if you say Yes after approving: two check-ins on Hermes' own scheduler, a day and three days later. They run as ordinary Hermes cron jobs, post to the same chat, and are listed by `hermes cron list`. Nothing else is scheduled.

Nothing is posted or sent to any social network.

## Credits

One search per run, about one posts.design credit. Free accounts get 100 credits a month. References come from posts.design. Pro adds full playbooks.

## License

MIT. The bundled Inter font is under the SIL Open Font License.

Built by [posts.design](https://posts.design).
