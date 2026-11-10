// Newry Events Capture -- pure DOM extraction logic, split out from
// content.js so it has no dependency on chrome.* APIs and can be loaded
// directly into a plain DOM (jsdom in tests, or the live page via
// manifest.json listing this file before content.js in the same
// content-script world -- plain top-level function declarations here are
// visible to content.js as ordinary globals, no module system needed).
//
// Selector notes (verified 2026-11-10 against a real "Copy outerHTML"
// snapshot of the user's own feed -- see facebook-capture's git history
// for how; no login/automation was used to get it):
//
//   - The three-dot menu on an organic post is a
//     `div[role="button"][aria-haspopup="menu"]` whose aria-label starts
//     with "Actions for this post" (optionally "... by <author>"). This
//     reliably excludes the *other* aria-haspopup="menu" buttons that
//     exist on a feed page: "More footer links" (chrome), "Hide or
//     report this" and "Open menu for ... sponsored content" (ads) --
//     confirmed present in the same snapshot and NOT matching this
//     prefix.
//   - The nearest ancestor of that button carrying an `aria-labelledby`
//     attribute is the post's own root element -- Facebook composes that
//     attribute from several child element ids to label the whole story
//     for screen readers, which only happens once per post. Those ids
//     are also in reading order: the 1st is the author, the 2nd the
//     timestamp -- confirmed live, used below since there's no semantic
//     attribute for the timestamp the way there is for the others.
//   - Inside that root, Facebook tags the semantically meaningful parts
//     with a stable `data-ad-rendering-role` attribute (used for ad
//     accessibility rendering, so it isn't churned the way class names
//     are): "profile_name" for the author, "story_message" for the
//     post's own text. Confirmed present and correctly positioned on
//     every organic post in the snapshot.
//   - No snapshot example contained a real Facebook "Event" card (a
//     formal event attached to a post, with its own date/time/location),
//     so extractEventCard() below is UNVERIFIED -- a best-effort
//     reading of the generally-known shape (a link to "/events/<id>"
//     plus nearby date/location text), written so it fails open (returns
//     null) rather than guesses wrong. Revisit it against a real example
//     the first time one is captured.
//   - A post's timestamp text is sometimes rendered with its DOM
//     character order scrambled (confirmed: one timestamp's innerText
//     came back as a shuffled string of individual letters/digits) --
//     apparently a Facebook anti-scraping measure. There is no reliable
//     way to recover the true visual string from the DOM (it isn't a
//     paint-order trick innerText would fix either). timestampText is
//     therefore captured as best-effort, possibly-garbled supplementary
//     context only -- never the source of truth for an event's actual
//     date, which is why that parsing is explicitly deferred to the
//     downstream ChatGPT task reading the post's own free text instead.
//   - No real permalink href was present on plain text/photo organic
//     posts in the snapshot (the visible timestamp link's href was just
//     a click-tracking fragment, `?__cft__=...#?bfg`, with the real
//     navigation handled by a JS click handler rather than a crawlable
//     link) -- only reel/photo/video attachments carried a real,
//     permalink-shaped href. permalink is therefore genuinely absent on
//     some captures; the extension still sends everything else rather
//     than drop the whole capture over one missing field.

const PERMALINK_HREF_PATTERN = /\/(posts|videos|reel|permalink\.php|story\.php|photo(\.php)?|events)\//i;
const STORY_FBID_PATTERN = /story_fbid=/i;

function findActionButtons(root) {
  const candidates = root.querySelectorAll('div[role="button"][aria-haspopup="menu"]');
  return Array.from(candidates).filter((el) => {
    const label = el.getAttribute('aria-label') || '';
    return label.indexOf('Actions for this post') === 0;
  });
}

function findPostRoot(actionButton) {
  return actionButton.closest('[aria-labelledby]');
}

function textOf(el) {
  if (!el) return null;
  try {
    const value = (el.innerText || el.textContent || '').trim();
    return value === '' ? null : value;
  } catch (e) {
    return null;
  }
}

