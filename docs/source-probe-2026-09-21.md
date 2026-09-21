# Source probe, 2026-09-21

Run from a development PC with curl. Results from GitHub's servers may differ
and must be re-tested there before the source list is final.

| Source | Region | Result | Verdict |
|---|---|---|---|
| Slickdeals front page feed | US | 200, feed | works |
| Slickdeals popular deals feed | US | 200, feed | works |
| Reddit r/deals feed | US | 200, feed | works, but see Reddit note |
| Reddit r/buildapcsales feed | US | 429 (too many requests) | rate-limited |
| Reddit r/singaporedeals feed | SG | 429 | rate-limited |
| HardwareZone forum feed (all forums) | SG | 200, feed | works; correct deals sub-forum address still to find (guess returned 404) |
| SingPromos feed | SG | 200, feed | works |
| MoneyDigest feed | SG | 200, feed | works |
| Telegram public preview (t.me/s/...) | SG | no connection | blocked by the local network; test from GitHub |
| HotUKDeals feed | Europe | 403, block page | blocked |
| mydealz feed | Europe | 403, block page | blocked |
| Dealabs feed | Europe | 403, block page | blocked |
| Secret Flying feed | travel | 200 but a web page, not a feed | no feed at that address; page would need reading directly |

Notes:

- Reddit: the second and third requests in a row got 429. Reddit limits
  anonymous readers. Options: space requests out and send a descriptive
  identifier, or use Reddit's free registered-app access (free, needs a Reddit
  account). GitHub's addresses are often limited harder than home ones.
- Europe: HotUKDeals, mydealz and Dealabs are one company and all three refuse
  automated readers. Europe needs other sources: Reddit deal communities for
  the UK, Germany and France, and independent deal blogs with feeds. This is the
  weakest region and the main open risk in the design.

## Second probe: Hong Kong and Japan (same day, same PC)

| Source | Region | Result | Verdict |
|---|---|---|---|
| Jetso Club feed | HK | 200, feed, 25 posts | works |
| Jetso Today feed | HK | 200, feed, 100 posts | works |
| GoTrip feed (travel offers) | HK | 200, feed, 10 posts | works |
| r/HongKong search feed for "deal" | HK | 200, feed, 25 posts | works, Reddit limits apply |
| flyagain.la feed | HK | no connection | unreachable from this network; test from GitHub |
| HongKongCard feed | HK | 404 | no feed at that address |
| U Lifestyle feed | HK | 200 but a stub page | no feed |
| Gekiyasu deal blog feed | Japan | 200, feed, 11 posts | works |
| PC Watch feed | Japan | 200, feed, 21 posts | works; general tech news, needs the sale filter |
| ITmedia feed | Japan | 200, feed, 50 posts | works; general news, needs the sale filter |
| Gigazine feed | Japan | 200, feed, 30 posts | works; mostly news, low value |
| Traicy feed (airline fare sales) | Japan | 200, feed, 30 posts | works |
| Netatopi feed | Japan | 404 | address wrong; find the right one |
| kaimono-yosan feed | Japan | no connection | unreachable; test from GitHub |
| r/japandeals feed | Japan | 429 | Reddit rate limit |

Japan is thinner than it looks: only Gekiyasu is a pure deal source. The
biggest Japanese glitch-spotting happens on X (Twitter) and on 5ch boards,
neither of which has a free readable feed. More Japanese deal blogs should be
found during the build.
