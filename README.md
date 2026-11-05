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

To put your product on the card, send a screenshot and the agent attaches it with `launch_desk_attach_screenshot`. The plugin also ships a skill, `launch-desk:launch-desk`, that teaches the agent the launch workflow, so it steers you well even when you do not type the command.

## What it checks

- The first line is 12 words or fewer, with no link in it.
- No stock launch phrases such as "excited to announce" or "game-changer".
- At most one em-dash, one emoji and two hashtags.
- No placeholders such as [link], {name} or TODO.
- At most 280 characters on X, counting each link as 23.
- No run of five words copied from a reference.
- No numbers or time and effort claims that are not in your facts.

## What goes where

- posts.design receives only the search phrasings and filters. Your draft, brand and screenshot are not sent.
- Your own model, the one Hermes is configured with, receives the brief: what you shipped, your detail, your product name and tagline, and the reference captions.
- Your product site is fetched to read colours and logo: at most 4 requests.
- The brand kit, drafts and pending drafts are stored in the plugin's data folder on your machine.
- Nothing is posted, scheduled or sent to any social network.

## Credits

One search per run, about one posts.design credit. Free accounts get 100 credits a month. References come from posts.design. Pro adds full playbooks.

## License

MIT. The bundled Inter font is under the SIL Open Font License.

Built by [posts.design](https://posts.design).
