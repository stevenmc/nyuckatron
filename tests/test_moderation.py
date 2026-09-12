from types import SimpleNamespace
from unittest.mock import MagicMock

import prawcore

import moderation


def fake_post(title, url):
    return SimpleNamespace(title=title, url=url, flair=MagicMock())


# --- flair assignment -------------------------------------------------

def test_flair_matches_eventbrite_domain_before_title_keywords():
    category, template_id = moderation.pick_flair_template_id(
        "https://www.eventbrite.co.uk/e/some-event", "Totally unrelated title"
    )
    assert category == "Events"
    assert template_id == moderation._FLAIR_TEMPLATE_IDS["Events"]


def test_flair_matches_title_keyword():
    category, _ = moderation.pick_flair_template_id(
        "https://example.com/a", "Daisy Hill Hospital announces new ward"
    )
    assert category == "Hospital"


def test_flair_matches_sport_keyword_cliftonville():
    category, _ = moderation.pick_flair_template_id(
        "https://example.com/a", "Cliftonville edge out rivals in derby thriller"
    )
    assert category == "Sport"


def test_flair_defaults_to_news():
    category, template_id = moderation.pick_flair_template_id(
        "https://example.com/a", "Council publishes annual accounts summary"
    )
    assert category == "News"
    assert template_id == moderation._FLAIR_TEMPLATE_IDS["News"]


def test_flair_submission_selects_template(caplog):
    submission = fake_post("Daisy Hill Hospital news", "https://example.com/a")
    moderation.flair_submission(submission, "https://example.com/a", "Daisy Hill Hospital news")
    submission.flair.select.assert_called_once_with(
        flair_template_id=moderation._FLAIR_TEMPLATE_IDS["Hospital"]
    )


def test_flair_submission_does_not_raise_on_reddit_error():
    submission = fake_post("Some news", "https://example.com/a")
    submission.flair.select.side_effect = Exception("reddit is down")
    moderation.flair_submission(submission, "https://example.com/a", "Some news")  # should not raise


def test_flair_submission_handles_forbidden_without_full_traceback(caplog):
    # Same permission gap fetch_spam_urls already handles gracefully (no mod
    # permission, or -- when testing against a subreddit other than the
    # real target -- a flair_template_id belonging to a different
    # subreddit). The post already succeeded; this should log a short
    # warning, not dump a stack trace for an expected/known cause.
    submission = fake_post("Some news", "https://example.com/a")
    submission.flair.select.side_effect = prawcore.exceptions.Forbidden(MagicMock(status_code=403))

    with caplog.at_level("WARNING"):
        moderation.flair_submission(submission, "https://example.com/a", "Some news")  # should not raise

    assert any(r.levelname == "WARNING" and "Some news" in r.message for r in caplog.records)
    assert not any(r.exc_info for r in caplog.records)  # no full traceback logged


# --- live subreddit checks ---------------------------------------------

def test_fetch_recent_posts_includes_raw_and_stripped_titles():
    reddit = MagicMock()
    posts = [
        fake_post("New bins - Newry Times", "https://example.com/bins"),
        fake_post("Plain headline with no suffix", "https://example.com/plain"),
    ]
    reddit.subreddit.return_value.new.return_value = posts

    titles, urls, submissions = moderation.fetch_recent_posts(reddit, "newry", limit=50)

    assert "New bins - Newry Times" in titles
    assert "New bins" in titles  # vendor-suffix-stripped variant also present
    assert "Plain headline with no suffix" in titles
    assert urls == {"https://example.com/bins", "https://example.com/plain"}
    assert submissions == posts  # raw submission objects returned too
    reddit.subreddit.assert_called_with("newry")
    reddit.subreddit.return_value.new.assert_called_with(limit=50)


# --- commenting an alternate source on a probable-duplicate post ------

def fake_submission_with_comments(existing_comment_bodies=()):
    submission = MagicMock()
    submission.comments.list.return_value = [
        SimpleNamespace(body=body) for body in existing_comment_bodies
    ]
    return submission


def test_comment_with_alternate_source_replies_and_distinguishes():
    submission = fake_submission_with_comments()

    made = moderation.comment_with_alternate_source(submission, "https://example.com/other-outlet")

    assert made is True
    submission.comments.replace_more.assert_called_once_with(limit=0)
    body = submission.reply.call_args[0][0]
    assert "https://example.com/other-outlet" in body
    assert moderation._ALT_SOURCE_MARKER in body
    submission.reply.return_value.mod.distinguish.assert_called_once_with(sticky=False)


