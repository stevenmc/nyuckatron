# newry-bot

Polls news feeds for Newry-related articles and posts new ones to a
subreddit. Each run is a single fetch-filter-post pass, meant to be triggered
by cron -- no server, nothing kept running between runs.

## What it does

- Pulls entries from the feeds in [config.py](config.py): BBC News NI,
  newry.ie's own editorial output, Newry Democrat's own feed, and a Google
  News RSS search -- see `CATCHMENT_PLACES` in config.py: Newry, Mayobridge,
  Camlough, Rostrevor, Warrenpoint, Carlingford, Crossmaglen, Lislea,
  Omeath. Dundalk is deliberately excluded -- it's a substantial town in a
  different jurisdiction with its own separate news cycle, and including
  it pulled in a lot of Dundalk-only stories with no real connection to
  Newry. Hilltown was removed 2026-10-01 for the same reason, despite
  being a genuine catchment town -- "Hilltown" turned out to collide with
  unrelated places of the same name often enough (Dundee, a Pennsylvania
  township, and others) to be unmanageable; see "Design notes" below.
  Newry.ie is scraped from its homepage HTML (`bot.py`'s
  `_scrape_newry_ie_homepage`), not its RSS feed -- that feed silently
  stopped updating in practice; see "Design notes" below.
- Drops anything older than `MAX_NEWS_AGE_DAYS` (by the feed's own
  published/updated date). Newry.ie and Newry Democrat are exempt from
  this specific check -- their own date fields are unreliable (confirmed:
  entries scattered years off, or a "newest" entry over five weeks stale
  on a site that publishes daily) -- and instead use `max_entries` to cap
  how many of their most-recent-by-feed-position articles are even
  considered, sidestepping the bad date field entirely. See `bot.py`'s
  `fetch_entries` and `_is_too_old` for how the two checks stay mutually
  exclusive per feed.
