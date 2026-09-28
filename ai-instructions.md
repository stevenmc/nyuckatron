# AI instructions for newry-bot

This file exists for whichever AI assistant works on this project next —
possibly not the one that built it. It is not user documentation (that's
[README.md](README.md)); it's a record of *why* the code looks the way it
does wherever that isn't obvious from reading it, so nothing here gets
"fixed" back into a bug that was already found and fixed once.

Every claim below was verified against real, tested behavior during
development (live requests, real Reddit posts, actual failures), not
assumed. Where something is a measured/probabilistic fact rather than a
logical certainty, that's said explicitly — don't firm it up into "always"
or "never" without re-testing.

If you're about to change something described here, that's fine — just
re-read the reasoning first and make sure the change accounts for it,
rather than silently reintroducing the original problem.

## Duplicate detection (`textutil.py`)

**Similarity uses an overlap coefficient (`shared / min(len_a, len_b)`),
not Jaccard (`shared / union`).** This looks like an odd choice — Jaccard
is the "normal" one. It was tried first and under-detected real duplicates:
different outlets rarely phrase the same headline at the same length, so
one title is often a subset of shared keywords plus its own extra words,
and Jaccard's union-based denominator punishes that length difference.
Don't "simplify" this back to Jaccard.

**Two titles are never called duplicates if they both contain numbers and
those number sets don't match exactly** (`_numbers` + the check in
`similarity`). This exists because templated headlines — road-resurfacing
announcements, sports fixtures — share almost all their wording and differ
*only* in a number (an amount, a date, a score), which is exactly the part
that makes them different stories. Without this guard, `"Kimmins announces
£262,000 resurfacing on A28..."` and `"...£250,000 resurfacing on A27..."`
score as near-identical and the second gets wrongly suppressed as a
duplicate. The check requires *exact* set equality, not just non-empty
overlap — a naive "do they share any number" check fails here too: amounts
like `262,000` vs `250,000` both contain the token `000` (comma-split), and
dates like `2026-08-15` vs `2026-08-11` both contain `2026` and `08`.

**Catchment place names (`config.CATCHMENT_PLACES`) are stopwords in
similarity comparisons**, not just in normalization for its own sake.
Without this, two unrelated stories that both happen to mention
"Rostrevor" would look artificially similar just from sharing that one
word, on top of whatever real overlap they have.

**Threshold is 0.5, chosen empirically against real headline pairs**, not
picked round-number-style. It was raised/lowered by testing against actual
fetched Google News/BBC data until real duplicate pairs cleared it and real
distinct pairs (including the numeric-guard cases above) didn't. See
`tests/test_textutil.py`'s `DUPLICATE_CASES` / `DISTINCT_CASES` — those are
real headlines captured live, not synthetic. If you change the threshold,
re-run against those fixtures, not just intuition.

## Feed relevance filtering (`bot.py`, `config.py`)

**`fetch_entries` checks `keyword_filter` against the entry's *raw* title
(before the outlet-suffix strip), not the cleaned title.** This looks
backwards — you'd expect to clean first, then filter. It's deliberate: a
human-interest story can be genuinely local with no catchment place in the
headline text itself, signalled only by a local outlet name in the suffix
(e.g. a story with no "Newry" in the headline, only in `"... - Newry.ie"`).
Filtering on the already-stripped title silently drops stories exactly like
that — confirmed with a real example (`'Team Mullen' to compete in Great
North Run...`, relevant only via its `- Newry.ie` suffix). If you reorder
this, that class of story disappears silently, with no error to notice.

**The Google News feed has a `keyword_filter` even though its search query
already OR-combines every catchment place.** This looks redundant — why
filter again after the query already asked for exactly this? Because
Google's relevance for a broad multi-term OR query isn't strict: in a real
production run, it returned a crime story from Dundee, Scotland with no
connection to any catchment place, and — because nothing re-checked — it
got posted. Don't remove this filter on the assumption that the query
alone is sufficient; it measurably isn't.