def test_comment_with_alternate_source_is_idempotent_for_the_same_url():
    already_there = (
        f"{moderation._ALT_SOURCE_MARKER} because this headline closely matched "
        "an existing post.)\n\nhttps://example.com/other-outlet"
    )
    submission = fake_submission_with_comments([already_there])

    made = moderation.comment_with_alternate_source(submission, "https://example.com/other-outlet")

    assert made is False
    submission.reply.assert_not_called()


def test_comment_with_alternate_source_still_comments_when_a_different_url_was_linked_before():
    other_url_comment = (
        f"{moderation._ALT_SOURCE_MARKER} ...)\n\nhttps://example.com/a-completely-different-story"
    )
    submission = fake_submission_with_comments([other_url_comment])

    made = moderation.comment_with_alternate_source(submission, "https://example.com/new-one")

    assert made is True
    submission.reply.assert_called_once()


def test_comment_with_alternate_source_ignores_a_plain_user_comment_that_happens_to_paste_the_url():
    # A regular user comment linking the URL (no bot marker) must not be
    # mistaken for "already handled" -- we only dedupe our own marker.
    user_comment = "here's another writeup https://example.com/other-outlet"
    submission = fake_submission_with_comments([user_comment])

    made = moderation.comment_with_alternate_source(submission, "https://example.com/other-outlet")

    assert made is True


def test_comment_with_alternate_source_fails_open_on_reddit_error():
    submission = MagicMock()
    submission.comments.replace_more.side_effect = Exception("reddit is down")

    made = moderation.comment_with_alternate_source(submission, "https://example.com/x")  # no raise

    assert made is False


def test_comment_with_alternate_source_survives_distinguish_permission_error():
    submission = fake_submission_with_comments()
    submission.reply.return_value.mod.distinguish.side_effect = Exception("not a mod")

    made = moderation.comment_with_alternate_source(submission, "https://example.com/x")

    assert made is True  # the comment itself succeeded; distinguish is best-effort


def test_fetch_spam_urls_returns_spam_submission_urls():
    reddit = MagicMock()
    reddit.subreddit.return_value.mod.spam.return_value = [
        SimpleNamespace(url="https://spam.example.com/a"),
        SimpleNamespace(url="https://spam.example.com/b"),
    ]

    urls = moderation.fetch_spam_urls(reddit, "newry", limit=50)

    assert urls == {"https://spam.example.com/a", "https://spam.example.com/b"}


def test_fetch_spam_urls_fails_open_when_bot_lacks_mod_permissions():
    reddit = MagicMock()
    fake_response = MagicMock(status_code=403)
    reddit.subreddit.return_value.mod.spam.side_effect = prawcore.exceptions.Forbidden(fake_response)

    urls = moderation.fetch_spam_urls(reddit, "newry", limit=50)

    assert urls == set()  # doesn't raise, doesn't block posting


# --- auto-approving the bot's own spam-queued posts --------------------

def fake_spam_post(title, author_name, banned_by=True):
    # banned_by=True (the default here) means Reddit's own automated spam
    # filter caught it -- the only case approve_own_spam_posts should ever
    # act on. A real username (a moderator's, or "AutoModerator") means a
    # deliberate removal, which tests below cover separately.
    return SimpleNamespace(
        title=title,
        author=SimpleNamespace(name=author_name) if author_name else None,
        banned_by=banned_by,
        mod=MagicMock(),
    )


def test_approve_own_spam_posts_approves_only_the_bots_own_posts():
    reddit = MagicMock()
    reddit.user.me.return_value = SimpleNamespace(name="newrynyuck")
    own_post = fake_spam_post("Bot's own post", "newrynyuck")
    other_post = fake_spam_post("Someone else's post", "some_other_user")
    reddit.subreddit.return_value.mod.spam.return_value = [own_post, other_post]

    approved = moderation.approve_own_spam_posts(reddit, "newry", limit=50)

    own_post.mod.approve.assert_called_once()
    other_post.mod.approve.assert_not_called()
    assert approved == 1


def test_approve_own_spam_posts_matches_username_case_insensitively():
    reddit = MagicMock()
    reddit.user.me.return_value = SimpleNamespace(name="NewryNyuck")
    own_post = fake_spam_post("Bot's own post", "newrynyuck")
    reddit.subreddit.return_value.mod.spam.return_value = [own_post]

    approved = moderation.approve_own_spam_posts(reddit, "newry")

    own_post.mod.approve.assert_called_once()
    assert approved == 1


def test_approve_own_spam_posts_skips_deleted_author():
    reddit = MagicMock()
    reddit.user.me.return_value = SimpleNamespace(name="newrynyuck")
    deleted_author_post = fake_spam_post("Ghost post", None)
    reddit.subreddit.return_value.mod.spam.return_value = [deleted_author_post]

    approved = moderation.approve_own_spam_posts(reddit, "newry")

    deleted_author_post.mod.approve.assert_not_called()
    assert approved == 0


