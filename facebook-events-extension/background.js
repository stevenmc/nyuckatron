// MV3 background service worker. The actual POST to capture.php happens
// here rather than in content.js -- a content script's fetch() is still
// bound by the *host page's* Content-Security-Policy, and Facebook's own
// CSP blocks a connect-src to an arbitrary third-party host (confirmed
// live: "TypeError: Failed to fetch" from content.js while the same URL
// was reachable fine with curl -- the classic symptom of this exact MV3
// gotcha). A service worker has no host page and isn't bound by one.
//
// This also needs the server's own origin listed in manifest.json's
// host_permissions (see "fb-events.stevenmccullagh.com" there) -- that's
// what lets this fetch bypass the server's own CORS headers (our PHP
// endpoints don't send any) rather than just being a same-origin-style
// request that would otherwise still fail reading the response. If the
// server's URL is ever changed in the options page, that manifest entry
// needs updating to match, or this will start failing again the same way.

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || message.type !== 'nec-capture') return false;

  (async () => {
    try {
      const response = await fetch(message.endpointBaseUrl + '/capture.php', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Capture-Token': message.captureToken,
        },
        body: JSON.stringify(message.bundle),
      });
      if (!response.ok) {
        sendResponse({ ok: false, error: `Server rejected capture: HTTP ${response.status}` });
        return;
      }
      sendResponse({ ok: true });
    } catch (e) {
      sendResponse({ ok: false, error: String(e) });
    }
  })();

  return true; // keep the message channel open for the async sendResponse above
});