**`config.KNOWN_LOCAL_OUTLETS` and the `restrict_to_known_local_outlets`
per-feed flag exist and are fully implemented (`linkclean.is_allowed_domain`,
the check in `bot.py`).** History matters here: this was built on
2026-09-04 after a story about Hilltown, Dundee (a real district of
Dundee, Scotland, unrelated to Hilltown, Co. Down) passed the keyword
filter and got posted — single-word place names are genuinely ambiguous
across the UK/Ireland, and a blocklist of non-local outlets was considered
and rejected (no reliable way to classify a domain's "nationality," and
the space of possible false-positive outlets is unbounded — an allowlist
inverts that into something tractable). It shipped enabled on the Google
News feed only (BBC NI never needed it — its feed is Northern
Ireland-scoped by BBC's own editorial curation, not a keyword search, so
it structurally cannot surface an out-of-area story regardless of
wording).

**One day later (2026-09-05), it was disabled**, then **re-enabled the
same day with a real fix — it is currently `True` on the Google News feed
(the only one it's ever applied to).** The disable: a real production run
where link resolution failed on every single Google News entry (see the
next section on why that happens) — every entry fell back to an unresolved
`news.google.com` wrapper link, which `is_allowed_domain` couldn't verify
and therefore dropped. Result: 0 posts from a run where every headline was
genuinely, obviously on-topic. The re-enable fix: `news.google.com` itself
was added to `KNOWN_LOCAL_OUTLETS` (see that list's own comment), so an
entry that never resolves off the wrapper link now *passes* this check
instead of being dropped by it — deliberately favouring not losing real
coverage over catching the rare ambiguous-placename false positive.
**Practical consequence worth understanding, not a bug**: this makes the
check a no-op for any entry that doesn't resolve (currently most of them —
see the live-decode hit-rate notes below), and it only actually
discriminates once resolution succeeds. `keyword_filter` remains the real
day-to-day gate until resolution reliability improves. The same day,
`config.KNOWN_LOCAL_OUTLET_SUFFIXES` (shared naming patterns, e.g.
`-ni.gov.uk` for NI government departments) and a third `place_names`
substring-of-domain check (against `CATCHMENT_PLACES`) were added to
`is_allowed_domain` alongside the domain list — same three-way check,
now used together everywhere this function is called. **If you're asked
to revisit any of this, the above is current state, not history** — don't
re-disable it on the assumption the original 2026-09-05 problem is still
unaddressed.

**`KNOWN_LOCAL_OUTLETS`'s contents were rebuilt from real evidence on
2026-09-05, replacing a first version that was guessed from a handful of
live test fetches.** The guessed version missed `newrydemocrat.com`
entirely, which turned out to have 90 historical posts in r/newry — more
than every outlet except Google News and BBC. The rebuild method:
authenticate via the bot's own Reddit credentials (already in `.env` on
the deployment machine) and scan the subreddit's actual posting history
(`subreddit.new()` + `.top(time_filter="all")` + `.hot()`, deduplicated by
post id — combining all three gets a broader sample than any one listing
alone, since Reddit's listing API caps each at ~1000 posts regardless of
subreddit size), then count `submission.domain` across everything that
wasn't a self-text post. This is a one-off analysis script, not part of
the bot itself — there's nothing in the codebase that re-runs it
automatically, and there doesn't need to be; it's not something to wire
into a cron job, just something to re-run by hand if the list needs
revisiting again. If you're tempted to add a domain to this list based on
a hunch, prefer re-running that scan over guessing again — that's the
whole reason the first version needed replacing.

**Not every domain that appeared in that scan was added.** Generic
media/platform hosts (`i.redd.it`, `v.redd.it`, `youtube.com`,
`reddit.com`, `facebook.com`, `docs.google.com`, `archive.is` and
variants) were deliberately excluded even though they appeared, sometimes
frequently — they can host content about literally anything, so matching
on the bare domain would tell you nothing about Newry-relevance, which is
the entire point of this list. `irishnews.com` also appeared (8 times)
but wasn't added: `EXCLUDE_DOMAINS` already vetoes it for its paywall
regardless, so adding it to the allowlist too would only be confusing.
`news.google.com` itself was *initially* excluded for the more basic
reason that it's the unresolved wrapper link, not an outlet — but this was
**reversed on 2026-09-05** and it's now deliberately included; see the
`restrict_to_known_local_outlets` re-enable note above and
`KNOWN_LOCAL_OUTLETS`'s own comment in `config.py` for why. Don't
"correct" this back out on the old reasoning without re-reading that note
first.