def test_approve_own_spam_posts_fails_open_when_bot_lacks_mod_permissions():
    reddit = MagicMock()
    fake_response = MagicMock(status_code=403)
    reddit.subreddit.return_value.mod.spam.side_effect = prawcore.exceptions.Forbidden(fake_response)

    approved = moderation.approve_own_spam_posts(reddit, "newry")

    assert approved == 0  # doesn't raise, doesn't block the run
    reddit.user.me.assert_not_called()  # never even needs the bot's own username


def test_approve_own_spam_posts_continues_after_one_approve_call_fails():
    reddit = MagicMock()
    reddit.user.me.return_value = SimpleNamespace(name="newrynyuck")
    failing_post = fake_spam_post("Post that errors", "newrynyuck")
    failing_post.mod.approve.side_effect = Exception("reddit is down")
    ok_post = fake_spam_post("Post that succeeds", "newrynyuck")
    reddit.subreddit.return_value.mod.spam.return_value = [failing_post, ok_post]

    approved = moderation.approve_own_spam_posts(reddit, "newry")  # should not raise

    ok_post.mod.approve.assert_called_once()
    assert approved == 1


def test_approve_own_spam_posts_does_not_reapprove_a_moderator_removal():
    # Regression test for a real, confirmed-live incident (2026-09-08):
    # a moderator removed one of the bot's own posts on r/newry, and the
    # very next cron run silently un-removed it, because this function
    # couldn't tell "Reddit's spam filter caught this" apart from "a human
    # just removed this on purpose." banned_by is a moderator's username
    # (not the boolean True) for a real removal -- must be left alone.
    reddit = MagicMock()
    reddit.user.me.return_value = SimpleNamespace(name="newrynyuck")
    mod_removed_post = fake_spam_post("Hilltown Mart first ewe lamb sale", "newrynyuck", banned_by="stevenmc")
    reddit.subreddit.return_value.mod.spam.return_value = [mod_removed_post]

    approved = moderation.approve_own_spam_posts(reddit, "newry")

    mod_removed_post.mod.approve.assert_not_called()
    assert approved == 0


def test_approve_own_spam_posts_respects_removal_by_any_moderator_not_just_one():
    # The check is "banned_by is not the automated filter", not "banned_by
    # is stevenmc specifically" -- must hold for any moderator's username,
    # current or future, not just the one from the real incident above.
    reddit = MagicMock()
    reddit.user.me.return_value = SimpleNamespace(name="newrynyuck")
    removed_by_someone_else = fake_spam_post("Removed by a different mod", "newrynyuck", banned_by="a_future_third_moderator")
    reddit.subreddit.return_value.mod.spam.return_value = [removed_by_someone_else]

    approved = moderation.approve_own_spam_posts(reddit, "newry")

    removed_by_someone_else.mod.approve.assert_not_called()
    assert approved == 0


def test_approve_own_spam_posts_does_not_reapprove_an_automoderator_removal():
    reddit = MagicMock()
    reddit.user.me.return_value = SimpleNamespace(name="newrynyuck")
    automod_removed_post = fake_spam_post("Some post automod removed", "newrynyuck", banned_by="AutoModerator")
    reddit.subreddit.return_value.mod.spam.return_value = [automod_removed_post]

    approved = moderation.approve_own_spam_posts(reddit, "newry")

    automod_removed_post.mod.approve.assert_not_called()
    assert approved == 0


def test_approve_own_spam_posts_still_approves_genuine_automated_spam_filter_catches():
    # The feature this function exists for in the first place must keep
    # working: banned_by is literally True (not a username) when Reddit's
    # own automated filter -- not a person -- did the removing.
    reddit = MagicMock()
    reddit.user.me.return_value = SimpleNamespace(name="newrynyuck")
    auto_caught_post = fake_spam_post("Caught by the real spam filter", "newrynyuck", banned_by=True)
    reddit.subreddit.return_value.mod.spam.return_value = [auto_caught_post]

    approved = moderation.approve_own_spam_posts(reddit, "newry")

    auto_caught_post.mod.approve.assert_called_once()
    assert approved == 1


# --- submission ----------------------------------------------------------

def test_submit_post_submits_and_flairs():
    reddit = MagicMock()
    submission = fake_post("A Title", "https://example.com/a")
    reddit.subreddit.return_value.submit.return_value = submission

    result = moderation.submit_post(reddit, "newry", "https://example.com/a", "A Title")

    reddit.subreddit.return_value.submit.assert_called_once_with(
        title="A Title", url="https://example.com/a", resubmit=False, send_replies=False
    )
    submission.flair.select.assert_called_once()
    assert result is submission
