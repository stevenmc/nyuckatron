"""Title normalization + similarity, used to catch reposts of the same story
from different outlets (which won't share a URL, so exact dedup misses them)."""

import re

import config

_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "of", "in", "on", "at", "to", "for",
    "with", "from", "by", "is", "are", "was", "were", "be", "as", "it", "its",
    "this", "that", "after", "over", "into", "new",
} | {place.lower() for place in config.CATCHMENT_PLACES}


def normalize(title):
    words = re.findall(r"[a-z0-9]+", title.lower())
    return " ".join(w for w in words if w not in _STOPWORDS)


def token_set(normalized_title):
    return set(normalized_title.split())


def _numbers(normalized_title):
    return {tok for tok in normalized_title.split() if tok.isdigit()}


def similarity(a_normalized, b_normalized):
    """Overlap coefficient (intersection / smaller set size) rather than
    Jaccard (intersection / union). Different outlets covering the same
    story rarely phrase headlines the same length or way -- one is often a
    strict subset of shared keywords plus its own fluff -- so penalizing by
    the union size (as Jaccard does) under-detects real duplicates.

    Guard: templated headlines (road-resurfacing announcements, sports
    fixtures) share almost all their words and only differ in a number --
    an amount, a date, a score -- which is exactly the part that makes them
    different stories. If both titles carry numbers and none match, treat
    them as unrelated regardless of word overlap.
    """
    a, b = token_set(a_normalized), token_set(b_normalized)
    if not a or not b:
        return 0.0

    # Require the number sets to match exactly, not just overlap: amounts
    # like "262,000" vs "250,000" both contain the token "000", and dates
    # like "2026-08-15" vs "2026-08-11" both contain "2026" and "08" -- a
    # plain intersection check would miss both as distinct stories.
    a_nums, b_nums = _numbers(a_normalized), _numbers(b_normalized)
    if a_nums and b_nums and a_nums != b_nums:
        return 0.0

    return len(a & b) / min(len(a), len(b))


def is_duplicate_story(normalized_title, recent_normalized_titles, threshold):
    return any(
        similarity(normalized_title, other) >= threshold
        for other in recent_normalized_titles
    )


def best_duplicate_match(normalized_title, candidates, threshold):
    """From an iterable of (key, normalized_title) pairs, return the
    (key, score) whose title is most similar to normalized_title -- but
    only if that score clears `threshold`. Returns None if nothing does.

    Unlike is_duplicate_story (a yes/no), this tells the caller *which*
    already-posted item a fuzzy match lines up with, so a probable
    same-story-different-outlet repost can be added as a comment on that
    post instead of being silently dropped. The similarity function is
    inherently uncertain on headlines from different outlets, which is
    exactly why the softer "link it as a comment" outcome exists.
    """
    best = None
    for key, other in candidates:
        score = similarity(normalized_title, other)
        if score >= threshold and (best is None or score > best[1]):
            best = (key, score)
    return best
