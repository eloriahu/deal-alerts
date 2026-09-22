# Deal Alerts: design

Date: 2026-09-21
Owner: the repository owner
Status: approved 2026-09-21. Build plan: docs/superpowers/plans/2026-09-21-deal-alerts.md

## Purpose

Spot very large price drops and suspected price errors ("glitches") on major
shopping and travel sites, and tell the owner fast enough to act. Run online
at zero cost, with no dependence on any PC being on.

## Decisions already made

| Question | Decision |
|---|---|
| Regions | Singapore, Hong Kong, Japan, US, Europe. Kept separate everywhere: never mixed in one list or one channel. |
| Phone alerts | Telegram. One bot, five channels: SG Deals, HK Deals, Japan Deals, US Deals, Europe Deals. Each can be muted on its own. |
| Strictness | Two tiers. Phone alert for suspected glitches and discounts of 70% or more. Dashboard only for 40 to 70% off. |
| Categories | Everything. No category filter. |
| Hosting | GitHub: scheduled run every 5 minutes, dashboard on GitHub Pages, public repository `github.com/eloriahu/deal-alerts`. |
| Cost | Zero. No paid service, no credit card. |

## How it finds deals

It does not scan shop catalogues. Large shops block automated visitors and no
free method survives that. Instead it reads the places where people post
glitches within minutes of finding them, and filters hard.

The list that ships is in `sources.yaml`, and that file is the only source of
truth. Each entry carries its region; region is fixed at the source and never
guessed from the text. As shipped:

- Singapore: SingPromos, MoneyDigest, MileLion, r/singaporedeals.
- Hong Kong: Jetso Club, Jetso Today, GoTrip (Hong Kong's main deal sites;
  "jetso" is the local word for a bargain). Posts are in Traditional Chinese.
- Japan: the Gekiyasu deal blog, Traicy for airline fare sales, PC Watch.
  Posts are in Japanese.
- US: the Slickdeals front page and popular feeds, r/deals, r/buildapcsales,
  The Flight Deal.
- Europe: DealDoktor and Mein-Deal (Germany), Travel-Dealz for error fares,
  r/UKDeals.

Everything else named during design stays a candidate, tested from GitHub's
servers before it is added: public deal Telegram channels through their web
preview pages, ITmedia, Secret Flying, r/HongKong, Japanese deal subreddits,
and HotUKDeals, mydealz and Dealabs, which all refuse automated readers today.
A source that blocks GitHub's addresses is replaced, not left failing quietly.

## Parts

Each part has one job and can be tested on its own.

| Part | Job | Input | Output |
|---|---|---|---|
| `sources.yaml` | List of sources with name, region, kind, address | none | config |
| `fetchers/` | One reader per source kind (feed, Reddit, Telegram preview). Fetch and turn posts into a common `Deal` record | source entry | list of `Deal` |
| `parse.py` | Pull current price, usual price, discount %, shop name out of a post title and body | text | fields on `Deal` |
| `score.py` | Decide the tier: `alert`, `dashboard`, or `ignore`, with the reasons | `Deal` | tier + reasons |
| `store.py` | Remember which deals were seen and alerted, and source health. One file, `data/state.json`, committed back by the run | deals | state |
| `notify.py` | Send one Telegram message per new `alert` deal to that region's channel | deal + tier | message sent |
| `site.py` | Build the static dashboard page into `public/` (published by the run; not the `docs/` folder, which holds this spec) | state | `index.html` |
| `run.py` | Run the parts in order; one source failing never stops the rest | none | exit code |
| `translate.py` | Ask MyMemory for an English headline. Display only: never touches scoring | title + source language | English title or nothing |
| `.github/workflows/scan.yml` | Timer (every 5 minutes), runs `run.py`, commits `data/state.json`, publishes `public/` to GitHub Pages | none | none |

`Deal` record: id (`<region>-<hash of the cleaned link>`), region, source,
title, English title if one was obtained, link, shop, price now, usual price,
discount %, votes or heat if the source gives it, posted time, first seen time. The id carries the region, so
the same link in two regions is two separate deals.

## Scoring rules

First, before anything else: in the regions listed under `online_only_regions`
(Hong Kong, Japan, the US and Europe as shipped) a post whose title or summary
carries one of the `in_store_words` is dropped outright. Those are regions the
owner can only buy from at a distance, so a bargain that can only be taken on a
shop floor or in a dining room is noise there. This overrides every rule below,
including the price-error words and the vote count: a price error at a till is
still a till. Singapore is deliberately not in the list.

Phone alert (tier `alert`) when any of these is true:

1. The post's **title** contains a price-error word: "price error", "pricing
   error", "price glitch", "pricing glitch", "misprice", "mispriced", "price
   mistake", "pricing mistake", "error fare", "mistake fare" (plus German and
   French equivalents for the Europe sources: "Preisfehler", "erreur de prix";
   for Hong Kong: "錯價", "出錯價", "標錯價", "價錢錯誤"; for Japan:
   "価格ミス", "価格エラー", "価格誤表記", "価格バグ", "価格設定ミス"). The words
   are listed per language in `sources.yaml`, so adding one needs no code
   change. Only the title is read: in a summary these words are almost always
   about something else, and a news site's story about a configuration mistake
   is not a deal.
