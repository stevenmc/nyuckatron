"""Bot configuration. Non-secret tunables live here and can be edited
directly. The target subreddit and Reddit credentials are read from the
environment (.env) instead, per-deployment settings that shouldn't require a
code change -- see .env.example."""

import os

# Set REDDIT_SUBREDDIT in .env to point this whole bot at a different
# subreddit without touching any code.
SUBREDDIT = os.environ.get("REDDIT_SUBREDDIT", "newry")

# Google Calendar sync (calendar_sync.py) is entirely optional -- if either
# of these isn't set in .env, calendar_sync.is_configured() is False and
# every sync attempt is skipped with a one-time log line, no error. Reddit
# posting of events works independently either way.
GOOGLE_SERVICE_ACCOUNT_FILE = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
GOOGLE_CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID")

# newry.ie's events RSS only gives a start time (see events.py) -- an event
# page with no "Event End Date" field falls back to this duration.
DEFAULT_EVENT_DURATION_HOURS = 1

EVENTS_FEED_URL = "https://www.newry.ie/events?format=feed&type=rss"

# r/newry's native sidebar "Calendar" widget couldn't show per-event
# links at all -- confirmed by reading praw's widgets.py directly and
# inspecting its live data: its configuration schema is only six
# display-toggle fields, and its data has no URL field, even though
# calendar_sync.py already writes a real link into every event. It's
# since been removed from the sidebar. bot.run_events() (via
# events_widget.py) creates/updates a bot-owned sidebar TextArea widget
# under this exact name -- a list of Markdown links -- every run.
#
# Originally "Upcoming Events (Links)"; renamed 2026-10-06 to match a
# manual rename+reposition the user did directly in Reddit's widget
# editor (to plain "Upcoming Events") -- sync_events_widget finds the
# existing widget to update by matching this name exactly, so this
# constant has to track whatever the widget is actually called on Reddit
# right now, not the other way around. If it's ever renamed again in
# Reddit's UI, update this to match or the next run will create a
# duplicate instead of updating it (confirmed live: that's exactly what
# happened the one time this fell out of sync). Keep it under 30
# characters: praw/Reddit's own limit on a widget's shortName.
EVENTS_WIDGET_SHORT_NAME = "Upcoming Events"

# Empty values inherit the subreddit's default widget theme. Confirm this
# looks right during the dry-run/visual-check step before relying on it.
EVENTS_WIDGET_STYLES = {"backgroundColor": "", "headerColor": ""}

# Towns/villages in r/newry's actual catchment area -- a story is in-scope
# if it mentions any one of these, not just "Newry" itself.
#
# Dundalk is deliberately excluded even though it's geographically close: at
# ~40k people with its own council and its own local news cycle, including
# it as a blanket search term pulled in a lot of Dundalk-only stories with
# no real connection to Newry.
#
# "Hilltown" was removed 2026-10-01 after its false-positive rate became
# unmanageable -- it's not just ambiguous with Dundee's Hilltown district
# (the original 2026-09-04 incident that motivated the whole
# KNOWN_LOCAL_OUTLETS / restrict_to_known_local_outlets mechanism below);
# there's also a Hilltown Township, Pennsylvania, and evidently others,
# confirmed live by a steady stream of totally unrelated US/Scottish
# stories (a Williamsburg animal clinic, an MLB writer's hometown
# obituary, a Dundee bin-collection complaint...) all matching on the bare
# word. The known-outlets domain check was *supposed* to catch these on
# the Google News feed, but can't currently do its job: most Google News
# links never resolve past the news.google.com wrapper (Tier 3, the
# browser resolver, is unavailable on the current EC2 box -- see
# linkclean.GoogleNewsBrowserResolver), and news.google.com itself is
# deliberately in KNOWN_LOCAL_OUTLETS as a fail-open no-op for exactly
# that case -- so an unresolved "Hilltown" story passes the domain check
# trivially regardless of its real origin, leaving keyword_filter as the
# only actual gate. Revisit once Tier 3 is reliable again (see
# NEWRY_IE_LINK_MATCH_THRESHOLD's comment for the same EC2-upgrade
# dependency) -- the underlying place is still genuinely in the
# catchment area, this is a data-quality workaround, not a redrawn
# boundary.
CATCHMENT_PLACES = [
    "Newry",
    "Mayobridge",
    "Camlough",
    "Rostrevor",
    "Warrenpoint",
    "Carlingford",
    "Crossmaglen",
    "Lislea",
    "Omeath",
]

