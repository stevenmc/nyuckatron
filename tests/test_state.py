from datetime import datetime, timedelta, timezone

import pytest

import config
import state


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DB_PATH", str(tmp_path / "test_state.db"))
    connection = state.connect()
    yield connection
    connection.close()


def test_url_not_posted_initially(conn):
    assert not state.url_already_posted(conn, "https://example.com/a")


def test_record_and_check_posted(conn):
    state.record_posted(conn, "https://example.com/a", "A Title", "title")
    assert state.url_already_posted(conn, "https://example.com/a")
    assert not state.url_already_posted(conn, "https://example.com/b")


def test_recent_titles_within_window(conn):
    state.record_posted(conn, "https://example.com/a", "A Title", "normalized a")
    titles = state.recent_titles(conn, window_days=14)
    assert titles == ["normalized a"]


def test_recent_titles_excludes_entries_outside_window(conn):
    old_time = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    conn.execute(
        "INSERT INTO posted (url, title, normalized_title, posted_at) VALUES (?, ?, ?, ?)",
        ("https://example.com/old", "Old", "old", old_time),
    )
    conn.commit()
    assert state.recent_titles(conn, window_days=14) == []


def test_prune_old_removes_entries_beyond_pruning_window(conn):
    old_time = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    conn.execute(
        "INSERT INTO posted (url, title, normalized_title, posted_at) VALUES (?, ?, ?, ?)",
        ("https://example.com/ancient", "Ancient", "ancient", old_time),
    )
    conn.commit()
    state.prune_old(conn, window_days=14)  # pruning cutoff is window_days * 3
    assert not state.url_already_posted(conn, "https://example.com/ancient")


def test_prune_old_keeps_entries_within_pruning_window(conn):
    recent_time = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
    conn.execute(
        "INSERT INTO posted (url, title, normalized_title, posted_at) VALUES (?, ?, ?, ?)",
        ("https://example.com/recent", "Recent", "recent", recent_time),
    )
    conn.commit()
    state.prune_old(conn, window_days=14)  # cutoff is 42 days back; this row is 20 days old
    assert state.url_already_posted(conn, "https://example.com/recent")


def test_record_posted_is_idempotent_on_url(conn):
    state.record_posted(conn, "https://example.com/a", "First Title", "first title")
    state.record_posted(conn, "https://example.com/a", "Second Title", "second title")
    rows = conn.execute("SELECT title FROM posted WHERE url = ?", ("https://example.com/a",)).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "First Title"
