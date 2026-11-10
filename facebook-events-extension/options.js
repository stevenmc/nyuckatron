// Options page: stores the endpoint base URL + shared secret token in
// chrome.storage.local (never baked into committed source -- see
// manifest.json/content.js, neither of which hardcodes a token).

const endpointInput = document.getElementById('endpoint');
const tokenInput = document.getElementById('token');
const statusEl = document.getElementById('status');

function showStatus(message, isError) {
  statusEl.textContent = message;
  statusEl.className = isError ? 'err' : 'ok';
}

chrome.storage.local.get(['endpointBaseUrl', 'captureToken'], (result) => {
  if (result.endpointBaseUrl) endpointInput.value = result.endpointBaseUrl;
  if (result.captureToken) tokenInput.value = result.captureToken;
});

document.getElementById('save').addEventListener('click', () => {
  const endpointBaseUrl = endpointInput.value.trim().replace(/\/+$/, '');
  const captureToken = tokenInput.value.trim();

  if (!endpointBaseUrl || !captureToken) {
    showStatus('Both fields are required.', true);
    return;
  }
  try {
    new URL(endpointBaseUrl);
  } catch {
    showStatus('Server base URL is not a valid URL.', true);
    return;
  }

  chrome.storage.local.set({ endpointBaseUrl, captureToken }, () => {
    showStatus('Saved.', false);
  });
});