# Known Newry/South Down/South Armagh area news outlets. Used to gate
# GOOGLE_NEWS-sourced entries (see FEEDS below) -- NOT to gate BBC NI or
# newry.ie's own feeds, which don't need it (see restrict_to_known_local_
# outlets there for why).
#
# Rebuilt 2026-09-05 from real evidence, not guessed: scanned r/newry's own
# posting history (1192 unique posts via combined new/top/hot listings,
# the practical cap on what Reddit's listing API returns) and counted
# domains actually posted there. Two genuinely-common local outlets --
# newrydemocrat.com (90 historical posts) and downnews.co.uk (4) -- were
# missing from the first, guessed version of this list; this is exactly why
# guessing was replaced with checking. Domains below appeared >=2 times in
# that scan unless marked otherwise (a single appearance was still included
# for an obviously legitimate official/institutional source, e.g. police,
# council, government).
#
# Deliberately NOT included despite appearing in the scan:
#   - irishnews.com: appeared 8 times historically, but EXCLUDE_DOMAINS
#     already vetoes it for its paywall regardless of this list -- adding
#     it here would just be confusing, not additionally permissive.
#   - Generic media/platform hosts (i.redd.it, v.redd.it, i.imgur.com,
#     youtube.com, youtu.be, reddit.com, facebook.com, docs.google.com,
#     msn.com, archive.is/.today, change.org): these can host content
#     about literally anything, so matching on the bare domain tells you
#     nothing about Newry-relevance -- the entire point of this list.
#     archive.ph is the one exception -- see its own entry below.
#   - One-off (`count == 1`) non-institutional domains from the scan
#     (e.g. a personal blog, a single small business site): too thin an
#     evidence base to call "known." Add them back individually if they
#     turn out to be recurring, not speculatively.
#
# Extended 2026-09-05 (second pass) with outlets identified by hand rather
# than the posting-history scan: paradewatch.co.uk, narrowwaterbridge.com,
# thejournal.ie (jrnl.ie, its short-link domain, was already here),
# clanryegroup.com, evorahospice.org, warrenpointport.com, translink.co.uk,
# gaeliclife.com, and archive.ph specifically (an exception to the
# archive.is/.today exclusion above -- still a generic mirror that can host
# anything, but kept anyway per an explicit decision to allow it, since it's
# routinely used to get around outlet paywalls for stories that are
# genuinely about this area).
#
# news.google.com is included here too, as of the same date, reversing the
# earlier "unresolved wrapper link, not an outlet" exclusion -- deliberately:
# see the restrict_to_known_local_outlets re-enable note in FEEDS below for
# why. This makes the allowlist a no-op for any entry whose link never
# resolves off news.google.com, which is most of them right now -- accepted
# on purpose, to favour not losing real local news over rejecting a
# false positive.
#
# Irish political party official sites, added 2026-09-05 -- deliberately
# excludes Saoradh (whose real site is saoradh.irish, not saoradh.ie --
# verified, so it doesn't get added later by someone assuming otherwise).
# Sourced from each party's official site, not guessed; not necessarily a
# complete list of every registered Irish party -- add more as needed.
KNOWN_LOCAL_OUTLETS = [
    "bbc.co.uk",
    "bbc.com",
    "newrydemocrat.com",
    "belfasttelegraph.co.uk",  # includes m.belfasttelegraph.co.uk via subdomain match
    "belfastlive.co.uk",
    "newsletter.co.uk",  # Belfast News Letter
    "armaghi.com",  # Armagh I
    "4ni.co.uk",
    "newry.ie",
    "newrytimes.com",
    "newryreporter.com",
    "destinationnewry.com",
    "newrymournedown.org",  # council
    "downnews.co.uk",
    "rte.ie",
    "irishtimes.com",
    "jrnl.ie",  # TheJournal.ie's short-link domain
    "thejournal.ie",
    "u.tv",
    "itv.com",  # UTV
    "eventbrite.co.uk",
    "eventbrite.ie",
    "eventbrite.com",
    "ticketsource.co.uk",
    "psni.police.uk",  # official -- kept despite low count
    "northernireland.gov.uk",  # official
    "daera-ni.gov.uk",  # official (NI dept. of agriculture)
    "sluggerotoole.com",  # NI politics/current-affairs
    "farminglife.com",  # already used in flair matching; genuinely recurring source
    "paradewatch.co.uk",
    "narrowwaterbridge.com",
    "archive.ph",
    "clanryegroup.com",
    "evorahospice.org",
    "warrenpointport.com",
    "translink.co.uk",  # NI public transport authority
    "gaeliclife.com",
    "fiddlersgreenfestival.com",  # local festival; add more local festival sites here as they come up
    "news.google.com",  # see note above -- deliberate, not an oversight
    "sinnfein.ie",
    "sdlp.ie",
    "allianceparty.org",
    "mydup.com",  # DUP
    "uup.org",
    "fiannafail.ie",
    "finegael.ie",
    "labour.ie",
    "greenparty.ie",
    "socialdemocrats.ie",
    "aontu.ie",
    "pbp.ie",  # People Before Profit
]

