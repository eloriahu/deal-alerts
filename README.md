# Deal Alerts

This is a small free tool that watches public bargain websites for Singapore,
Hong Kong, Japan, the US and Europe, and messages your phone when something
looks like a mistake or a very deep cut.

It checks every 5 minutes. GitHub (the website that stores this project) runs
it, so no computer of yours has to be switched on, and it costs nothing.
GitHub's timer is loose: a check can turn up a few minutes late, and sometimes
one is skipped when GitHub is busy. Nothing is lost when that happens; the next
check picks up whatever was posted in the meantime.

Everything it finds is also listed on a web page:
https://eloriahu.github.io/deal-alerts/

## The five Telegram channels

Telegram is a free messaging app. The tool has one channel per region:
Singapore, Hong Kong, Japan, US and Europe. Messages for a region only ever go
to that region's channel; the regions are never mixed.

If you do not want a region on your phone, open that channel in Telegram and
mute it. Nothing in this project needs changing.

## What makes your phone buzz

Three things, and only three:

1. **The headline says it is a price mistake.** Phrases such as "price error",
   "price glitch", 標錯價, 価格ミス or "Preisfehler". Only the headline counts.
   If the phrase is buried in the description it is nearly always about
   something else, such as a phone app having a glitch, so it is ignored.
2. **The cut is 70% or more.** Either stated on the post ("70% off") or worked
   out from a "now" price and a "usual" price in the same currency.
3. **A Slickdeals post that arrives already popular.** Slickdeals is a big US
   bargain site where readers vote posts up. If a post already has 60 or more
   votes the first time this tool sees it, and it was put up less than 3 hours
   ago, that counts. The tool looks at the vote count once, when it first sees
   the post; it does not watch votes climb over time.

On top of that: **if the post says it was put up more than 24 hours ago, your
phone stays quiet.** A price mistake from last week is gone by now. The deal
still appears on the web page, and if it is a suspected price mistake it still
carries the red "SUSPECTED PRICE ERROR" mark there. So an old one of those on
the page with no message on your phone is the tool working as intended, not an
alert it missed. If a post does not say when it was put up, this limit is not
applied and the alert goes through.

Anything between 40% and 70% off goes to the web page only. Everything else is
dropped.

Each deal buzzes once, and only once, even if several sites post it.

## Only online deals outside Singapore

Outside Singapore you cannot walk into the shop, so a bargain you could only
take at a till or a dinner table is no use to you. In **Hong Kong, Japan, the
US and Europe** the tool therefore throws away any post whose headline or
description mentions a shop floor or a dining room: "in-store", "dine-in",
"restaurant", 堂食, 店舗, "Filiale", "en magasin" and so on.

This beats every other rule. Even a post that says "price error", and even one
with 90% off, is dropped if it also says "in-store only".

**Singapore is never filtered this way.** That is where you are, so a shop deal
there still reaches you.

The words live in `sources.yaml` under `in_store_words`, grouped by language.
To stop dropping posts that mention buffets, delete `"buffet"` from the list
it appears in. To start dropping posts about click-and-collect, add it:

```yaml
  en: ["in-store", "in store", "instore", "store only", "click and collect"]
```

Keep the quotation marks and the commas exactly as they are.

The regions this applies to are listed near the top of the same file:

```yaml
  online_only_regions: [hk, jp, us, eu]
```

Remove a region from that line and it stops being filtered. Add `sg` and
Singapore starts being filtered too. Only `sg`, `hk`, `jp`, `us` and `eu` are
allowed there; anything else stops the tool and sends you a message saying so.

## The web page

Five tabs, one per region. Suspected price mistakes sit at the top. The page
shows the last 7 days and reads fine on a phone.

At the top is **"Last check"**: the time the tool last ran. If that time stops
moving forward, the tool has stopped. See "When something looks wrong" below.

At the bottom of each tab is one line per source with a coloured dot:

- **Green dot** - that source was read successfully, and the line says when.
- **Red dot** - that source is failing, or has never been read at all.

One red dot is normal and not urgent; websites go down and come back. All of
them red at once usually means the tool itself is stuck.

## English titles

Posts from Hong Kong, Japan, Germany and France arrive in Chinese, Japanese,
German and French. The tool sends each headline to **MyMemory**, a free
translation service that needs no sign-up and no payment card, and shows you
the English version first. The original headline is always kept directly
underneath, in smaller letters on the web page and in italics in the Telegram
message, so nothing is lost when a translation comes out clumsy.

Only the headline is translated, and only for show. What counts as a bargain is
still decided on the original words, so a bad translation can never cost you an
alert or invent one.

If MyMemory is down or too busy, you simply see the original headline. The tool
mentions it once in its log and carries on; nothing else changes.

### The free daily allowance

Without any setting-up, MyMemory translates about **5,000 characters a day**
for free, which is roughly 150 headlines. After that it stops answering until
the next day and you see original headlines for the rest of the day.

