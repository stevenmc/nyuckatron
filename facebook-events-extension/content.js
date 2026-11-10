// Newry Events Capture -- content script, facebook.com only.
//
// Everything here is read-only DOM inspection plus one POST per button
// click -- there is no automation of Facebook itself (no simulated
// clicks, no login, no background polling of FB). The user drives every
// capture by hand.
//
// The actual selector/extraction logic lives in extract.js (loaded
// before this file in manifest.json, sharing this content script's
// isolated-world global scope) so it can be unit-tested under Node/jsdom
// without any chrome.* dependency -- see that file for the detailed,
// snapshot-verified notes on how posts and their fields are found.

(function () {
  'use strict';

  const INJECTED_ATTR = 'data-nec-injected';

  let config = null; // { endpointBaseUrl, captureToken } -- loaded async, see loadConfig()

  function loadConfig() {
    return new Promise((resolve) => {
      chrome.storage.local.get(['endpointBaseUrl', 'captureToken'], (result) => {
        config = result.endpointBaseUrl && result.captureToken ? result : null;
        resolve(config);
      });
    });
  }

  async function sendCapture(bundle) {
    if (!config) await loadConfig();
    if (!config) {
      throw new Error('Not configured yet -- open the extension options and set a server URL + token.');
    }
    const response = await fetch(config.endpointBaseUrl + '/capture.php', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Capture-Token': config.captureToken,
      },
      body: JSON.stringify(bundle),
    });
    if (!response.ok) {
      throw new Error('Server rejected capture: HTTP ' + response.status);
    }
  }

  function isAlreadyCaptured(key) {
    return new Promise((resolve) => {
      chrome.storage.local.get(['capturedKeys'], (result) => {
        const captured = result.capturedKeys || {};
        resolve(Boolean(captured[key]));
      });
    });
  }

  function markCaptured(key) {
    chrome.storage.local.get(['capturedKeys'], (result) => {
      const captured = result.capturedKeys || {};
      captured[key] = true;
      chrome.storage.local.set({ capturedKeys: captured });
    });
  }

  // --- UI injection --------------------------------------------------------

  function createDot() {
    const dot = document.createElement('div');
    dot.className = 'nec-capture-dot';
    dot.setAttribute('role', 'button');
    dot.setAttribute('tabindex', '0');
    dot.setAttribute('aria-label', 'Capture this post for the Newry events feed');
    dot.textContent = '✓'; // checkmark glyph, hidden via color until state="done"
    dot.setAttribute('data-state', 'idle');
    return dot;
  }

  function setState(dot, state) {
    dot.setAttribute('data-state', state);
  }

  async function handleClick(dot, actionButton) {
    if (dot.getAttribute('data-state') === 'pending' || dot.getAttribute('data-state') === 'done') return;

    const postRoot = findPostRoot(actionButton);
    if (!postRoot) {
      console.error('[Newry Events Capture] could not find post root for this button');
      setState(dot, 'error');
      return;
    }

    setState(dot, 'pending');
    const bundle = extractPostData(postRoot);
    const key = dedupKeyFor(bundle);

    try {
      await sendCapture(bundle);
      markCaptured(key);
      setState(dot, 'done');
    } catch (e) {
      console.error('[Newry Events Capture] capture failed, leave the dot as-is and let the user retry:', e);
      setState(dot, 'error');
    }
  }

  async function injectControl(actionButton) {
    if (actionButton.getAttribute(INJECTED_ATTR)) return;
    actionButton.setAttribute(INJECTED_ATTR, '1');

    const dot = createDot();
    actionButton.insertAdjacentElement('afterend', dot);

    const postRoot = findPostRoot(actionButton);
    if (postRoot) {
      const bundle = extractPostData(postRoot);
      const key = dedupKeyFor(bundle);
      if (await isAlreadyCaptured(key)) {
        setState(dot, 'done');
      }
    }

    dot.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      handleClick(dot, actionButton);
    });
  }

  function scan() {
    for (const btn of findActionButtons(document)) {
      injectControl(btn);
    }
  }

  // --- Observe the feed for new/virtualized posts ------------------------

  let rescanScheduled = false;
  function scheduleScan() {
    if (rescanScheduled) return;
    rescanScheduled = true;
    setTimeout(() => {
      rescanScheduled = false;
      scan();
    }, 200);
  }

  loadConfig().then(() => {
    scan();
    new MutationObserver(scheduleScan).observe(document.body, { childList: true, subtree: true });
  });
})();