# Domain-suffix patterns (not fixed domains, not true subdomains -- see
# linkclean.is_allowed_domain) for a whole family of sites that share a
# naming convention. Northern Ireland executive departments all use
# "<name>-ni.gov.uk" (daera-ni.gov.uk above is one instance of this same
# pattern) -- this catches the rest without enumerating each one by hand.
KNOWN_LOCAL_OUTLET_SUFFIXES = [
    "-ni.gov.uk",
]

# Each feed is polled every run.
#
# `keyword_filter` (a list of lowercase substrings), if set, means an entry
# is only kept when at least one of the filter words appears in its title or
# summary -- use this for broad/national feeds you only want catchment-area
# stories from.
#
# `restrict_to_known_local_outlets`, if True, means an entry is only kept
# if its resolved URL's domain is in KNOWN_LOCAL_OUTLETS, matches a
# KNOWN_LOCAL_OUTLET_SUFFIXES pattern, or contains a CATCHMENT_PLACES name
# (see linkclean.is_allowed_domain -- bot.py passes all three). Added
# 2026-09-04 after a keyword_filter match on "Hilltown" turned out not to
# be enough to confirm a story was about Hilltown, Co. Down -- Dundee,
# Scotland also has a district called Hilltown, and a story about it
# passed the keyword filter and got posted.
#
# DISABLED 2026-09-05, then RE-ENABLED the same day: it was switched off
# after one production run where link resolution failed on every single
# Google News entry (see linkclean.py's live-decode hit-rate notes), so
# every entry fell back to an unresolved wrapper link, which this check
# couldn't verify and therefore dropped -- 0 posts from a run where every
# headline was genuinely, obviously on-topic. Re-tested live before
# re-enabling: resolution is still failing 100% of the time (0/20 in a
# fresh check), so the underlying reliability problem hasn't gone away.
# What's different now: news.google.com itself was deliberately added to
# KNOWN_LOCAL_OUTLETS (see its entry there), so an unresolved wrapper link
# passes this check instead of being dropped by it -- a conscious choice
# to prioritise not losing real coverage over catching the rare
# ambiguous-placename false positive (the "Police investigate rape at
# Dundee multi" incident, keyword_filter-matched, is the concrete case
# that prompted this). Practical effect: this check is a no-op for any
# entry that doesn't resolve (currently most of them) and only actually
# discriminates once resolution succeeds -- keyword_filter remains the
# real gate until resolution reliability improves. The three-way
# KNOWN_LOCAL_OUTLETS / KNOWN_LOCAL_OUTLET_SUFFIXES / CATCHMENT_PLACES
# matching added the same day exists for exactly that "once resolution
# succeeds" case, and for the other two feeds below if they ever need it.
#
# BBC NI never needed this: its feed is Northern Ireland-scoped by BBC's
# own editorial curation, not a keyword search -- a Dundee story
# structurally can't appear in it regardless of wording.
FEEDS = [
    {
        "name": "BBC News NI",
        "url": "https://feeds.bbci.co.uk/news/northern_ireland/rss.xml",
        "keyword_filter": CATCHMENT_PLACES,
        "restrict_to_known_local_outlets": False,
    },
    {
        # scrape_homepage=True, replacing this feed's RSS URL entirely as
        # of 2026-09-24: newry.ie's own feed (https://www.newry.ie/
        # ?format=feed&type=rss, verified working 2026-09-05) turned out to
        # have silently stopped updating around that same date -- confirmed
        # live nearly three weeks later still returning the identical 6
        # stale entries (dated 2022 through mid-2026, not even in
        # chronological order) while the actual website kept publishing new
        # articles daily. Discovered when a reader reported a same-day
        # newry.ie article (the Evora Hospice "postcode lottery" story)
        # that bot.find_newry_ie_substitute had no way to see, because it
        # was never in the feed's candidate list -- see ai-instructions.md
        # for the full incident. bot.py's _scrape_newry_ie_homepage now
        # parses newry.ie's homepage HTML directly instead (confirmed
        # genuinely newest-first, matching the dates newry.ie displays next
        # to each story) -- no special headers needed, a plain `requests`
        # GET with its default user agent gets a normal 200. Doesn't need
        # restrict_to_known_local_outlets: it's newry.ie's own editorial
        # output, not an aggregator search, so the ambiguous-placename
        # problem (see Hilltown/Dundee in KNOWN_LOCAL_OUTLETS's history)
        # doesn't apply here either.
        #
        # max_entries=3 still applies on top of the scrape, unchanged from
        # when it was added 2026-09-06 to work around the old feed's
        # unreliable pubDate: keeps both this feed's own direct-posting
        # pipeline and the Google News substitution candidate list scoped
        # to only the newest few articles, same as before.
        "name": "Newry.ie",
        "scrape_homepage": True,
        "keyword_filter": CATCHMENT_PLACES,
        "restrict_to_known_local_outlets": False,
        "max_entries": 3,
    },
    {
        # Verified working 2026-09-05. newrydemocrat.com runs on a
        # proprietary Java CMS ("VirtualCms", branded internally as "Alpha
        # Newspaper Group") -- not WordPress, so none of the standard
        # /feed/ guesses apply. The real pattern, found via a linked "RSS"
        # page at /section/807/rss rather than guessed: rss.jsp?sezione=N,
        # confirmed as genuine RSS 2.0 with real content for sezione=145
        # (Home), 267 (News -- used here), 268 (Sport), 269 (Football).
        # Doesn't need restrict_to_known_local_outlets for the same reason
        # Newry.ie doesn't: it's this outlet's own editorial feed, not an
        # aggregator search.
        #
        # max_entries=3, added 2026-09-06: same unreliable-pubDate problem
        # as Newry.ie above -- confirmed live, newest-claimed entry was
        # over five weeks old despite the site publishing daily, and
        # MAX_NEWS_AGE_DAYS dropped 100% of this feed (0/20 survived). See
        # Newry.ie's comment above for how max_entries substitutes for the
        # date check here.
        "name": "Newry Democrat",
        "url": "https://www.newrydemocrat.com/rss.jsp?sezione=267",
        "keyword_filter": CATCHMENT_PLACES,
        "restrict_to_known_local_outlets": False,
        "max_entries": 3,
    },
    {
        "name": "Google News - Newry area",
        "url": (
            "https://news.google.com/rss/search?q="
            + "+OR+".join(CATCHMENT_PLACES)
            + "&hl=en-GB&gl=GB&ceid=GB:en"
        ),
        # Google's search relevance for a 10-term OR query isn't strict --
        # confirmed in production (2026-09-04): it returned a story about a
        # crime in Dundee, Scotland, with no connection to any catchment
        # place. Don't trust the query alone; re-check independently.
        "keyword_filter": CATCHMENT_PLACES,
        "restrict_to_known_local_outlets": True,  # re-enabled 2026-09-05 -- see comment above
    },
]

