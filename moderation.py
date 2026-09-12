"""Reddit-side actions: duplicate/spam checks against live subreddit state,
submitting posts, and flair assignment.

These are a *supplement* to the local SQLite + fuzzy-title dedup in state.py
and textutil.py, not a replacement for it: state.py catches near-duplicate
headlines about the same story from different outlets across days, which
Reddit's own post list can't tell you. This module catches the case local
state can't -- something posted by a human moderator, or from a wiped/fresh
state.db, since Reddit itself is the ground truth for what's actually live.
"""

import logging

import prawcore

log = logging.getLogger("newry-bot")

# Real flair template IDs configured on r/newry. Keyword/domain matching is
# intentionally specific to this subreddit's local vocabulary (place names,
# team names) rather than generic categories.
_FLAIR_TEMPLATE_IDS = {
    "Sport": "0477e83c-369d-11ee-a2c4-6e3cb7b674c3",
    "Bridge": "d1964f2a-4bc9-11ee-894b-4ef10bd618b8",
    "Events": "dbcb5aec-16ba-11ee-b3ac-f6de8c561e88",
    "Farming": "08009a56-1b83-11ee-b36b-4e7f9824e7f8",
    "Hospital": "30928dc8-16bd-11ee-9fe0-b6c9f9894a64",
    "Prices": "85ffbd20-2550-11ee-a7d5-fe3b1d676ebe",
    "Central Perk": "dbb010b8-4bc9-11ee-b334-467671b614d8",
    "Jobs": "6fdd9a02-4e3c-11ee-bcdc-6acc09b27895",
    "News": "c39ddc60-16ba-11ee-8247-aa3941a40f6e",
}

_FLAIR_URL_KEYWORDS = {
    "Farming": ["farminglife.com"],
    "Events": ["eventbrite.com", "eventbrite.co.uk", "eventbrite.ie"],
}

_FLAIR_TITLE_KEYWORDS = {
    "Sport": [
        "mayobridge", "sfc", "kilkoo", "goal", "championship", "premiership",
        "sport", "player", "comeback", "football", "hurling", "gaa",
        "rangers", "rovers", "crusaders", "linfield", "glentoran",
        "cliftonville", "coleraine", "ballymena", "national league",
        "match", "the bridge",
    ],
    "Bridge": ["bridge", "Narrow Water"],
    "Events": [
        "event", "events", "visit", "weekend", "coming up", "programme",
        "this friday", "this saturday", "this sunday",
    ],
    "Farming": [
        "calves", "ewes", "nfu", "farmer", "farming", "newry show",
        "bullocks", "farm", "heifers", "daera", "cattle", "dairy",
    ],
    "Hospital": ["daisy hill", "hospital", "ambulance", "renal", "patients", "surgery"],
    "Prices": ["cheaper", "expensive", "cost of", "price", "food bank", "trussell trust"],
    "Central Perk": ["park"],
    "Jobs": ["jobs", "recruitment"],
}


def pick_flair_template_id(url, title):
    for category, domains in _FLAIR_URL_KEYWORDS.items():
        if any(domain in url for domain in domains):
            return category, _FLAIR_TEMPLATE_IDS[category]

    title_lower = title.lower()
    for category, keywords in _FLAIR_TITLE_KEYWORDS.items():
        if any(kw.lower() in title_lower for kw in keywords):
            return category, _FLAIR_TEMPLATE_IDS[category]

    return "News", _FLAIR_TEMPLATE_IDS["News"]


def flair_submission(submission, url, title, force_category=None):
    """force_category skips domain/keyword auto-detection and applies that
    category directly -- for callers that already know what a post is
    (e.g. the events pipeline, which is always "Events" regardless of
    whether the event's title happens to contain a matching keyword)."""
    if force_category:
        category, template_id = force_category, _FLAIR_TEMPLATE_IDS[force_category]
    else:
        category, template_id = pick_flair_template_id(url, title)
    try:
        submission.flair.select(flair_template_id=template_id)
        log.info("Flaired post '%s' as %s", title, category)
    except prawcore.exceptions.Forbidden:
        # Same permission gap as fetch_spam_urls -- no mod permission (or,
        # when testing against a subreddit other than the real target, a
        # flair_template_id that belongs to a different subreddit entirely).
        # The post itself already succeeded; flair is a nice-to-have on top,
        # not worth a full traceback for an expected, already-known cause.
        log.warning(
            "Could not flair post '%s' as %s -- no mod permission, or this "
            "subreddit doesn't have that flair template.", title, category
        )
    except Exception:
        log.exception("Failed to set flair '%s' on post '%s'", category, title)


def fetch_recent_posts(reddit, subreddit_name, limit=50):
    """Titles, URLs, and the raw submission objects of the subreddit's most
    recent posts. Each title is included both as-is and with a generic
    vendor suffix stripped (matching how we clean titles before posting, so
    a post we'd clean to the same title as an existing one still counts as
    a duplicate). The submission list is returned as well so the caller can
    comment on a specific post -- e.g. to link a probable same-story repost
    from another outlet rather than silently dropping it."""
    import linkclean

    posts = list(reddit.subreddit(subreddit_name).new(limit=limit))
    titles = set()
    for post in posts:
        titles.add(post.title)
        titles.add(linkclean.strip_vendor_suffix(post.title))
    urls = {post.url for post in posts}
    return titles, urls, posts