- For a Google News entry, checks first whether a recent Newry.ie
  article's headline is a close match (`config.NEWRY_IE_LINK_MATCH_
  THRESHOLD` -- stricter than the general dedup threshold, since a wrong
  substitution is worse than a missed one) and, if so, uses Newry.ie's own
  real link straightaway -- a lot of what the Google News search surfaces
  already ran on Newry.ie too, and there's no point resolving Google's
  wrapper link once we already know that. Otherwise, resolves the wrapper
  link to the real article URL where possible -- offline decode first
  (free, works for older-format links), then a live resolve through
  Google's internal API as a fallback (works intermittently -- see
  "Design notes" below for measured hit rate). Falls back to posting the
  wrapper link itself if both fail; readers still get a working link
  either way. Also rewrites a `m.`-prefixed mobile subdomain (e.g.
  `m.belfasttelegraph.co.uk`) down to its canonical host, whether that
  shows up directly in a feed or only after Google News decoding.
- `KNOWN_LOCAL_OUTLETS` (plus `KNOWN_LOCAL_OUTLET_SUFFIXES` for shared
  naming patterns like NI government departments, and a substring match
  against `CATCHMENT_PLACES` for outlets whose own domain name signals
  their location) requires Google News entries resolve to a recognised
  local source, added after a Dundee, Scotland story (which has its own
  Hilltown district, unrelated to Hilltown, Co. Down) passed the keyword
  filter and got posted. **Currently enabled on the Google News feed**
  (`restrict_to_known_local_outlets: True`) -- it was disabled for one day
  in between after dropping *every* Google News story in one production
  run (link resolution failed on all of them, and an unresolved link
  couldn't be verified as local), then re-enabled once `news.google.com`
  itself was added to the allowlist, so an unresolved link no longer gets
  penalised by this check -- see "Design notes" below for the full story.
- Strips outlet-name suffixes from titles (`"Headline - Outlet"` /
  `"Headline | Outlet"`), using the feed's own source metadata when
  available and falling back to a generic strip otherwise.
- Strips Eventbrite's tracking query string from event links.
- Drops anything matching `EXCLUDE_KEYWORDS` in config.py (death notices,
  advertorials, property-for-sale listings).
- Drops anything whose *resolved* URL's domain is in `EXCLUDE_DOMAINS`
  (checked after Google News link decoding, so a paywalled outlet hiding
  behind a `news.google.com` wrapper link still gets caught). Currently
  just `irishnews.com` -- see "Design notes" below for why.
- Drops anything whose URL was already posted (local state), whose title is
  a close paraphrase of something posted in the last `DEDUP_WINDOW_DAYS`
  days (catches the same story covered by multiple outlets), whose URL or
  title already appears live on the subreddit (catches posts made outside
  this bot -- a moderator, a second bot instance), or whose URL is sitting
  in the subreddit's moderator spam queue.
- Before each run, approves any of the bot's *own* posts that Reddit's spam
  filter auto-removed (common for a newer/low-karma account) -- see
  `moderation.approve_own_spam_posts`. Never touches other authors' posts.
- Posts what's left as link posts, applies flair based on domain/keyword
  matching, capped at `MAX_POSTS_PER_RUN` per run with a delay between
  submissions.
- Separately, `bot.py run_events` posts newry.ie's own events feed to
  Reddit (Events flair forced), and `bot.py calendar` (or `make
  run-events` / `./run.sh run-events`) syncs those same events to a
  Google Calendar via a service account -- entirely independent of each
  other and of Reddit posting, so running one never blocks or requires
  the other. A plain `bot.py` with no argument (what cron actually runs)
  does all three: news, events-to-Reddit, and calendar sync. Reddit
  posting of events is deduped by exact URL, exact title, and fuzzy
  title (same mechanism as news) -- newry.ie's own events system has
  been seen splitting one multi-night show into several separate event
  listings that share one identical title, which exact-URL dedup alone
  doesn't catch; see "Design notes" below. Calendar sync (`sync_calendar`)
  is unaffected by this and keeps every distinct date as its own
  occurrence, which is what you want for a multi-night run. `run_events`
  also keeps a bot-managed sidebar widget in sync every run
  (`events_widget.py`): clickable event links, a GBP/EUR exchange rate
  (`exchange_rates.py`), the cheapest fresh local petrol/diesel price
  (`fuel_prices.py`), and the top 3 cheapest 500L heating oil suppliers in
  NI (`heating_oil.py`) -- see "Subreddit sidebar" below.

## Subreddit sidebar

Most of the subreddit's sidebar is Reddit configuration, entirely outside
this project. One widget, added 2026-10-05, *is* managed by this bot.
Documented here so anyone maintaining the subreddit later knows which is
which, rather than guessing from the sidebar alone.

- **Reddit sidebars in general are not controlled by this bot.** Rules,
  widgets, calendars -- anything in the sidebar is subreddit
  configuration, edited through Reddit's own mod tools, independent of
  everything below, with one exception (next bullet).
- **"Upcoming Events"** (a Markdown TextArea widget, `config.
  EVENTS_WIDGET_SHORT_NAME`) *is* managed by this bot -- `bot.run_events()`
  creates/updates it every run (see `events_widget.py`). Originally
  created under the name "Upcoming Events (Links)"; the user renamed and
  repositioned it directly in Reddit's UI on 2026-10-06, so the config
  constant was updated to match -- **if it's ever renamed again in
  Reddit's UI, update `EVENTS_WIDGET_SHORT_NAME` to match, or the next run
  will create a duplicate instead of updating it** (confirmed live: that's
  exactly what happened the one time this fell out of sync). Added
  because Reddit's native Calendar widget type (which used to sit
  alongside this one, since removed) has no way to show a per-event link
  at all (its `configuration` schema is six display-toggle fields, nothing
  more -- confirmed by reading praw's `widgets.py` directly), even though
  `calendar_sync.py` already writes a real link into every event it
  syncs. Below the event links, in order: a GBP/EUR exchange rate
  (`exchange_rates.py`), the cheapest fresh petrol/diesel price in the
  Newry area (`fuel_prices.py`), and the top 3 cheapest 500L heating oil
  suppliers in NI (`heating_oil.py`) -- each independently omitted if its
  own fetch fails or has nothing current to show. Don't hand-edit this
  widget's text in Reddit's UI -- the next run overwrites it.
- **Weather** is a Google Calendar on Steven's own Google account,
  sourced from Meteomatics' public ICS feed for Newry:
  `webcal://ical.meteomatics.com/calendar/Newry/54.175102_-6.34023/en/meteomat.ics`.
  Reddit's calendar widget polls that calendar directly -- no newry-bot
  code is involved anywhere in this path.