# An entry is dropped if any of these substrings appear in its title or summary
# (case-insensitive). Tune this list as you see false positives/negatives.
EXCLUDE_KEYWORDS = [
    # death notices
    "death notice",
    "death notices",
    "died peacefully",
    "reposing at",
    "funeral mass",
    "removal will take place",
    "family flowers only",
    "in loving memory",
    "condolences to",
    # advertorial / sponsored content
    "advertorial",
    "sponsored content",
    "promoted content",
    "advertisement feature",
    "in association with",
    "paid partnership",
    # property / for-sale listings
    "for sale",
    "guide price",
    "asking price",
    "properties for sale",
    "bedroom house for sale",
    "bedroom home for sale",
]

# An entry is dropped if its (resolved) URL's domain matches one of these.
#
# irishnews.com: confirmed paywalled -- its pages load Google's "Subscribe
# with Google" library (subscriptions-control="manual", swg.js), Google's
# own metered-paywall integration for publishers. Metering is per-visitor
# state (e.g. "N free articles this month," tracked per browser), not a
# fixed property of the article URL -- there's no reliable way to tell
# whether a given link will be free or paywalled for a given Reddit reader,
# so rather than guess, it's excluded outright.
EXCLUDE_DOMAINS = [
    "irishnews.com",
]