_ALT_SOURCE_MARKER = "^(Posted automatically by newry-bot"


def comment_with_alternate_source(submission, url):
    """Reply to `submission` with `url`, flagging it as another outlet's
    coverage of what looks like the same story. Returns True if a new
    comment was actually made.

    Idempotent: if a newry-bot alternate-source comment already links this
    URL, does nothing and returns False -- so a story that keeps
    reappearing in the feed on every cron run only ever gets commented
    once, even if the local state DB is wiped. Fails open (logs, returns
    False) on any Reddit error, same as the rest of this module -- a failed
    comment must never block the run."""
    try:
        submission.comments.replace_more(limit=0)
        for comment in submission.comments.list():
            body = getattr(comment, "body", "") or ""
            if _ALT_SOURCE_MARKER in body and url in body:
                return False

        comment = submission.reply(
            "Another source is covering what looks like the same story:\n\n"
            f"{url}\n\n"
            f"{_ALT_SOURCE_MARKER} because this headline closely matched an "
            "existing post. If it's actually a separate story, reply here and "
            "a moderator can post it on its own.)"
        )
        if comment is not None:
            try:
                comment.mod.distinguish(sticky=False)
            except Exception:
                pass  # not a mod, or distinguish unavailable -- the comment still stands
        return True
    except Exception:
        log.exception(
            "Failed to add alternate-source comment (%s) on %s",
            url, getattr(submission, "permalink", submission),
        )
        return False


def fetch_spam_urls(reddit, subreddit_name, limit=50):
    """URLs currently in the subreddit's spam queue. Requires the bot
    account to be a moderator with 'posts' permission -- if it isn't, this
    fails open (logs a warning, returns an empty set) rather than blocking
    every post, since this is a supplementary safety net, not the primary
    dedup mechanism."""
    try:
        spam = reddit.subreddit(subreddit_name).mod.spam(only="submissions", limit=limit)
        return {submission.url for submission in spam}
    except prawcore.exceptions.Forbidden:
        log.warning(
            "Bot account lacks mod permissions on r/%s -- skipping spam-queue "
            "check. Grant it 'posts' mod permission to enable this.",
            subreddit_name,
        )
        return set()


def approve_own_spam_posts(reddit, subreddit_name, limit=50):
    """Approves the bot's own submissions sitting in the subreddit's spam
    queue *specifically because Reddit's automated spam filter caught
    them* -- Reddit's filter routinely flags posts from new/low-karma
    accounts, exactly what this bot's account looks like to it, even
    though nothing about the post itself is actually spam; since we
    submitted it ourselves, we already know it's legitimate, so approve
    it outright rather than leaving it silently hidden until a human
    moderator happens to check.

    Critically, this must NOT re-approve a post a human moderator (or
    AutoModerator) deliberately removed -- confirmed live (2026-09-08) as
    a real, repeating incident on r/newry: a moderator would remove one
    of the bot's posts, and the very next cron run silently undid it,
    because this function couldn't originally tell "Reddit's spam filter
    flagged this" apart from "a moderator just removed this on purpose."
    The fix uses `submission.banned_by`, which Reddit sets differently
    for each case: the boolean `True` means the automated filter did it;
    any actual username (a real moderator's name, or "AutoModerator")
    means a deliberate decision was made, which this function now leaves
    alone. `is True` (not a truthiness check) is deliberate -- a
    moderator's username is also truthy, and the two must not be
    conflated.

    Deliberately scoped to posts authored by the bot's own account only --
    this is not a general auto-approve-everything tool, and other authors'
    spam-queued submissions are left untouched for a human to review.

    Requires the bot account to be a moderator with 'posts' permission --
    if it isn't, this fails open (logs a warning, does nothing) rather
    than blocking the run, same as fetch_spam_urls. Returns the number of
    posts approved."""
    try:
        spam = list(reddit.subreddit(subreddit_name).mod.spam(only="submissions", limit=limit))
    except prawcore.exceptions.Forbidden:
        log.warning(
            "Bot account lacks mod permissions on r/%s -- skipping auto-approve "
            "of its own spam-queued posts. Grant it 'posts' mod permission to "
            "enable this.",
            subreddit_name,
        )
        return 0

    bot_username = reddit.user.me().name.lower()
    approved = 0
    for submission in spam:
        if not submission.author or submission.author.name.lower() != bot_username:
            continue
        if submission.banned_by is not True:
            log.info(
                "Leaving own post removed by %s alone (not the automated spam "
                "filter, so not re-approving): %s",
                submission.banned_by, submission.title,
            )
            continue
        try:
            submission.mod.approve()
            log.info("Approved own post removed by spam filter: %s", submission.title)
            approved += 1
        except Exception:
            log.exception("Failed to approve own spam-queued post: %s", submission.title)
    return approved


def submit_post(reddit, subreddit_name, url, title, force_category=None):
    subreddit = reddit.subreddit(subreddit_name)
    submission = subreddit.submit(title=title, url=url, resubmit=False, send_replies=False)
    flair_submission(submission, url, title, force_category=force_category)
    return submission