**newry.ie has two distinct faces: its regular pages are blocked, but its
RSS feed endpoints are not.** Earlier testing (2026-09-04) found
`newry.ie`'s normal pages returning 403/empty bodies to plain requests —
concluded at the time to mean the whole site was unscrapable. That was an
overgeneralization: its Joomla-generated feed endpoints
(`/?format=feed&type=rss` for news, `/events?format=feed&type=rss` for
events) are not behind the same block and work cleanly with a default
`feedparser.parse()` call, no special headers needed — verified
2026-09-05. If a similarly-blocked site comes up again, check whether it
exposes an RSS/Atom `<link>` tag in its page `<head>` before concluding
it's a dead end; a feed endpoint can be reachable even when the rest of
the site isn't. The events feed in particular gives real structured data
(title, link, a `pubDate` start time) but no end time or venue — those
live only on the linked event page.

**A fourth feed, Newry Democrat (`rss.jsp?sezione=267`), was added
2026-09-06 — found via a linked "RSS" page, not guessed.** The site runs a
proprietary Java CMS ("VirtualCms") with no WordPress-style `/feed/`
endpoints; the real pattern only turned up by finding a linked "RSS" page
on the site itself (at `/section/807/rss`) that listed the real
`rss.jsp?sezione=N` URLs for its sections (145=Home, 267=News — used
here, 268=Sport, 269=Football). If another VirtualCms-based site comes up,
check for a similar linked RSS directory page before assuming there's no
feed at all.

**`config.MAX_NEWS_AGE_DAYS` (added 2026-09-05) and `max_entries` (added
2026-09-06) are deliberately mutually exclusive per feed — never apply
both to the same feed.** The age check drops anything older than
`MAX_NEWS_AGE_DAYS` by the feed's own published/updated date; confirmed
live it correctly filters BBC News NI and Google News. But Newry.ie and
Newry Democrat's own `pubDate` turned out to be unreliable, not just
occasionally wrong — Newry.ie's entries scatter across 2022-2025 with no
relation to actual recency, and Newry Democrat's "newest" entry was over
five weeks stale despite the site publishing daily. Applying the age
check to either dropped 100% of the feed (confirmed: 0/6 and 0/20
survived) — the exact same "filter silently kills all real coverage"
failure mode as the `restrict_to_known_local_outlets` incident above, just
a new instance of it. The fix: `max_entries` (set to 3 on both feeds)
takes only the first N entries *by feed position* instead, trusting the
feed's own ordering (assumed newest-first, the standard RSS convention)
rather than its date *values*. `bot.py`'s `fetch_entries` skips the age
check entirely whenever `max_entries` is set on a feed — if you add a new
feed with unreliable dates, use `max_entries` for it too rather than
tuning `MAX_NEWS_AGE_DAYS` around it, which would weaken the check for
every other feed that doesn't have this problem.

**`CATCHMENT_PLACES` deliberately excludes Dundalk** even though it's
geographically close to Newry. At ~40k people with its own council and
news cycle, including it as a search term pulled in a lot of Dundalk-only
stories with no real connection to Newry. This was an explicit tradeoff
decision, not an omission — don't add it back without re-confirming that's
still wanted.

**Title-based checks (`live_titles`, fuzzy similarity) run before URL
resolution in `main()`'s loop; URL-based checks (domain exclusion, exact
dedup, live/spam URL match) run after.** This ordering exists purely for
cost: resolving a Google News URL can cost two extra HTTP requests (see
below), so cheap title-only checks reject as much as possible before ever
paying for that. If you reorder checks in this loop, keep resolution as
late as reasonably possible. (One exception to "title checks fully
resolve the entry": a fuzzy match against a live post no longer `continue`s
immediately — it records `dup_of` and falls through to resolution and the
domain checks, because the alternate-source comment needs the resolved
URL. See the duplicate-detection note further down.)

## Google News URL resolution (`linkclean.py`)

This is the most fragile, most-tested-and-changed part of the project.
Read this before touching `decode_google_news_url`,
`resolve_google_news_url_live`, or `clean_url`.

**Two tiers exist because Google changed their link format between when
this was first built and when it was tested against live data days later.**
Tier 1 (`decode_google_news_url`) decodes the base64/protobuf blob in the
URL path locally — free, no network call, but only works for the
*older* link format (roughly pre-2024), where the real URL is stored
directly as a plain string in the decoded bytes. Verified against a real
2023 article link. Current-format live links instead store an opaque
token, so tier 1 now returns `None` for most of what the feeds actually
produce today. It's kept anyway because it's free when it does work and
costs nothing when it doesn't — don't remove it as "dead code."

