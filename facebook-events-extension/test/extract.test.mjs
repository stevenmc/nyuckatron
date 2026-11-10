import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { createRequire } from 'node:module';
import { JSDOM } from 'jsdom';

const here = path.dirname(fileURLToPath(import.meta.url));

// extract.js is plain CommonJS (see its own guarded `module.exports` --
// written that way so it also works unmodified as a browser content
// script); this test file is loaded as an ES module (needed for the
// top-level `import` of jsdom), so grab `require` from Node's builtin
// module API instead of using an import statement for a CommonJS file.
const require = createRequire(import.meta.url);
const extract = require(path.join(here, '..', 'extract.js'));

function loadFixture() {
  const html = readFileSync(path.join(here, 'fixtures', 'feed-sample.html'), 'utf8');
  const dom = new JSDOM(html);
  return dom.window.document;
}

test('findActionButtons finds only the real per-post menu buttons', () => {
  const document = loadFixture();
  const buttons = extract.findActionButtons(document);
  assert.equal(buttons.length, 3, 'post A, B, and C -- not the footer/report/ad noise buttons');
  for (const btn of buttons) {
    assert.ok(btn.getAttribute('aria-label').startsWith('Actions for this post'));
  }
});

test('findPostRoot resolves to the nearest aria-labelledby ancestor', () => {
  const document = loadFixture();
  const [btnA] = extract.findActionButtons(document);
  const root = extract.findPostRoot(btnA);
  assert.equal(root.getAttribute('aria-labelledby'), 'authorA timeA privacyA textA');
});

test('extracts author, text, and timestamp for a plain post with no permalink', () => {
  const document = loadFixture();
  const [btnA] = extract.findActionButtons(document);
  const root = extract.findPostRoot(btnA);

  assert.equal(extract.extractAuthor(root), 'Test Page One');
  assert.equal(extract.extractText(root), 'Join us Friday for a community coffee morning.');
  assert.equal(extract.extractTimestampText(root), '3h');
  assert.equal(extract.extractPermalink(root), null);
  assert.equal(extract.extractEventCard(root), null);
});

test('strips the "Verified account" badge text from the author field', () => {
  const document = loadFixture();
  const [, btnB] = extract.findActionButtons(document);
  const root = extract.findPostRoot(btnB);

  assert.equal(extract.extractAuthor(root), 'Test Page Two');
});

test('finds a real permalink-shaped href when one is present', () => {
  const document = loadFixture();
  const [, btnB] = extract.findActionButtons(document);
  const root = extract.findPostRoot(btnB);

  const permalink = extract.extractPermalink(root);
  assert.ok(permalink && permalink.includes('/TestPageTwo/posts/998877'));
});

test('extracts a best-effort event card when a /events/ link is present', () => {
  const document = loadFixture();
  const [, btnB] = extract.findActionButtons(document);
  const root = extract.findPostRoot(btnB);

  const event = extract.extractEventCard(root);
  assert.ok(event);
  assert.equal(event.title, 'Christmas Market 2026');
  assert.equal(event.dateTimeText, 'Sat, 12 Dec, 6:00 PM');
  assert.equal(event.location, 'Town Square');
});

test('picks the larger image over small icon-sized ones', () => {
  const document = loadFixture();
  const [btnA] = extract.findActionButtons(document);
  const root = extract.findPostRoot(btnA);

  assert.equal(extract.extractImageUrl(root), 'https://example.com/photo-large.jpg');
});

test('extraction never throws and fails open to nulls when fields are missing', () => {
  const document = loadFixture();
  const buttons = extract.findActionButtons(document);
  const btnC = buttons[2];
  const root = extract.findPostRoot(btnC);

  assert.doesNotThrow(() => extract.extractPostData(root));
  const bundle = extract.extractPostData(root);
  assert.equal(bundle.author, null);
  assert.equal(bundle.text, null);
  assert.equal(bundle.permalink, null);
  assert.equal(bundle.event, null);
});

test('dedupKeyFor prefers the permalink when present', () => {
  const bundle = { permalink: 'https://facebook.com/x/posts/1', author: 'A', text: 'B', timestampText: 'C' };
  assert.equal(extract.dedupKeyFor(bundle), 'https://facebook.com/x/posts/1');
});

test('dedupKeyFor falls back to a stable hash when there is no permalink', () => {
  const bundle = { permalink: null, author: 'A', text: 'B', timestampText: 'C' };
  const first = extract.dedupKeyFor(bundle);
  const second = extract.dedupKeyFor({ ...bundle });
  assert.equal(first, second);
  assert.ok(first.startsWith('fallback:'));
});

test('dedupKeyFor fallback differs for genuinely different posts', () => {
  const a = extract.dedupKeyFor({ permalink: null, author: 'A', text: 'one', timestampText: '1h' });
  const b = extract.dedupKeyFor({ permalink: null, author: 'B', text: 'two', timestampText: '2h' });
  assert.notEqual(a, b);
});