- **Currency** (EUR/GBP) is set up the same way -- a Google Calendar --
  but isn't currently shown on the sidebar (it wasn't displayed even
  before "Upcoming events" was removed, back when Reddit's per-subreddit
  Calendar-widget cap meant only two of these three Calendar widgets could
  show at once). If it's added back, its data isn't manually maintained
  either: a daily Google Apps Script named `EURtoGBPRedditSidebar`, also
  running on Steven's account, populates it.

## Setup

### 1. Create a Reddit script app

Do this yourself in a browser -- don't paste your Reddit password to any AI
tool, including this one:

1. Log in to Reddit as the account that will run the bot (recommend a
   dedicated bot account made a moderator of the target subreddit, not your
   personal login -- moderator status with "posts" permission is required
   for the spam-queue check to work; without it, the bot just skips that
   check and logs a warning).
2. Go to https://www.reddit.com/prefs/apps
3. Click "create app" / "create another app".
4. Name it, select **script**, set the redirect URI to
   `http://localhost:8080` (unused for script apps, but required).
5. Note the string under the app name (the **client ID**) and the
   **secret** field.

### 2. Configure

```bash
make env
```

This copies `.env.example` to `.env`. Edit `.env` and fill in your Reddit
credentials and target subreddit:

```
REDDIT_CLIENT_ID=...
REDDIT_CLIENT_SECRET=...
REDDIT_USERNAME=...
REDDIT_PASSWORD=...
REDDIT_USER_AGENT=newry-news-bot/1.0 by u/your_bot_account
REDDIT_SUBREDDIT=newry
```

`.env` is gitignored -- never commit it. Everything secret or
per-deployment (subreddit included) lives in `.env`; `config.py` only holds
non-secret tunables (feed list, exclude keywords, dedup thresholds) that are
safe to edit directly and check in.

**To point this at a different subreddit**, change `REDDIT_SUBREDDIT` in
`.env` -- no code change needed.

### 3. Check your environment, then install

```bash
make check     # verifies Python 3.10+, sqlite3 support, venv module
make install   # creates .venv, installs runtime dependencies into it
```

### 4. Run it once and check the log

```bash
make run
```

Check `bot.log` for what it found and whether it posted anything. Run it a
second time immediately after -- it should post nothing new (dedup working).

### 5. Run the tests

```bash
make test
```

Runs offline against mocked Reddit/feed data -- no credentials or network
needed. 215 tests covering the dedup similarity logic, link/title cleanup,
local state, Reddit moderation actions (spam-queue check, own-post
auto-approve, flair selection, live-duplicate check, alternate-source
commenting), the events/calendar pipeline, the news-age/max_entries
limiters, and the sidebar events widget (event-link formatting, the
GBP/EUR exchange rate, local fuel prices, and heating oil supplier
prices).

### 6. Schedule it

```bash
make cron-install
```

Adds a cron entry (hourly, 8am-8pm by default) that runs `bot.py` and
appends output to `cron.log`. Change the schedule with:

```bash
make cron-install CRON_SCHEDULE="*/15 * * * *"
```

Check what's installed with `make cron-status`, remove it with
`make cron-remove`. No always-on process, no extra AWS services -- a Python
interpreter wakes up, does one pass, exits. That should stay comfortably
within free-tier CPU/memory on a small instance.

### 7. Optional: Google Calendar sync

Entirely optional -- skip this and leave `GOOGLE_SERVICE_ACCOUNT_FILE` /
`GOOGLE_CALENDAR_ID` blank in `.env` if you don't want newry.ie's events
synced to a calendar. Reddit posting of events (`bot.py run_events`) works
independently either way; this only gates `bot.py calendar` /
`sync_calendar`.