**Tier 2 (`resolve_google_news_url_live`) depends on following redirects,
specifically.** A single non-redirect-following GET to the wrapper link
lands on Google's consent wall and gets nothing useful. The *full* redirect
chain (`allow_redirects=True`) picks up a consent-acknowledged parameter
along the way and can land on a page carrying a `data-n-a-sg` /
`data-n-a-ts` signature+timestamp pair, which gets redeemed against
Google's internal `https://news.google.com/_/DotsSplashUi/data/batchexecute`
RPC for the real URL. This whole mechanism was reverse-engineered from a
maintained open-source decoder (`SSujitX/google-news-url-decoder`) that has
itself been through 7 rewrites chasing Google's changes — it is not a
documented, stable API, and can break again at any time for reasons outside
this project's control. If it stops working, that's expected eventually,
not necessarily a bug in this code.

**Measured hit rate is not consistent and should not be assumed either
way.** In one test, this succeeded end-to-end against a real link. Minutes
later, a fresh batch of 10 different real links succeeded on 0 of 10 — same
code, same kind of link, landing on an actual interactive consent page
instead of the interstitial with signing params. This looks like
IP/network-reputation-based gating (the dev machine is a UK residential
IP, which Google's GDPR-consent logic treats differently from, say, a US
IP) rather than a stable success/failure state — but that's a hypothesis,
not confirmed. **Don't treat a single test run (success or failure) as
representative.** If asked to "fix" a low hit rate, the honest options are:
test from the actual deployment IP/region to get a real number, or accept
that this is inherently probabilistic and design around it carefully. The
`KNOWN_LOCAL_OUTLETS` check above was a first attempt at "design around
it" and it went wrong: coupling a hard requirement (verified local outlet)
to an unreliable dependency (link resolution) meant that whenever
resolution had a bad run, the requirement failed too, for everything, at
once. A more careful design-around would decouple them -- e.g. don't let
"resolution failed" silently become "treat as not local."

**Both tiers return `None` on *any* failure and never raise** — the broad
`except Exception` in `resolve_google_news_url_live` is deliberate, not
lazy error handling. This walks through two network calls and two layers
of ad-hoc parsing against an undocumented, unstable response format, where
almost any part could break independently (network error, missing
attributes, Google changing the JSON shape again, rate limiting). A narrow
except here would just mean a different, equally-unpredictable exception
type crashes the run next time Google changes something.

**`clean_url(url, live_decode=True)` has a `live_decode` parameter
specifically so tests and any future cost-sensitive caller can skip tier 2
without monkeypatching.** Any test exercising `clean_url` on a
Google-News-shaped URL that *doesn't* mock `resolve_google_news_url_live`
must pass `live_decode=False`, or it will make a real network call during
`pytest`. This bit a pre-existing test once already (see
`test_clean_url_falls_back_to_original_when_undecodable` in
`tests/test_linkclean.py`) — the regex that detects "is this a Google-News-
shaped URL" matches on `/articles/` in the path and doesn't care whether
the base64 payload is valid, so even a deliberately-garbage test fixture
URL will trigger a real live-decode attempt unless told not to.

## Paywall / outlet exclusion (`config.py` `EXCLUDE_DOMAINS`)

**`irishnews.com` is excluded outright, not filtered per-article.** It was
tempting to try to detect "is this specific article free or paywalled" —
rejected because the site runs Google's "Subscribe with Google" metered
paywall (confirmed by inspecting the page source: `subscriptions-control`,
`swg.js`), and metering is *per-visitor* state (e.g. "N free articles this
month," tracked per browser) — not a fixed property of the article URL.
There is no version of "check the URL" that would correctly predict whether
a given Reddit reader's click will be free. If another paywalled outlet
shows up, the same reasoning applies: check for a metering/paywall library
in the page source before assuming it's detectable, and if it's
per-visitor state, exclude the domain rather than trying to classify
individual articles.

## Reddit moderation actions (`moderation.py`)

**`fetch_spam_urls` and `flair_submission` both catch
`prawcore.exceptions.Forbidden` specifically and log a short warning
instead of a full traceback, then continue rather than blocking the post.**
This is expected, common behavior during testing against any subreddit the
bot account doesn't moderate (or whose flair templates don't match the
hardcoded IDs, which belong specifically to r/newry) — not a bug to chase
down every time it appears in logs. It fails open deliberately: a missing
permission shouldn't stop the bot from posting, since these are
supplementary safety/polish features, not the primary dedup mechanism.

