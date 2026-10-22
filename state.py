"""SQLite-backed state: what we've already posted, for dedup across runs."""

import sqlite3
from datetime import datetime, timedelta, timezone

import config


def connect():
    conn = sqlite3.connect(config.STATE_DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS posted (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            normalized_title TEXT NOT NULL,
            posted_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_posted_at ON posted(posted_at)")
    return conn


def url_already_posted(conn, url):
    row = conn.execute("SELECT 1 FROM posted WHERE url = ?", (url,)).fetchone()
    return row is not None


def recent_titles(conn, window_days):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
    rows = conn.execute(
        "SELECT normalized_title FROM posted WHERE posted_at >= ?", (cutoff,)
    ).fetchall()
    return [r[0] for r in rows]


def count_recent_titles_containing(conn, substring, window_hours=24):
    """How many posted rows have `substring` (case-insensitive) in their
    raw title, within the last `window_hours` -- used for per-keyword
    daily caps (config.KEYWORD_DAILY_LIMITS). Checked against the raw
    `title` column, not `normalized_title`: a catchment place name like
    "Crossmaglen" is itself stripped out during normalization (see
    textutil._STOPWORDS), so it would never match there."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
    row = conn.execute(
        "SELECT COUNT(*) FROM posted WHERE posted_at >= ? AND title LIKE ? COLLATE NOCASE",
        (cutoff, f"%{substring}%"),
    ).fetchone()
    return row[0]


def record_posted(conn, url, title, normalized_title):
    conn.execute(
        "INSERT OR IGNORE INTO posted (url, title, normalized_title, posted_at) VALUES (?, ?, ?, ?)",
        (url, title, normalized_title, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def prune_old(conn, window_days):
    # Keep a bit of margin beyond the dedup window so the DB doesn't grow forever
    # on a long-running low-memory box, without discarding rows still in use.
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days * 3)).isoformat()
    conn.execute("DELETE FROM posted WHERE posted_at < ?", (cutoff,))
    conn.commit()