2. Discount is 70% or more, from the stated prices or a stated percentage.
3. A Slickdeals post that already has 60 or more votes when it first appears in
   the feed, and is under 3 hours old (threshold per source in `sources.yaml`).
   Votes are read once, when the post is first seen; they are not tracked over
   time.

A post that states a time more than 24 hours old drops from `alert` to
`dashboard`, keeping its reasons plus "posted over 24 h ago". A post with no
usable time is never held back. The same limit decides first contact: a source
that has never been read, or was last read more than 24 hours ago, has its
whole front page stored as backlog without buzzing the phone.

Dashboard only (tier `dashboard`): discount from 40% up to 70%.

Everything else: `ignore`, not stored beyond the seen-list.

Guards against false alarms: "up to 70% off" and "up to an extra 70% off" do
not count; a minus sign has to touch the number ("-72%" counts, "- 85% battery
health" does not); a percentage aimed at something else does not count ("70%
off shipping", "75% off your first month"); a post carrying one of the
`ignore_words` from `sources.yaml` is never judged on its percentage at all; a
discount is only computed when both prices are in the same currency; posts
marked expired by the source are skipped.

Chinese and Japanese posts: prices and discounts are read with patterns for
those scripts ("HK$", "港幣", "円", "￥", "半額" meaning half price, "7割引"
meaning 70% off, "3折" meaning 70% off in Hong Kong usage, where the number is
the share you pay). Titles are shown in English with the original underneath,
translated by MyMemory (free, no sign-up) from the source's declared language;
the price, discount and shop lines of the alert are always in English. The
translation is display only and never reaches scoring, so a translation that is
wrong, late or missing cannot change what alerts. A source whose translation
fails once is not retried for the rest of that run, and one run asks at most
`max_translations_per_run` times.

Each deal alerts once. It is remembered by its region-scoped id for 30 days
after it was last seen, so a post that sits in a feed for months never alerts
again. If the same product link appears from two sources in one region, only
the first alerts; the same link in two different regions is two separate
deals, because regions are never mixed.

## Telegram message format

```
PRICE ERROR?  Sony WH-1000XM6
S$89 (usual S$549)  -84%
Shop: Amazon.sg
Why: glitch word in title; 84% off
Source: SingPromos, posted 6 min ago
<link>
```

The bot key and the five channel ids are kept in GitHub's encrypted secrets
(`TELEGRAM_BOT_TOKEN`, `TG_CHAT_SG`, `TG_CHAT_HK`, `TG_CHAT_JP`, `TG_CHAT_US`, `TG_CHAT_EU`). They never
appear in the repository, the logs, or the dashboard. One further optional
secret, `MYMEMORY_EMAIL`, holds any email address and raises MyMemory's free
daily allowance from 5,000 to 50,000 characters; it is treated exactly like the
others and never leaves the encrypted store. Without it the anonymous allowance
applies and everything still works.

## Dashboard

One static page at `https://eloriahu.github.io/deal-alerts/`.

- Five tabs: Singapore, Hong Kong, Japan, US, Europe. The chosen tab is remembered.
- Suspected glitches pinned at the top of each tab, then other deals, newest first.
- Each row: product (in English, with the original headline underneath), price
  now, usual price, discount, shop, source, age, link.
- Header shows when the last check ran.
- A health line per source: last success time, and a red mark after failures.
- Readable on a phone. Shows the last 7 days.

The page is public. It contains only deals, nothing about the owner.

## When things go wrong

- One source failing: logged, marked on the dashboard, the rest continue.
- A source failing 5 runs in a row: one Telegram note to the region's channel,
  then silence until it recovers. At most one such note per source per 24
  hours, so a source that flaps cannot send a note on every wobble.
- `sources.yaml` unreadable: nothing is checked, and one plain Telegram line
  says so and names the position of the problem, at most once a day.
- Telegram send failing: the deal stays un-alerted and is retried next run.
- GitHub pausing the timer (it does this after 60 days with no repository
  activity): the run commits data each time, which counts as activity. The
  dashboard's "last check" time makes a stall visible.
- GitHub's timer is loose. A run can arrive 5 to 20 minutes late. Accepted.
  If it proves too slow, the fallback is moving the timer to Cloudflare; the
  parts above do not change.

## Testing

- Unit tests for `parse.py` and `score.py` using saved real post titles,
  including the false-alarm cases ("up to 70% off", mixed currencies).
- Each fetcher tested against a saved sample of its source.
- `run.py --dry-run` prints what it would send, sends nothing.
- A one-off source probe run on GitHub itself, to find out which sources block
  GitHub's addresses before relying on them.
- First live run sends to a private test chat before the five real channels.

## Limits

- It sees a glitch only after someone posts it publicly.
- Shops often cancel glitch orders. The tool spots; it does not buy.
- No automatic purchasing, no logging in to any shop, no storing of any
  personal or payment detail.
- Kept separate from any other project.

## Not in this version

Direct price tracking of a personal product wish-list; category filters;
currency conversion to Singapore dollars; AI-written summaries. Each can be
added later without changing the parts above.