**The flair template IDs in `_FLAIR_TEMPLATE_IDS` are real, specific to
r/newry, and were verified byte-for-byte against the original prototype's
values.** They will not work on any other subreddit (including a test
subreddit) unless that subreddit happens to have identically-configured
flair templates, which is not something you can set up by choosing the
same category names — Reddit generates these IDs itself. Don't "fix" a
flair 403 on a test subreddit by changing these IDs; they're correct for
the real target.

**Two layers of duplicate detection exist on purpose and check different
things:** local SQLite fuzzy-title matching (`state.py` + `textutil.py`)
catches the same story covered by different outlets over multiple days —
something Reddit's own post history can't tell you, since it only sees what
was actually posted, not near-miss headline variants. `moderation.
fetch_recent_posts`'s live check against the subreddit's actual last 50
posts catches what local state can't: a post made by a human moderator, or
a fresh/wiped `state.db`. Removing either one narrows what gets caught, not
just removes redundancy.

**A fuzzy title match against a *still-live* post is treated more softly
than a silent skip (added 2026-09-10).** `textutil.best_duplicate_match`
finds *which* recent live submission a candidate's headline matches; if
there is one, `main()` posts the candidate's link as a comment on that
submission (`moderation.comment_with_alternate_source`) instead of making
a second front-page post — the motivating case was two different outlets
covering the same train disruption on the same day, with different
headlines. Similarity across outlets is inherently uncertain, so the
comment (not a hard drop) is the point. It's only reached after the link
resolves to a real URL and clears the domain checks — an unresolved
wrapper link or a non-local/excluded URL is dropped, not commented. The
comment URL is written to `state.db` via `record_posted` so it isn't
re-evaluated (or re-commented) next run; `comment_with_alternate_source`
is also independently idempotent (it scans existing comments for its own
marker + the URL) as a backstop for a wiped `state.db`. If a fuzzy match
hits local state but nothing still live, the original silent-skip
behaviour is kept.

**`approve_own_spam_posts` (added 2026-09-06) actively approves the bot's
own posts out of the spam queue** — a step further than `fetch_spam_urls`
above, which only reads the queue for dedup purposes and never acts on it.
Reddit's automatic spam filter routinely catches posts from new/low-karma
accounts (exactly what this bot's account looks like to it) even when
nothing about the post is actually spam; since the bot submitted it, that's
already known, so it's approved outright rather than left hidden until a
human happens to check. Deliberately scoped to the bot's *own* authored
posts only, checked by username, case-insensitively — never touches other
authors' spam-queued submissions. Same fail-open behavior as
`fetch_spam_urls` if the account isn't a moderator with "posts" permission.
Called at the start of both `bot.main` and `bot.run_events`, not
`sync_calendar` (which has no Reddit interaction of any kind).

## Testing philosophy

**All tests are offline and mock every external call** (Reddit via
`unittest.mock.MagicMock`, feeds via monkeypatching `feedparser.parse`,
Google News resolution via monkeypatching `requests.get`/`requests.post`).
This is a deliberate departure from the original prototype, whose tests hit
live Reddit and needed real credentials to even run (`test_get_latest_posts`
asserted exactly 10 live posts came back — flaky and environment-dependent
by construction). If you add a test that needs real network/Reddit access
to pass, that's very likely the wrong design for this project, not a
necessary evil — find what to mock instead.

**Several test fixtures are real captured data, not synthetic examples**:
`REAL_GNEWS_URL`/`REAL_GNEWS_DECODED` in `test_linkclean.py` (a real 2023
article link and its real decoded URL), `REAL_SIGNATURE`/`REAL_TIMESTAMP`/
`REAL_BATCHEXECUTE_RESPONSE` (scraped and captured from an actual
successful live decode), and the headline pairs in `test_textutil.py`'s
`DUPLICATE_CASES`/`DISTINCT_CASES` (pulled from real BBC/Google News fetches
during development, chosen specifically because early similarity-metric
attempts got them wrong). These aren't arbitrary-looking magic strings —
they're pinned to real-world behavior on purpose, so keep them intact when
editing nearby tests rather than replacing with tidier-looking fake data.

## Project structure and tooling

**Both `Makefile` and `run.sh` exist, doing the same things, on purpose —
not duplication to clean up.** `run.sh` is a pure-shell reimplementation of
every Makefile target, for a development machine where `make` itself
doesn't run (this project was built on a Mac with broken/absent Xcode
Command Line Tools and too little free disk space to fix that). The actual
deployment target (EC2/Linux) is expected to have a working `make`, so the
Makefile stays the primary interface documented first in the README; `run.sh`
is the fallback. If you change one, change the other to match, or note the
divergence — don't delete either without checking why it was added.