You can raise that to **50,000 characters a day** by giving MyMemory an email
address. This is optional. To do it, on GitHub open this project, **Settings**,
then **Secrets and variables**, then **Actions**, press **New repository
secret**, name it exactly `MYMEMORY_EMAIL`, and put any email address of yours
in the value box. It is stored encrypted, it never appears on the web page, in
a message or in the logs, and nothing else about the tool changes.

## "Source down" messages

If a source fails five checks in a row, you get one Telegram message naming it.
You will get at most one message per source per day, however badly it
misbehaves, so a site that keeps dropping in and out cannot flood your phone.
When the source recovers, the messages simply stop.

## Changing things

Everything you can change is in one file, `sources.yaml`. Open the project on
GitHub in a browser, click that file, click the pencil, edit, and press
"Commit changes". The next run picks it up.

Keep the spacing exactly as you found it. In this file, indentation is not
decoration; it is what tells the tool which setting belongs to what.

### Adding a place to watch

Under `sources:`, copy one of the lines and change it. For example, to add a
German bargain blog:

```yaml
  - {name: Sparblog, region: eu, kind: feed, language: de,
     url: "https://www.sparblog.example/feed/"}
```

Every part explained:

- `name` - whatever you want to call it, but it must be different from every
  other name in the file. It is what appears on the web page and in "source
  down" messages.
- `region` - exactly one of `sg`, `hk`, `jp`, `us`, `eu`. This decides which
  Telegram channel the deal goes to and which tab it appears on.
- `kind` - `feed` for a normal website with a news feed, or `telegram` for a
  public Telegram channel's web preview page. Almost always `feed`.
- `url` - the address of the feed. Most blogs have one at their address
  followed by `/feed/`.
- `language` - `en`, `de`, `fr`, `zh` or `ja`. Get this one right: it says
  which language the headlines are written in, and the tool translates them
  into English from that language (see "English titles" above). Say `ja` for a
  site that writes in Japanese, `en` for one that writes in English, and so on.
  It does not change what counts as a bargain: every price-mistake phrase in
  the file is checked against every source whatever its language.
- `require_sale_word` - add `require_sale_word: true` for a general news site
  that only sometimes writes about bargains, such as a tech news site. Posts
  from it are then ignored unless they mention a sale or a discount, or their
  headline says it is a price mistake. Leave it out for a site that is all
  bargains.
- `heat_threshold` - only for Slickdeals, where posts carry a vote count. Leave
  it out everywhere else.

The first time a new source is read, nothing buzzes your phone: its whole front
page is treated as old news and only goes to the web page.

### Adding a price-mistake phrase

Under `glitch_words:`, add it to its language's list. Keep the quotes and the
comma:

```yaml
  en: ["price error", "pricing error", "price glitch", "pricing glitch", "epic fail price"]
```

Make it specific. A word on its own, such as "glitch", matches ordinary posts
about broken apps and games.

### Ignoring a kind of offer

Under `ignore_words:`, add a phrase that marks an offer you never want. Any
post containing it is never judged on its percentage:

```yaml
  en: ["new customers", "first month", "second item", "2nd item", "free trial"]
```

This is how "50% off your first month" stops looking like a 50% bargain. It
does not switch off the price-mistake rule, which is deliberate.

### Making alerts rarer or more common

Near the top of the file:

```yaml
  alert_discount: 70
```

That is the percentage that buzzes your phone. Raise it to 80 for fewer
messages, lower it to 60 for more. `dashboard_discount: 40` is the percentage
from which a deal appears on the web page, and works the same way.

## When something looks wrong

If "Last check" on the web page stops moving forward, or Telegram tells you
that sources.yaml could not be read, **undo your last edit to that file.** A
single misplaced space, a missing quotation mark or a misspelt setting name
stops everything, and the message names the line number or the setting.

To undo an edit on GitHub: open `sources.yaml`, click "History", open the
version from before your edit, and copy its contents back into the file.

## Pausing and restarting

On GitHub, open this project, the **Actions** tab, choose **scan** on the left,
then the **"..."** button on the right and **Disable workflow**. Your phone
goes quiet and the web page stops updating. The same button, now reading
**Enable workflow**, starts it again.

## Sending yourself a test message

On GitHub, **Actions** tab, **scan**, **Run workflow**. Tick the box labelled
"Only send a test message to every Telegram chat", then press the green
**Run workflow** button. Within a minute or two each of the five channels gets
one line saying it works. No bargains are checked on such a run.

## What it cannot do

It only spots a price mistake after somebody has posted it publicly, so you are
never first. Shops very often cancel price-mistake orders after taking them. It
never buys anything, never signs in to any shop, and never stores any card or
personal detail. It does not watch particular products for you.

## Running it on your own computer

You do not have to; GitHub does all of it. If you want to try it anyway, you
need `uv`, which is a free program that installs and runs Python projects for
you (Python is the language this tool is written in). Install `uv` from
https://docs.astral.sh/uv/, then in this project's folder:

```
uv sync
uv run pytest
uv run python run.py --dry-run
```

`--dry-run` prints what it would send and sends nothing. Leave it out and it
would message the channels for real, so keep it.