1. In the [Google Cloud Console](https://console.cloud.google.com/), create
   a project (or use an existing one) and enable the **Google Calendar
   API** for it (APIs & Services -> Enable APIs and Services -> search
   "Google Calendar API" -> Enable). This is the single most likely thing
   to forget -- a service account with valid credentials still gets a 403
   ("Calendar API has not been used in project ... or it is disabled")
   until this is done.
2. Create a **service account** (APIs & Services -> Credentials -> Create
   Credentials -> Service Account), then create a JSON key for it
   (that service account -> Keys -> Add Key -> Create new key -> JSON) and
   download it. Note the service account's email address
   (`...@<project-id>.iam.gserviceaccount.com`) -- you'll need it next.
3. In [Google Calendar](https://calendar.google.com/), open the target
   calendar's Settings -> "Share with specific people or groups" -> add
   the service account's email with **"Make changes to events"**
   permission. Without this, syncing fails with a 403 even though
   everything else is configured correctly.
4. Put the downloaded JSON key file somewhere in the project (e.g.
   `.google-service-key/`, already gitignored) and set in `.env`:
   ```
   GOOGLE_SERVICE_ACCOUNT_FILE=.google-service-key/your-key-file.json
   GOOGLE_CALENDAR_ID=your_calendar_id@group.calendar.google.com
   ```
   The calendar ID is on the same Settings page as step 3, under
   "Integrate calendar".
5. Verify with `./run.sh run-events` (or `make run-events`) -- it needs no
   Reddit credentials, only the two variables above, and logs
   `Synced event to calendar: ...` per event on success.

## No `make` available locally?

If `make` doesn't run on your machine (e.g. macOS with no, or a broken,
Xcode Command Line Tools install, and not enough free disk space to fix
that -- CLT is 1-2GB+, and Homebrew's own `make` formula needs a working
CLT-free bottle or a full `homebrew-core` update, which for an old/shallow
Homebrew clone means re-fetching the entire repo history, easily several
GB), use [run.sh](run.sh) instead. It's a plain-shell script that does
exactly the same thing as every Makefile target below, with no dependency
on `make`, a C compiler, or any new package install -- only a working
`python3`:

```bash
./run.sh check
./run.sh install
./run.sh test
./run.sh env
./run.sh run
./run.sh run-events
./run.sh cron-install
./run.sh reset-db
```

It auto-detects a working `python3` (skipping a broken `/usr/bin/python3`
shim that shells out to `xcrun` and fails, if that's what's on the
machine) rather than assuming the first thing found on `PATH` actually
runs. Override with `PYTHON=/path/to/python3 ./run.sh install` if it picks
the wrong one.

On the actual deployment box (EC2/Linux), `make` is normally preinstalled
or a trivial `apt`/`yum` install with no such complications -- prefer the
Makefile there. `run.sh` exists specifically for local iteration on a
machine where `make` itself is the blocker.

## macOS + python.org installer: SSL certificates

If Python was installed via the official `.pkg` from python.org (rather
than Homebrew or already present on the box), every HTTPS request the bot
makes will silently fail with `SSL: CERTIFICATE_VERIFY_FAILED` -- the
installer doesn't wire up a CA trust store by default. This isn't loud:
`feedparser.parse()` just returns 0 entries with no exception, so every
feed looks empty rather than erroring, and the bot's own logging reports
it as "Feed ... returned no entries" -- easy to mistake for the feed
itself being down. It bit this project once already, during a Python
3.9 -> 3.13 upgrade (praw 8 requires 3.10+).

Fix (one-time, per Python install):

```bash
"/Applications/Python 3.13/Install Certificates.command"
```

(adjust the version number to match whichever python.org release is
installed). Confirm it worked with:

```bash
python3 -c "import feedparser; print(feedparser.parse('https://feeds.bbci.co.uk/news/northern_ireland/rss.xml').entries[:1])"
```

If that prints an entry instead of `[]`, certificates are wired up
correctly.

## Makefile reference

| Target | What it does |
|---|---|
| `make check` | Verify Python version + sqlite3/venv support before doing anything else |
| `make install` | Create `.venv`, install runtime dependencies |
| `make install-dev` | Same, plus pytest |
| `make env` | Create `.env` from `.env.example` if it doesn't exist yet |
| `make run` | Run the bot once (news, then events-to-Reddit, then Google Calendar sync) |
| `make run-events` | Google Calendar sync only -- no Reddit interaction at all, doesn't need Reddit credentials |
| `make test` | Run the test suite |
| `make cron-install` | Install the cron schedule (`CRON_SCHEDULE` overridable) |
| `make cron-remove` | Remove the cron schedule |
| `make cron-status` | Show whether the cron entry is currently installed |
| `make clean` | Remove `.venv` and Python caches (keeps `state.db`, logs) |
| `make reset-db` | Delete `state.db` (dedup history) -- asks for confirmation first unless `FORCE=1` |

## Design notes

**Don't trust Google News' search relevance -- verify independently
(2026-09-04 incident):** the Google News feed's query already OR-combines
every catchment place, so `keyword_filter` was originally left off it on
the assumption that Google's own results would already be relevant. That
assumption was wrong: in a live test run, it returned a crime story from
Dundee, Scotland with no connection to Newry at all, and it got posted.
Google News RSS relevance for a broad multi-term OR query isn't strict --
it pads or relates results loosely. Fix: the Google News feed now carries
the same `keyword_filter` as BBC, so every entry's relevance is checked
independently of what Google decided to return.

That fix had to be done carefully, though: the filter checks the entry's
*raw* title (and summary) as the feed supplied it, before the outlet-name
suffix is stripped off -- not the cleaned title. A human-interest story can
be genuinely local with no catchment place in the headline text itself,
signalled only by a local outlet name in the suffix (e.g.
`"... - Newry.ie"`); filtering on the already-stripped title would have
silently dropped stories exactly like that (confirmed against a real
example that a naive fix would have wrongly filtered out).

**Keyword matching alone can't tell WHICH place, but gating on it turned
out to cause more harm than good -- built 2026-09-04, disabled
2026-09-05:** a story about Hilltown, Dundee (a real district of Dundee,
Scotland, unrelated to Hilltown, Co. Down) passed the `keyword_filter` and
got posted, because "Hilltown" matched regardless of which Hilltown it
was. Single-word place names are fundamentally ambiguous across the UK and
Ireland; no amount of keyword tuning fixes that. A blocklist of non-local
(British/American) outlets was considered and rejected -- there's no
reliable way to classify a domain's "nationality," and the space of
possible false-positive outlets is unbounded. The fix built instead:
`KNOWN_LOCAL_OUTLETS`, a small curated allowlist of outlets that actually
cover the Newry area, with Google News entries required to resolve to one
of them.

That shipped and broke something else the very next day: this check runs
on the *resolved* URL, so it inherits the live-decode hit-rate problem
below -- and in a real production run, live decode failed on every single
Google News entry that run. Every one fell back to the unresolved
`news.google.com` wrapper link, which can't be matched against any outlet
domain and therefore got dropped -- including several stories that were
obviously, unambiguously on-topic (a Newry parking scheme, a Newry public
realm improvement scheme, a Newry & Armagh MLA selection, Newry football
results). Zero posts that run, from a feed that had found real content.
**`restrict_to_known_local_outlets` was `False` on the Google News feed**
for one day after this -- the mechanism (the allowlist, the check itself)
was left in place, not deleted, but switched off until it stopped
silently equating "couldn't verify" with "verified not local."

**Re-enabled the next day (2026-09-05), same feed, after a different fix:**
`news.google.com` itself was added to `KNOWN_LOCAL_OUTLETS` (see its entry
there for the full reasoning), so an entry that never resolves off the
wrapper link now passes this check instead of being dropped by it -- a
deliberate choice to keep favouring real coverage over catching the rare
ambiguous-placename false positive (the "Police investigate rape at
Dundee multi" incident is the concrete case that prompted re-enabling it
this way, rather than leaving it off indefinitely). Practically, this
means the check is currently a no-op for any entry that doesn't resolve
(most of them, per the live-decode hit-rate below) and only actually
discriminates once resolution succeeds -- `keyword_filter` remains the
real day-to-day gate until resolution reliability improves. The same day,
`KNOWN_LOCAL_OUTLET_SUFFIXES` (for shared naming patterns like NI
government departments) and a `CATCHMENT_PLACES` substring-of-domain
check were added alongside it, for the "once resolution succeeds" case
and for BBC/Newry.ie/Newry Democrat if they ever need this check too --
they don't currently, for the reasons in their own `config.py` comments.

**"Hilltown" itself was eventually dropped from `CATCHMENT_PLACES`
entirely (2026-10-01), after the no-op-while-unresolved gap above made it
unmanageable in practice:** with Tier 3 (browser resolution) unavailable
on the EC2 box the bot runs on, most Google News links never get past the
`news.google.com` wrapper, so `restrict_to_known_local_outlets` really
was a no-op for most entries, exactly as predicted when it was
re-enabled -- and "Hilltown" turned out to be ambiguous with more than
just Dundee: a Pennsylvania township, at minimum, judging by a steady
stream of unrelated US local-news stories (an animal clinic, a school,
an MLB writer's obituary) all matching on the bare word with nothing left
to catch them. Rather than keep expanding `KNOWN_LOCAL_OUTLETS` to chase
an open-ended set of unrelated outlets, "Hilltown" was removed from the
search/keyword terms outright -- it's still a real catchment town, this
is a data-quality workaround for a currently-broken resolution tier, not
a redrawn boundary, and worth revisiting once Tier 3 is reliable again.

**`KNOWN_LOCAL_OUTLETS` was rebuilt from real evidence, not guessed
(2026-09-05):** the original list was built from outlets observed during
a handful of live test fetches -- reasonable at the time, but it missed
`newrydemocrat.com`, which turned out to have 90 historical posts in
r/newry, more than every outlet except Google News and BBC. The fix:
authenticate via the bot's own Reddit credentials and scan r/newry's
actual posting history (new + top + hot listings combined, ~1192 unique
posts -- the practical ceiling of what Reddit's listing API returns) and
count real domains actually posted there, rather than relying on however
much of the feed happened to be sampled by hand. If this list needs
revisiting again later, re-running that scan is a better starting point
than guessing a second time.

**Resolving Google News links -- two tiers, and an honest note on hit rate
(2026-09-04 findings):** turning a `news.google.com/rss/articles/...`
wrapper link into the real article URL has gotten harder over time as
Google has changed the format.

*Tier 1, offline (`decode_google_news_url`):* the path segment after
`/articles/` is base64url-encoded protobuf. Links in the format Google used
through ~2023 store the real URL directly in it as a plain string --
decodable locally, free, can't be rate limited. Current-format live links
instead store an opaque token, so this tier now returns `None` for most of
what the feeds actually produce today. Kept because it's free when it does
work.

*Tier 2, live (`resolve_google_news_url_live`):* reverse-engineered from a
maintained open-source decoder that's itself been through 7 rewrites
chasing Google's changes -- this is an undocumented internal API, not a
stable contract. Fetches the article's Google News interstitial page
*following redirects* (critical detail: a single non-redirect-following
request lands on Google's consent wall and gets nothing; the full chain
picks up a consent-acknowledged parameter along the way and can land on a
page carrying a signature+timestamp pair), then redeems those against
Google's internal `batchexecute` RPC.

Both were verified end-to-end against a real production link -- and then,
testing a fresh batch of 10 links minutes later from the same machine, tier
2 succeeded on **0 of 10**, landing on an actual interactive consent page
each time instead of the interstitial with signing params. Same URL,
same code, different outcome within the same session. This looks like
IP/network-reputation-based gating (this dev machine has sent Google a lot
of automated traffic today) rather than a stable failure -- a fresh EC2 IP
that hasn't been hammering Google may do meaningfully better, but that's a
hypothesis, not something confirmed. **Test the actual hit rate from your
own EC2 after deploying** rather than assuming either outcome.

None of this affects whether posting works: when both tiers fail,
`clean_url` falls back to the original wrapper link, which still works
fine for a human clicking it in a real browser (Google's own JS completes
the redirect there) -- it just shows `news.google.com` as the link domain
instead of the real outlet. `bot.py` logs a warning every time this
fallback is used, so you can see the actual live/wrapped ratio in
`bot.log` over time.

**Excluding Irish News rather than trying to detect its paywall
per-article:** irishnews.com's pages load Google's "Subscribe with Google"
library (`subscriptions-control="manual"`, `swg.js`) -- Google's own
metered-paywall integration for publishers, confirmed by inspecting the
page source rather than assumed. Metering is per-visitor state (e.g. "N
free articles this month," tracked per browser), not a fixed property of
the article URL, so there's no way to classify a given link as "free" or
"paywalled" that would hold true for every Reddit reader who clicks it.
Rather than guess, `EXCLUDE_DOMAINS` drops it outright. The check runs
against the *resolved* URL (after Google News link decoding), since a
paywalled outlet can otherwise hide behind a `news.google.com` wrapper link
that doesn't reveal the real domain until decoded.

**Two layers of duplicate detection, deliberately:** local fuzzy-title
matching (`state.py` + `textutil.py`) catches the same story covered by
different outlets over multiple days, which Reddit's own post history can't
tell you. A live check against the subreddit's actual last 50 posts
(`moderation.py`:`fetch_recent_posts`) catches things local state can't --
a post made by a human moderator, or a fresh/wiped `state.db`. Similarity
uses an overlap coefficient (shared words / smaller title's word count)
rather than Jaccard (shared words / all words) because outlets rarely
phrase the same headline the same length; a plain Jaccard under-detects
real duplicates. It also refuses to call two titles duplicates when they
both contain numbers that don't match -- templated headlines (road-works
announcements, sports fixtures) share almost all their wording and differ
only in an amount, date, or score, which is exactly the part that makes
them different stories.

When a fuzzy headline match is against a post that's **still live** on the
subreddit -- most often two outlets covering the same story on the same day
with different headlines -- the bot doesn't just drop the second one. It
posts that article's link as a comment on the existing post instead
(`moderation.py`:`comment_with_alternate_source`), since a cross-outlet
match is never certain enough to silently bin. The comment is added once
and only once (recorded in `state.db`, and the function also scans for its
own earlier comment as a backstop).

## Known limitations

- **Newry Reporter's category feeds exist but are empty; its site-wide
  feed works but isn't useful.** Its homepage and most paths sit behind a
  Cloudflare bot challenge, but `/rss` and `<category>/rss` are
  specifically exempted from it and fetch fine with a plain request
  (`feedparser` confirmed getting through cleanly). The catch: every
  category feed tested (`/news/rss`, `/your-newry/rss`,
  `/sport/football/newry-city/rss`, etc.) returns a valid but genuinely
  empty channel, and the one populated feed (`/rss`, the site-wide one) is
  currently dominated by syndicated shopping/affiliate content, not local
  news -- not included as a source for that reason, not an access
  problem. Their `robots.txt` also explicitly disallows `ClaudeBot`,
  `anthropic-ai`, `GPTBot`, and similar -- doesn't technically restrict
  what this bot does (`feedparser`'s user-agent isn't any of those), but
  worth knowing. Newry Times (`newrytimes.com`) hasn't been investigated
  at all -- genuinely unknown either way, not confirmed blocked.
- **Events are wired up: newry.ie's own events feed, posted to Reddit
  and synced to Google Calendar independently** (`bot.py`'s `run_events`
  and `sync_calendar` -- see "What it does" above). The feed itself only
  gives a title, link, and `pubDate`; start/end time, venue, and price are
  scraped from each event's own page (`events.fetch_event_details`),
  since they're not in the feed at all.
- **Other events sources remain unavailable.** The council's `/events`
  page (`newrymournedown.org/events`) currently redirects to a page that
  500s. Eventbrite's public "search all events near X" API was
  discontinued in 2020 (only returns events for accounts you manage now).
  Google Events has no public API. `linkclean.py` still cleans Eventbrite
  URLs if you wire up an Eventbrite-based feed later.
- **Google News RSS is unofficial** and can return an empty feed if queried
  too often in a short window (confirmed while testing: a handful of
  requests within a couple of minutes got throttled to zero results, then
  recovered). At the default hourly cron interval this shouldn't bite in
  practice. The bot logs a warning and skips it for that run rather than
  failing -- the other three feeds keep working independently either way.
- **Spam-queue check needs mod permissions** (as does the own-post
  auto-approve, `moderation.approve_own_spam_posts`). If the bot account
  isn't a moderator with "posts" permission, both fail open -- log a
  warning and skip, rather than blocking every post -- since neither is
  the primary dedup mechanism.
- **Google News URL decoding is best-effort and its hit rate is unproven
  from your actual deployment.** If both decode tiers fail, the article
  still posts with the original `news.google.com` wrapper link -- readers
  get a working link, it just shows Google's domain instead of the real
  outlet.
- **`restrict_to_known_local_outlets` is currently a no-op in practice for
  the Google News feed** (the only one it's enabled on): `news.google.com`
  itself is in `KNOWN_LOCAL_OUTLETS` (a deliberate choice -- see "Design
  notes"), so an entry that never resolves off the wrapper link still
  passes. Since live resolution is currently unreliable (see above), this
  check only actually discriminates once resolution succeeds --
  `keyword_filter` remains the real gate in the meantime.
- **Newry.ie and Newry Democrat's own `pubDate` is unreliable**, not just
  occasionally off -- confirmed live, entries scattered from 2022-2025 (a
  Joomla feed quirk) and, separately, a "newest" entry over five weeks
  stale on a site that publishes daily. `MAX_NEWS_AGE_DAYS` is skipped for
  both and `max_entries` (feed-position-based, not date-based) used
  instead -- see "What it does" above.
- **Reddit's own AutoModerator is a second, independent lever worth
  using alongside this bot**, not instead of it -- the original prototype's
  author left a comment noting exactly this ("a lot of this can be taken
  care of by Automod anyway"). A native AutoMod rule blocking specific
  domains site-wide (not just from this bot) catches manually-submitted
  posts too, which nothing in this bot's own logic can see or prevent.

## What didn't get carried over from the prototype, and why

An earlier prototype (Flask app + `praw`) covered some of the same ground
via a different architecture: a webhook server (`app.py`) that accepted
POSTed `{url, title}` pairs from an external trigger, rather than polling
feeds itself. Two things from it weren't carried into this project:

- **The Flask webhook server itself.** This project already polls RSS on a
  schedule (the architecture chosen earlier for this build) -- running an
  always-on server as well would need something external feeding it POSTs,
  add a second moving part, and (per the "no extra AWS cost" constraint)
  isn't worth it unless there's a specific source that can only be
  ingested that way.
- **Google OAuth credentials** (`credentials.json`, `GCLIENT_ID` /
  `GCLIENT_SECRET`) present in the prototype's config were never actually
  referenced by any code path -- dead configuration, likely from an
  abandoned Gmail-API integration attempt. Not carried forward. If you want
  a Gmail-based ingestion path (e.g. parsing Google Alerts emails), that's
  a separate, real feature to design and build, not something to silently
  wire back in.

The prototype's `check_post`/spam-queue/flair logic, and the Google-News
title-cleaning idea, *were* carried over -- reworked as described above
where testing showed the original approach no longer worked, and given
offline unit test coverage (the prototype's tests hit live Reddit and
required real credentials to even run).

## Software Bill of Materials (SBOM)

High-level dependency inventory. Exact pinned versions are in
[requirements.txt](requirements.txt) / [requirements-dev.txt](requirements-dev.txt).

| Component | Version | Purpose | License |
|---|---|---|---|
| [praw](https://praw.readthedocs.io/) | 8.0.3 | Reddit API client -- fetching subreddit posts, submitting, flairing | BSD (Simplified) |
| [prawcore](https://github.com/praw-dev/prawcore) | 4.0.0 | Low-level HTTP/auth layer under praw; imported directly for its `Forbidden` exception | BSD |
| [feedparser](https://feedparser.readthedocs.io/) | 6.0.11 | Parses RSS/Atom feeds (BBC NI, newry.ie, Newry Democrat, Google News) | BSD-2-Clause |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | 1.0.1 | Loads `.env` into the process environment | BSD-3-Clause |
| [requests](https://requests.readthedocs.io/) | 2.34.2 | HTTP calls for event-page scraping, the live Google News link resolver (`linkclean.resolve_google_news_url_live`), newry.ie's homepage scrape, and the sidebar widget's external data (`fuel_prices.py`'s fuelcosts.co.uk API, `heating_oil.py`'s niliving.co.uk scrape, `exchange_rates.py`'s Frankfurter API) | Apache-2.0 |
| [google-auth](https://google-auth.readthedocs.io/) | 2.57.1 | Service-account JWT signing/auth for `calendar_sync.py`'s direct REST calls to the Calendar API | Apache-2.0 |
| [cryptography](https://cryptography.io/) | 48.0.1 | google-auth's JWT-signing backend; pinned below latest -- see requirements.txt's comment (an x86_64/Intel Mac architecture limit, not a Python-version one) | Apache-2.0 / BSD |
| [pytest](https://pytest.org/) | 8.4.2 | Test runner (dev/test only, not needed to run the bot) | MIT |
| Python standard library: `sqlite3` | (bundled) | Local dedup state (`state.db`) -- no separate database service to install or run | PSF |
| Python standard library: `base64`, `hashlib`, `html`, `json`, `re`, `logging`, `urllib.parse`, `zoneinfo` | (bundled) | Link decoding, deterministic Calendar event IDs, HTML-entity unescaping, text matching, logging, URL parsing, timezone-aware past-event checks | PSF |

No database server, message queue, or other infrastructure is required --
`state.db` is a single SQLite file created automatically on first run.