**`load_dotenv()` in `bot.py` runs before `import config`, and the
`# noqa: E402` comments on the imports after it are intentional, not
lint-suppression laziness.** `config.py` reads `REDDIT_SUBREDDIT` from the
environment *at import time* (`os.environ.get("REDDIT_SUBREDDIT", "newry")`),
so `.env` must be loaded first or the subreddit silently falls back to the
hardcoded default regardless of what's actually in `.env`. Reordering these
imports "to be tidy" (imports at the top, before any other code) would
reintroduce that bug silently — there's no error, it just quietly points at
the wrong subreddit.

**`requests` was removed from `requirements.txt`, then re-added later in
the same project's history.** It was dropped when the offline-only
`decode_google_news_url` looked sufficient, then reinstated when
`resolve_google_news_url_live` was added and needed it for real HTTP calls.
If you see this dependency and wonder whether it's actually used: yes, by
`linkclean.py`'s live decode tier.

**`config.STATE_DB_PATH` pruning in `state.py`'s `prune_old` uses
`window_days * 3` as the deletion cutoff, not `window_days`.** This is
deliberate margin, not an off-by-something: the dedup window itself is
`window_days`, but rows are kept for 3x that before deletion so the
database doesn't grow forever on a long-running low-memory box, without
discarding rows that a same-window dedup check still legitimately needs.

## What's genuinely NOT wired up, and why (don't "complete" these)

- **Google OAuth credentials** (`GCLIENT_ID`/`GCLIENT_SECRET` in the
  original prototype's config, a `credentials.json` file) were never
  referenced by any code path in the original project — confirmed by
  grepping the entire codebase for any Google API usage beyond the news
  URL handling. Almost certainly an abandoned attempt at a Gmail-API
  integration (e.g. parsing Google Alerts emails). Nothing in this project
  uses them, and they weren't carried forward. **Google Calendar itself
  has since been built** (`calendar_sync.py`, 2026-09-05) — but as a fresh
  service-account integration, not by reviving these dead OAuth creds; see
  the "Google Calendar sync" section below. A Gmail-based ingestion path
  (e.g. parsing Google Alerts emails) is still new work to design, not
  something these old credentials were ever wired towards.
- **A Flask webhook server** (the original prototype's actual architecture
  — it received POSTed `{url, title}` pairs from an external trigger,
  rather than polling feeds itself) was deliberately not adopted. This
  project polls feeds on a cron schedule instead; running a webhook server
  as well would need an external service feeding it and would just be a
  second moving part for no clear benefit given the polling design already
  covers the same ground.
- **Events sources beyond newry.ie's own feed** (council site, Eventbrite,
  Google Events) are not wired up. The council's `/events` page currently
  500s on redirect, Eventbrite's public discovery API was discontinued in
  2020, and Google Events has no public API at all (would need a paid
  third-party scraper like SerpApi). `linkclean.strip_eventbrite_tracking`
  exists and works, ready for if an Eventbrite-based feed gets added
  later — it's not dead code, it's just currently unused by any active
  feed. **newry.ie's own events feed itself is fully wired up**, though
  (`bot.py`'s `run_events` + `sync_calendar`, `events.py`,
  `calendar_sync.py`) — don't mistake this bullet for "events aren't
  wired up at all."

## Google Calendar sync (`calendar_sync.py`, `events.py`, `bot.py`)

