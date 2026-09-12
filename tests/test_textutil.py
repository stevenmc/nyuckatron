import config
import textutil


def test_normalize_strips_stopwords_and_punctuation():
    assert textutil.normalize("The Newry Bridge, and a New Plan!") == "bridge plan"


def test_normalize_empty_string():
    assert textutil.normalize("") == ""


# Real headline pairs pulled from a live BBC/Google News fetch during
# development (2026-09-04), used to tune SIMILARITY_THRESHOLD and the
# numeric-mismatch guard in textutil.similarity.
DUPLICATE_CASES = [
    (
        "Newry's new £18.6m park is approved",
        "'Wonderful day' for Newry as £18.6m Albert Basin city park plans approved",
    ),
    (
        "SDLP credit Newry Community for making City Park vision a reality",
        "SDLP Says Newry Community Made Newry Park A Reality",
    ),
    (
        "Air quality in Newry",
        "Newry Air Quality Index (AQI) and United Kingdom Air Pollution",
    ),
    (
        "Kimmins confirms 'one hour grace period' for parking in Newry and Lisburn now permanent",
        "Lisburn and Newry: Parking 'grace period' scheme made permanent",
    ),
    (
        "Newry City Regeneration Gathers Momentum as Major Projects Move into Delivery",
        "Newry Regeneration Accelerates As Landmark Projects Enter Delivery",
    ),
]

# Headlines that share almost all their template wording but describe
# different events -- differ only by a number (amount, date, opponent's
# score). These must NOT be flagged as duplicates.
DISTINCT_CASES = [
    (
        "Kimmins announces £262,000 carriageway resurfacing scheme on A28 Armagh Road Roundabout, Newry",
        "Kimmins announces £250,000 carriageway resurfacing scheme on A27 Tandragee Road Roundabout, Newry",
    ),
    (
        "Kimmins announces £685,000 carriageway resurfacing scheme on Old Warrenpoint Road, Newry",
        "Kimmins announces £450,000 carriageway resurfacing scheme on A2 Greenbank Road, Roundabout, Newry",
    ),
    (
        "2026-08-15 - Rathfriland Rangers v Newry City",
        "2026-08-11 - Newry City v Glenavon",
    ),
    (
        "Newry City slump to three-goal defeat at home to Glenavon",
        "Glenavon hit back with victory at Newry City in BOYLE Sports Championship",
    ),
]


def test_duplicate_stories_are_detected():
    for a, b in DUPLICATE_CASES:
        sim = textutil.similarity(textutil.normalize(a), textutil.normalize(b))
        assert sim >= config.SIMILARITY_THRESHOLD, f"expected duplicate: {a!r} / {b!r} (sim={sim:.2f})"


def test_distinct_stories_are_not_flagged():
    for a, b in DISTINCT_CASES:
        sim = textutil.similarity(textutil.normalize(a), textutil.normalize(b))
        assert sim < config.SIMILARITY_THRESHOLD, f"expected distinct: {a!r} / {b!r} (sim={sim:.2f})"


def test_is_duplicate_story_checks_against_a_list():
    seen = [textutil.normalize("Newry's new £18.6m park is approved")]
    dup_title = textutil.normalize(
        "'Wonderful day' for Newry as £18.6m Albert Basin city park plans approved"
    )
    assert textutil.is_duplicate_story(dup_title, seen, config.SIMILARITY_THRESHOLD)

    unrelated = textutil.normalize("Man arrested after A28 collision near Newry")
    assert not textutil.is_duplicate_story(unrelated, seen, config.SIMILARITY_THRESHOLD)


def test_best_duplicate_match_returns_the_closest_candidate_above_threshold():
    candidates = [
        ("post-a", textutil.normalize("Man arrested after A28 collision near Newry")),
        ("post-b", textutil.normalize("Newry's new £18.6m park is approved")),
        ("post-c", textutil.normalize("Council publishes annual accounts summary")),
    ]
    target = textutil.normalize(
        "'Wonderful day' for Newry as £18.6m Albert Basin city park plans approved"
    )

    match = textutil.best_duplicate_match(target, candidates, config.SIMILARITY_THRESHOLD)

    assert match is not None
    assert match[0] == "post-b"
    assert match[1] >= config.SIMILARITY_THRESHOLD


def test_best_duplicate_match_returns_none_when_nothing_clears_the_threshold():
    candidates = [
        ("post-a", textutil.normalize("Man arrested after A28 collision near Newry")),
        ("post-b", textutil.normalize("Rostrevor pub wins tourism award")),
    ]
    target = textutil.normalize("Council approves new play park in Warrenpoint")

    assert textutil.best_duplicate_match(target, candidates, config.SIMILARITY_THRESHOLD) is None


def test_similarity_ignores_common_number_when_no_numbers_present():
    assert textutil.similarity("air quality newry", "newry air quality index") == 1.0


def test_catchment_place_names_dont_inflate_similarity_of_unrelated_stories():
    # Two unrelated Rostrevor stories shouldn't look like duplicates just
    # because they both mention Rostrevor -- place names are stopwords for
    # exactly this reason (see textutil._STOPWORDS).
    a = textutil.normalize("New footbridge planned for Rostrevor")
    b = textutil.normalize("Rostrevor pub wins tourism award")
    assert textutil.similarity(a, b) == 0.0


def test_all_catchment_places_are_stopwords():
    for place in config.CATCHMENT_PLACES:
        assert place.lower() in textutil._STOPWORDS
