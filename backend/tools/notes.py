"""
Jarvis Protocol — Notes / Mental Notes System
Local SQLite-backed notes with tags, search, and listing.
"""
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import DATA_DIR

logger = logging.getLogger("jarvis.tools.notes")

DB_PATH = DATA_DIR / "jarvis.db"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_table():
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                tag TEXT DEFAULT 'general',
                pinned INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)


_ensure_table()


def add_note(content: str, tag: str = "general") -> dict:
    """Add a new mental note."""
    now = datetime.now().isoformat()
    with _get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO notes (content, tag, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (content, tag.lower(), now, now)
        )
        note_id = cur.lastrowid
    logger.info(f"Note #{note_id} added: {content[:50]}...")
    return {"id": note_id, "content": content, "tag": tag, "created_at": now}


def list_notes(tag: Optional[str] = None, limit: int = 20) -> list[dict]:
    """List notes, optionally filtered by tag."""
    with _get_conn() as conn:
        if tag:
            rows = conn.execute(
                "SELECT * FROM notes WHERE tag = ? ORDER BY pinned DESC, created_at DESC LIMIT ?",
                (tag.lower(), limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM notes ORDER BY pinned DESC, created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


def search_notes(query: str) -> list[dict]:
    """Search notes by content."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM notes WHERE content LIKE ? ORDER BY created_at DESC LIMIT 20",
            (f"%{query}%",)
        ).fetchall()
    return [dict(r) for r in rows]


def delete_note(note_id: int) -> bool:
    """Delete a note by ID."""
    with _get_conn() as conn:
        cur = conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        return cur.rowcount > 0


def pin_note(note_id: int, pinned: bool = True) -> bool:
    """Pin or unpin a note."""
    with _get_conn() as conn:
        cur = conn.execute(
            "UPDATE notes SET pinned = ? WHERE id = ?",
            (1 if pinned else 0, note_id)
        )
        return cur.rowcount > 0


def get_notes_summary() -> dict:
    """Get a summary of notes for the dashboard."""
    with _get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        pinned = conn.execute("SELECT COUNT(*) FROM notes WHERE pinned = 1").fetchone()[0]
        tags = conn.execute(
            "SELECT tag, COUNT(*) as count FROM notes GROUP BY tag ORDER BY count DESC"
        ).fetchall()
        recent = conn.execute(
            "SELECT content, tag FROM notes ORDER BY created_at DESC LIMIT 3"
        ).fetchall()
    return {
        "total": total,
        "pinned": pinned,
        "tags": [{"tag": r["tag"], "count": r["count"]} for r in tags],
        "recent": [{"content": r["content"], "tag": r["tag"]} for r in recent]
    }