**Two independent pipelines, not one, and that's deliberate.**
`bot.py run_events` posts newry.ie's events feed to Reddit; `bot.py
calendar` (also `make`/`./run.sh run-events` despite the name) syncs the
same feed to Google Calendar via a service account. Neither calls the
other, and `calendar` mode needs no Reddit credentials at all — this was a
deliberate split (they used to be one function) so that testing or
scheduling the calendar side never risks posting to Reddit as a side
effect, and vice versa. A plain `bot.py` with no argument (what cron
actually runs) does both, plus the news pipeline.

**`sync_calendar` re-syncs the entire feed every run, unconditionally,
ignoring `state.db`'s "posted to Reddit" history on purpose.**
`calendar_sync.sync_event` upserts by a deterministic ID derived from the
event's link (`event_id_for`), so re-syncing something already synced is
a cheap no-op update, not a duplicate — and this means a calendar sync
that failed on a prior run (a transient Calendar API error, say) keeps
getting retried instead of being silently skipped forever just because
the event was already posted to Reddit. Two real bugs were found and
fixed via live testing here, neither visible from reading the code alone
— see `calendar_sync.py`'s own module docstring for both (the Calendar
API needing to be enabled once in the GCP console, and `event_id_for`'s
original "newryie" ID prefix containing characters Calendar's event-ID
charset doesn't allow).

**Past events are skipped before syncing** (`events.is_in_the_past`,
added 2026-09-06) — treats a start time of `None` as NOT past (can't
verify age, so don't drop it; `calendar_sync.sync_event` already refuses
a `None` start separately anyway), and compares against Europe/London
wall-clock time specifically (`zoneinfo`), not the host machine's own
local time — important because the actual deployment target is an EC2
box that won't necessarily be in that timezone.

## Newry.ie link substitution for unresolved Google News links (`bot.py`, `config.py`)

**Added 2026-09-24 as an explicit stopgap** — Tier 3 (a real headless
browser, `linkclean.GoogleNewsBrowserResolver`) is the most reliable way
to resolve a Google News wrapper link, but it can't run on the current
EC2 box (GLIBC 2.28 required, Bionic has 2.27; see the resolver's own
docstring), so Tiers 1/2 alone leave most Google News entries unresolved.
Since a large share of what the Google News search surfaces already ran
on Newry.ie too, `bot.fetch_newry_ie_candidates` independently scrapes
Newry.ie's homepage (same `max_entries`-limited window as the main
pipeline, but *not* filtered by that feed's own `keyword_filter` — a
Newry.ie entry that got filtered out there can still be exactly what a
differently-worded Google News entry is describing) and
`bot.find_newry_ie_substitute` fuzzy-matches the candidate title against
it. On a match, `main()` uses the Newry.ie link straightaway — checked
*before* even attempting to resolve the Google News wrapper link at all
(not just as a fallback after Tiers 1/2 fail, the original 2026-09-24
design — reordered the same day after a real miss, see the incident
below), and *before* any of the downstream domain-exclusion/known-
outlet/dedup checks run, so it benefits from all of them same as any
other link (and, usefully, if Newry.ie's own entry for the same story
was already posted earlier in the same run or a previous one, the
exact-URL dedup check now catches the Google News entry as a repost too,
which it couldn't before when the two entries carried different-looking
URLs).

**Deliberately a stricter threshold than the general fuzzy-dedup one**
(`config.NEWRY_IE_LINK_MATCH_THRESHOLD = 0.75` vs.
`SIMILARITY_THRESHOLD = 0.5`) — dedup only decides whether to *skip or
comment on* a post, so a false positive there is low-cost; this decides
*which URL to actually post*, so a false positive here would silently
send readers to the wrong article under the original (correct) headline.
Remove this whole mechanism once the EC2 box is upgraded and Tier 3 is
reliable again — it's a workaround for a specific, temporary infrastructure
gap, not a permanent design choice.

**Incident, same day: Newry.ie's own RSS feed had silently stopped
updating, and `fetch_newry_ie_candidates` was reading it.** A reader
reported that a specific same-day Newry.ie story ("Evora hospice joins
call to end palliative care 'postcode lottery'") should have matched and
didn't. Investigation found newry.ie's RSS feed
(`https://www.newry.ie/?format=feed&type=rss`, the same URL verified
working on 2026-09-05 — see the FEEDS entry's original comment, since
replaced) had returned the identical 6 entries, dated 2022 through mid-
2026 and not even in chronological order, for every run since roughly
that date — nearly three weeks — while the live site kept publishing new
articles daily, confirmed by browsing the homepage directly and finding
both that day's top story and the reported Evora Hospice article in its
HTML, neither anywhere in the RSS output. The candidate-matching logic
itself was never the problem; it was working correctly against a feed
that had gone stale without erroring, returning the same well-formed but
frozen response every time — nothing in `fetch_newry_ie_candidates` (or
feedparser) would raise or warn about that, since a feed returning old
entries indefinitely looks identical, from this code's point of view, to
a feed that's still updating but just doesn't have new stories yet. **The
same broken feed was also the Newry.ie entry's `url` in `config.FEEDS`
itself** — meaning the bot's *direct* Newry.ie-sourced Reddit posts (the
`fetch_entries()` pipeline, independent of the substitution feature) had
also been silently stalled since 2026-09-05, confirmed via `bot.log`
showing no `Posted [Newry.ie]` line in that entire window despite the
site publishing normally. The fix: `bot._scrape_newry_ie_homepage`
replaces the feed URL for both use sites with a direct HTML scrape of
`https://www.newry.ie/` (parsed by `bot._parse_newry_ie_homepage_articles`,
matching `<h3 class="raxo-title">`/`<h4 class="raxo-title">` article
links — Joomla's "Raxo" template, confirmed against the real page,
featured/hero items using `<h3>` and normal list items `<h4>`), which is
confirmed newest-first by the dates newry.ie displays next to each story
— unlike the old feed's `pubDate`, which config.py's FEEDS comment had
already documented as unreliable back on 2026-09-06, just not to the
point of being frozen entirely. **Lesson for future feed integrations on
this project: a feed that stops updating doesn't necessarily error or
even look different in shape — it can keep returning a perfectly
well-formed response forever. Don't assume "still parses, still has
entries" means "still current"; if a feed's own freshness matters (as
opposed to `max_entries`-style position-trust, which this incident shows
isn't a complete substitute either), periodically sanity-check its
newest entry's date against reality, not just its structure.**