# Two article titles are treated as "the same story" (and only the first is
# posted) when their token-overlap similarity is >= this value, and the
# earlier one was posted within DEDUP_WINDOW_DAYS.
#
# Deliberately not tuned to avoid every possible false positive -- confirmed
# with the user 2026-09-25 after a real one (two unrelated short titles both
# ending in "...-Community Events" scored exactly 0.5 by coincidence, one
# wrongly skipped for about a day until the other aged out of
# DEDUP_WINDOW_DAYS). Short titles are more prone to this since the
# denominator (the smaller title's word count) is small, so a couple of
# shared filler words can tip the ratio. Left as-is on purpose: the outlets
# this bot follows genuinely do post the same story twice sometimes,
# occasionally under a short title, and that's the more costly failure mode
# to miss. Don't tighten this threshold, add a minimum-word-count guard, or
# otherwise weaken this check to chase down a short-title false positive
# without raising it with the user first -- also applies to bot.run_events's
# same fuzzy-title check for events (added 2026-09-28 after newry.ie's own
# events system split one multi-night show into several listings sharing
# one identical title, which is the same class of problem for events that
# this constant already exists to catch for news).
SIMILARITY_THRESHOLD = 0.5
DEDUP_WINDOW_DAYS = 14

# Stricter than SIMILARITY_THRESHOLD, deliberately: used by bot.py to decide
# whether an unresolved Google News link and a recent Newry.ie article are
# the *same* story, close enough to substitute Newry.ie's real, working
# link for the Google News wrapper link before posting -- not just to merge
# two posts about a similar topic. A false positive here silently sends
# readers to the wrong article under the original headline, which is worse
# than the false positives SIMILARITY_THRESHOLD tolerates elsewhere, so this
# needs a tighter bar. Added 2026-09-24 as a stopgap: most Google News links
# currently fail to resolve at all (Tier 3, the headless-browser resolver,
# can't run on the current EC2 box -- see linkclean.GoogleNewsBrowserResolver's
# docstring), and a large share of what's posted from Google News turns out
# to already be on Newry.ie, which is a direct, always-resolvable link. This
# whole mechanism can be removed once the EC2 box is upgraded and Tier 3 is
# reliable again.
NEWRY_IE_LINK_MATCH_THRESHOLD = 0.75

# An entry older than this (by its feed-declared published/updated date) is
# never posted, regardless of how it scores on every other check. Added
# 2026-09-05 after noticing newry.ie's own feed carries at least one entry
# whose pubDate is from 2022 -- a stale-date quirk in their feed, not a
# real republish -- so without this, a bug or a feed change elsewhere could
# in principle surface something years old as if it were news. An entry
# with no parseable date at all is NOT dropped by this check (see
# bot.py's _is_too_old) -- we'd rather post something of unknown age than
# silently drop it over a missing field.
#
# Doesn't apply to Newry.ie or Newry Democrat -- confirmed 2026-09-06 that
# this check drops 100% of both feeds, because their pubDate is unreliable
# rather than their content actually being old. Those two use max_entries
# (see FEEDS) instead -- see bot.py's fetch_entries for how the two checks
# are kept mutually exclusive per feed.
MAX_NEWS_AGE_DAYS = 2

# Safety limits so one run can't flood the subreddit.
MAX_POSTS_PER_RUN = 5
SECONDS_BETWEEN_SUBMISSIONS = 20

STATE_DB_PATH = "state.db"
LOG_PATH = "bot.log"
