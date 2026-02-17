"""
Jarvis Protocol — Persistent Conversation Memory
Inspired by Microsoft JARVIS's task planning + sukeesh/Jarvis's memory system.

Provides:
- Conversation summarization (condense old messages into key facts)
- Persistent memory store (facts survive across sessions)
- Contextual recall (inject relevant memories into LLM context)
"""
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import DATA_DIR

logger = logging.getLogger("jarvis.memory")

DB_PATH = DATA_DIR / "jarvis.db"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_tables():
    with _get_conn() as conn:
        # Long-term facts extracted from conversations
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                source TEXT DEFAULT 'conversation',
                importance INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                last_accessed TEXT NOT NULL,
                access_count INTEGER DEFAULT 0
            )
        """)
        # Conversation summaries (compressed history)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversation_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                summary TEXT NOT NULL,
                message_count INTEGER NOT NULL,
                topics TEXT DEFAULT '[]',
                created_at TEXT NOT NULL
            )
        """)


try:
    _ensure_tables()
except Exception as e:
    logger.error(f"Memory table init failed (will retry on first use): {e}")


# ────────────────────────── Memory CRUD ──────────────────────────

def store_memory(content: str, category: str = "general", source: str = "conversation", importance: int = 1) -> dict:
    """Store a fact in long-term memory."""
    now = datetime.now().isoformat()
    with _get_conn() as conn:
        # Check for duplicates (fuzzy match)
        existing = conn.execute(
            "SELECT id, content FROM memories WHERE content LIKE ? LIMIT 1",
            (f"%{content[:50]}%",)
        ).fetchone()
        if existing:
            # Update existing memory instead of duplicating
            conn.execute(
                "UPDATE memories SET last_accessed = ?, access_count = access_count + 1 WHERE id = ?",
                (now, existing["id"])
            )
            logger.info(f"Memory #{existing['id']} reinforced: {content[:60]}")
            return {"id": existing["id"], "action": "reinforced", "content": content}

        cur = conn.execute(
            """INSERT INTO memories (content, category, source, importance, created_at, last_accessed)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (content, category, source, importance, now, now)
        )
        logger.info(f"Memory #{cur.lastrowid} stored: {content[:60]}")
        return {"id": cur.lastrowid, "action": "created", "content": content}


def recall_memories(query: str = "", category: str = None, limit: int = 10, update_access: bool = True) -> list[dict]:
    """Recall relevant memories, sorted by importance and recency.
    Set update_access=False for passive context injection to avoid inflating access counts.
    """
    with _get_conn() as conn:
        conditions = []
        params = []

        if query:
            conditions.append("content LIKE ?")
            params.append(f"%{query}%")
        if category:
            conditions.append("category = ?")
            params.append(category)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = conn.execute(
            f"""SELECT * FROM memories {where}
                ORDER BY importance DESC, last_accessed DESC
                LIMIT ?""",
            (*params, limit)
        ).fetchall()

        # Only update access timestamps on explicit recalls (not passive context injection)
        if update_access:
            now = datetime.now().isoformat()
            for row in rows:
                conn.execute(
                    "UPDATE memories SET last_accessed = ?, access_count = access_count + 1 WHERE id = ?",
                    (now, row["id"])
                )

    return [dict(r) for r in rows]


def delete_memory(memory_id: int) -> bool:
    """Delete a memory by ID."""
    with _get_conn() as conn:
        cur = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        return cur.rowcount > 0


def get_memory_context(limit: int = 5) -> str:
    """Get a formatted string of recent/important memories for LLM context injection."""
    memories = recall_memories(limit=limit, update_access=False)
    if not memories:
        return ""

    lines = ["[Long-term memories on file:]"]
    for m in memories:
        lines.append(f"- [{m['category']}] {m['content']}")
    return "\n".join(lines)


# ────────────────────────── Conversation Summarization ──────────────────────────

def summarize_conversation(messages: list[dict], max_messages: int = 20) -> Optional[str]:
    """
    Generate a summary prompt for condensing old conversation messages.
    Returns a prompt string that should be sent to the LLM for summarization.
    Inspired by Microsoft JARVIS's multi-stage pipeline approach.
    """
    if len(messages) <= max_messages:
        return None  # No need to summarize

    # Take the oldest messages that will be dropped
    to_summarize = messages[:-max_messages]

    # Build a condensation prompt
    conversation_text = ""
    for msg in to_summarize:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")[:200]
        conversation_text += f"{role}: {content}\n"

    return (
        f"Summarize the following conversation excerpt into 2-3 key facts or decisions. "
        f"Extract any user preferences, important information, or action items. "
        f"Be concise — just the essential facts:\n\n{conversation_text}"
    )


def store_summary(summary: str, message_count: int, topics: list[str] = None):
    """Store a conversation summary."""
    now = datetime.now().isoformat()
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO conversation_summaries (summary, message_count, topics, created_at)
               VALUES (?, ?, ?, ?)""",
            (summary, message_count, json.dumps(topics or []), now)
        )
    logger.info(f"Conversation summary stored ({message_count} messages condensed)")


def get_recent_summaries(limit: int = 3) -> list[dict]:
    """Get recent conversation summaries for context."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM conversation_summaries ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ────────────────────────── Fact Extraction ──────────────────────────

def build_extraction_prompt(user_message: str, assistant_response: str) -> str:
    """
    Build a prompt to extract memorable facts from a conversation turn.
    Only used when the conversation contains potentially important information.
    """
    # Heuristic: only extract if the message is substantive
    if len(user_message) < 20 and len(assistant_response) < 50:
        return ""

    keywords = [
        "remember", "always", "never", "prefer", "my name", "i am", "i'm",
        "i live", "i work", "my favorite", "important", "don't forget",
        "schedule", "deadline", "password", "key", "address", "birthday",
    ]
    should_extract = any(kw in user_message.lower() for kw in keywords)

    if not should_extract:
        return ""

    return (
        f"The user said: \"{user_message}\"\n"
        f"Your response was: \"{assistant_response[:200]}\"\n\n"
        f"Extract any personal facts, preferences, or important information from this exchange. "
        f"Return a JSON array of objects with 'content' and 'category' fields. "
        f"Categories: personal, preference, schedule, work, technical. "
        f"If nothing worth remembering, return an empty array: []"
    )


def get_memory_summary() -> dict:
    """Get memory stats for the dashboard."""
    with _get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        categories = conn.execute(
            "SELECT category, COUNT(*) as count FROM memories GROUP BY category ORDER BY count DESC"
        ).fetchall()
        recent = conn.execute(
            "SELECT content, category FROM memories ORDER BY last_accessed DESC LIMIT 3"
        ).fetchall()
        summaries = conn.execute("SELECT COUNT(*) FROM conversation_summaries").fetchone()[0]

    return {
        "total_memories": total,
        "categories": [{"category": r["category"], "count": r["count"]} for r in categories],
        "recent": [{"content": r["content"], "category": r["category"]} for r in recent],
        "conversation_summaries": summaries,
    }