## `run_events` reposted a multi-date event listing four times (`bot.py`)

**Incident, 2026-09-28: newry.ie's own events system split one multi-night
show into four separate event listings sharing one identical title, and
`run_events` posted all four as separate Reddit threads.** "Newry Youth
Performing Arts presents Dear Evan Hanson" got four event pages
(`/events/2522-...` through `/events/2525-...-03-10-2026`, one per
performance date), each a genuinely different URL/ID but an exactly
identical title. All four posted to Reddit 90 seconds apart, in the same
run. Two compounding bugs, both fixed the same day:

1. **The old docstring's premise was wrong.** It claimed a single
   first-party feed couldn't have the "same story, different link"
   problem that motivates news's fuzzy-title dedup, so exact-URL dedup
   was "enough." This incident is direct proof otherwise -- newry.ie's
   own event system can and does produce multiple links for what a reader
   experiences as one announcement. `run_events` now also runs the exact
   same fuzzy-title check `main()` already used for news
   (`textutil.is_duplicate_story` against `state.recent_titles`, same
   `SIMILARITY_THRESHOLD`). That threshold isn't up for renegotiation even
   though it occasionally false-positives on short titles -- confirmed
   with the user 2026-09-25 for the news side (see `config.py`'s
   `SIMILARITY_THRESHOLD` comment), and it applies here for exactly the
   same reason: Newry.ie messing up and posting the same thing twice,
   sometimes under a short title, is the real, recurring failure mode
   this exists to catch, and that's worth more than avoiding the
   occasional coincidental false positive.
2. **`live_titles`/`live_urls` were fetched once before the loop and never
   updated as the loop itself posted** -- so even the *existing*
   exact-title check (`title in live_titles`) missed repeats within a
   single run, because the set it was checking against was already stale
   by the second event. `main()`'s loop already updates its own
   `live_titles`/`live_urls`/`recent_titles` after every post for exactly
   this reason; `run_events` now does the same.

**Calendar sync is deliberately untouched.** `sync_calendar` keeps
posting all four dates as separate calendar events, which is correct --
a reader wants each performance date on the calendar, not one merged
entry. Only the Reddit-posting side had a real duplicate problem; the
two pipelines' dedup needs are genuinely different, not an oversight.
