"""
database.py
-----------
Handles all SQLite interactions for Provenance Guard.

Responsibilities:
  - Create the `submissions` table if it doesn't exist (called once on app start).
  - insert_submission()  → write a new classified entry.
  - get_log()            → return recent entries as a list of dicts.
  - get_submission()     → look up one entry by content_id (used by /appeal).
  - update_appeal()      → flip status to "under_review" and store appeal data.

SQLite is part of Python's standard library — no install needed.
The DB file (provenance.db) is created in the project root on first run.
"""

import sqlite3
import os

# Path to the SQLite database file.
# os.path.dirname(__file__) makes this work regardless of which directory
# you run the app from.
DB_PATH = os.path.join(os.path.dirname(__file__), "provenance.db")


def get_connection():
    """
    Open a connection to the SQLite DB.
    detect_types lets us store/retrieve Python dicts via sqlite3.Row.
    check_same_thread=False is required for Flask's multi-threaded dev server.
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row   # rows behave like dicts: row["column_name"]
    return conn


def init_db():
    """
    Create the submissions table if it doesn't already exist.
    Called once when the Flask app starts (see app.py).

    Column notes:
      text_snippet   — only the first 200 chars of the submitted text.
                       We don't store the full text to avoid turning the DB
                       into a content archive.
      stylo_score    — placeholder 0.0 in Milestone 3; real value added in M4.
      appeal_*       — NULL until a creator files an appeal.
    """
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS submissions (
            content_id       TEXT PRIMARY KEY,
            creator_id       TEXT NOT NULL,
            text_snippet     TEXT NOT NULL,
            attribution      TEXT NOT NULL,
            confidence       REAL NOT NULL,
            llm_score        REAL NOT NULL,
            stylo_score      REAL NOT NULL,
            label_text       TEXT NOT NULL,
            status           TEXT NOT NULL DEFAULT 'classified',
            appeal_reasoning TEXT,
            appeal_timestamp TEXT,
            created_at       TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def insert_submission(entry: dict) -> None:
    """
    Write a new submission entry to the DB.

    Expected keys in `entry`:
        content_id, creator_id, text_snippet, attribution, confidence,
        llm_score, stylo_score, label_text, status, created_at
    """
    conn = get_connection()
    conn.execute("""
        INSERT INTO submissions
            (content_id, creator_id, text_snippet, attribution,
             confidence, llm_score, stylo_score, label_text,
             status, created_at)
        VALUES
            (:content_id, :creator_id, :text_snippet, :attribution,
             :confidence, :llm_score, :stylo_score, :label_text,
             :status, :created_at)
    """, entry)
    conn.commit()
    conn.close()


def get_log(status_filter: str = None, limit: int = 50) -> list:
    """
    Return recent audit log entries as a list of plain dicts.

    Args:
        status_filter: if provided (e.g. "under_review"), return only
                       entries with that status. If None, return all.
        limit:         maximum number of entries to return (most recent first).

    Returns:
        List of dicts with all submission columns.
    """
    conn = get_connection()
    if status_filter:
        rows = conn.execute(
            "SELECT * FROM submissions WHERE status = ? ORDER BY created_at DESC LIMIT ?",
            (status_filter, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM submissions ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    conn.close()
    # Convert sqlite3.Row objects to plain dicts so jsonify can serialize them
    return [dict(row) for row in rows]


def get_submission(content_id: str) -> dict | None:
    """
    Look up a single submission by content_id.
    Returns a dict if found, None if not found.
    Used by the /appeal endpoint to validate that the content_id exists.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM submissions WHERE content_id = ?",
        (content_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_appeal(content_id: str, reasoning: str, timestamp: str) -> None:
    """
    Update an existing submission with appeal data.
    Flips status from "classified" → "under_review".

    Called by the /appeal endpoint after validating the content_id exists
    and the current status is "classified".
    """
    conn = get_connection()
    conn.execute("""
        UPDATE submissions
        SET status           = 'under_review',
            appeal_reasoning = ?,
            appeal_timestamp = ?
        WHERE content_id = ?
    """, (reasoning, timestamp, content_id))
    conn.commit()
    conn.close()