function extractAuthor(postRoot) {
  const el = postRoot.querySelector('[data-ad-rendering-role="profile_name"]');
  const raw = textOf(el);
  if (!raw) return null;
  // Strips the "Verified account" accessibility badge text that rides
  // along inside the same element on verified pages (confirmed live:
  // "Taylor Black Verified account", "Exhale Healthy Coffee Verified
  // account") -- it isn't part of the actual name.
  return raw.replace(/\s*Verified account\s*$/i, '').trim() || null;
}

function extractText(postRoot) {
  return textOf(postRoot.querySelector('[data-ad-rendering-role="story_message"]'));
}

function extractTimestampText(postRoot) {
  const labelledBy = postRoot.getAttribute('aria-labelledby');
  if (!labelledBy) return null;
  const ids = labelledBy.trim().split(/\s+/);
  if (ids.length < 2) return null;
  const doc = postRoot.ownerDocument || document;
  return textOf(doc.getElementById(ids[1]));
}

function extractImageUrl(postRoot) {
  const images = postRoot.querySelectorAll('img[src^="http"]');
  for (const img of images) {
    const width = img.naturalWidth || img.width || 0;
    const height = img.naturalHeight || img.height || 0;
    // Skips small chrome (reaction icons, etc.) -- a real post photo is
    // never this small. Falls back to "take it anyway" if dimensions
    // aren't available yet (image not finished loading, or no renderer
    // at all -- e.g. under jsdom in tests).
    if ((width === 0 && height === 0) || (width > 40 && height > 40)) {
      return img.src;
    }
  }
  return null;
}

function extractPermalink(postRoot) {
  const links = postRoot.querySelectorAll('a[href]');
  for (const a of links) {
    const href = a.href; // the IDL property, not getAttribute -- resolves to an absolute URL
    if (PERMALINK_HREF_PATTERN.test(href) || STORY_FBID_PATTERN.test(href)) {
      return href;
    }
  }
  return null;
}

// UNVERIFIED -- see module docblock. Returns null for anything that
// doesn't match this best-effort shape rather than guessing further.
function extractEventCard(postRoot) {
  try {
    const eventLink = postRoot.querySelector('a[href*="/events/"]');
    if (!eventLink) return null;

    const card = eventLink.closest('[role="article"], div') || eventLink;
    const title = textOf(eventLink) || textOf(card.querySelector('h1, h2, h3, h4'));
    if (!title) return null;

    return {
      title,
      eventUrl: eventLink.href,
      dateTimeText: textOf(card.querySelector('[data-ad-rendering-role="meta"]')),
      location: textOf(card.querySelector('[data-ad-rendering-role="description"]')),
      priceText: null,
    };
  } catch (e) {
    return null;
  }
}

function extractPostData(postRoot) {
  const bundle = { capturedAt: new Date().toISOString(), pageUrl: (postRoot.ownerDocument.defaultView || {}).location ? postRoot.ownerDocument.defaultView.location.href : null };
  const fields = {
    permalink: extractPermalink,
    author: extractAuthor,
    text: extractText,
    timestampText: extractTimestampText,
    imageUrl: extractImageUrl,
    event: extractEventCard,
  };
  for (const key of Object.keys(fields)) {
    try {
      bundle[key] = fields[key](postRoot);
    } catch (e) {
      bundle[key] = null;
    }
  }
  return bundle;
}

function dedupKeyFor(bundle) {
  if (bundle.permalink) return bundle.permalink;
  const basis = [bundle.author, (bundle.text || '').slice(0, 200), bundle.timestampText].join('|');
  let hash = 0;
  for (let i = 0; i < basis.length; i++) {
    hash = (hash * 31 + basis.charCodeAt(i)) | 0;
  }
  return 'fallback:' + hash;
}

// Exposed for Node-based tests (see test/extract.test.mjs); a no-op in
// the real extension context, where these stay plain content-script
// globals and this branch is never taken.
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    findActionButtons,
    findPostRoot,
    extractAuthor,
    extractText,
    extractTimestampText,
    extractImageUrl,
    extractPermalink,
    extractEventCard,
    extractPostData,
    dedupKeyFor,
  };
}
