"""
Jarvis Protocol — Calendar System
Local SQLite-backed calendar with ICS export capability.
Supports personal + uni calendars.
"""
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from config import DATA_DIR

logger = logging.getLogger("jarvis.tools.calendar")

DB_PATH = DATA_DIR / "jarvis.db"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_table():
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                calendar TEXT DEFAULT 'personal',
                start_time TEXT NOT NULL,
                end_time TEXT,
                location TEXT DEFAULT '',
                reminder_minutes INTEGER DEFAULT 15,
                recurring TEXT DEFAULT NULL,
                created_at TEXT NOT NULL
            )
        """)


_ensure_table()


def create_event(
    title: str,
    start_time: str,
    end_time: Optional[str] = None,
    description: str = "",
    calendar: str = "personal",
    location: str = "",
    reminder_minutes: int = 15
) -> dict:
    """Create a new calendar event."""
    now = datetime.now().isoformat()

    # Parse and validate start_time
    try:
        start_dt = _parse_datetime(start_time)
    except ValueError as e:
        return {"error": f"Invalid start_time: {e}"}

    end_dt = None
    if end_time:
        try:
            end_dt = _parse_datetime(end_time)
        except ValueError as e:
            return {"error": f"Invalid end_time: {e}"}
    else:
        end_dt = start_dt + timedelta(hours=1)

    with _get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO events (title, description, calendar, start_time, end_time,
               location, reminder_minutes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (title, description, calendar.lower(), start_dt.isoformat(),
             end_dt.isoformat(), location, reminder_minutes, now)
        )
        event_id = cur.lastrowid

    logger.info(f"Event #{event_id} created: {title} at {start_dt}")
    return {
        "id": event_id, "title": title, "start_time": start_dt.isoformat(),
        "end_time": end_dt.isoformat(), "calendar": calendar, "location": location
    }


def list_events(
    calendar: Optional[str] = None,
    days_ahead: int = 7,
    limit: int = 20
) -> list[dict]:
    """List upcoming events."""
    now = datetime.now().isoformat()
    future = (datetime.now() + timedelta(days=days_ahead)).isoformat()

    with _get_conn() as conn:
        if calendar:
            rows = conn.execute(
                """SELECT * FROM events WHERE calendar = ? AND start_time >= ? AND start_time <= ?
                   ORDER BY start_time ASC LIMIT ?""",
                (calendar.lower(), now, future, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM events WHERE start_time >= ? AND start_time <= ?
                   ORDER BY start_time ASC LIMIT ?""",
                (now, future, limit)
            ).fetchall()
    return [dict(r) for r in rows]


def get_today_events() -> list[dict]:
    """Get events for today."""
    today_start = datetime.now().replace(hour=0, minute=0, second=0).isoformat()
    today_end = datetime.now().replace(hour=23, minute=59, second=59).isoformat()

    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE start_time >= ? AND start_time <= ? ORDER BY start_time ASC",
            (today_start, today_end)
        ).fetchall()
    return [dict(r) for r in rows]


def delete_event(event_id: int) -> bool:
    """Delete an event by ID."""
    with _get_conn() as conn:
        cur = conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
        return cur.rowcount > 0


def export_ics(calendar: Optional[str] = None, days_ahead: int = 30) -> str:
    """Export events as ICS format string."""
    events = list_events(calendar=calendar, days_ahead=days_ahead, limit=100)

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//JARVIS Protocol//EN",
        f"X-WR-CALNAME:JARVIS {calendar or 'All'} Calendar",
    ]

    for evt in events:
        start = datetime.fromisoformat(evt["start_time"])
        end = datetime.fromisoformat(evt["end_time"]) if evt.get("end_time") else start + timedelta(hours=1)

        lines.extend([
            "BEGIN:VEVENT",
            f"UID:jarvis-{evt['id']}@local",
            f"DTSTART:{start.strftime('%Y%m%dT%H%M%S')}",
            f"DTEND:{end.strftime('%Y%m%dT%H%M%S')}",
            f"SUMMARY:{evt['title']}",
            f"DESCRIPTION:{evt.get('description', '')}",
            f"LOCATION:{evt.get('location', '')}",
            "END:VEVENT",
        ])

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)


def get_calendar_summary() -> dict:
    """Get a summary for the dashboard."""
    today = get_today_events()
    upcoming = list_events(days_ahead=7, limit=5)

    with _get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        calendars = conn.execute(
            "SELECT calendar, COUNT(*) as count FROM events GROUP BY calendar"
        ).fetchall()

    return {
        "today_count": len(today),
        "today_events": [{"title": e["title"], "start_time": e["start_time"]} for e in today],
        "upcoming_count": len(upcoming),
        "upcoming_events": [{"title": e["title"], "start_time": e["start_time"], "calendar": e["calendar"]} for e in upcoming],
        "total": total,
        "calendars": [{"name": r["calendar"], "count": r["count"]} for r in calendars]
    }


def _parse_datetime(s: str) -> datetime:
    """Parse various datetime formats."""
    formats = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue

    # Try relative dates
    s_lower = s.lower().strip()
    now = datetime.now()

    if s_lower == "today":
        return now.replace(hour=9, minute=0, second=0, microsecond=0)
    elif s_lower == "tomorrow":
        return (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    elif s_lower.startswith("in "):
        # e.g., "in 2 hours", "in 30 minutes"
        parts = s_lower.split()
        if len(parts) >= 3:
            try:
                amount = int(parts[1])
                unit = parts[2]
                if "hour" in unit:
                    return now + timedelta(hours=amount)
                elif "minute" in unit or "min" in unit:
                    return now + timedelta(minutes=amount)
                elif "day" in unit:
                    return now + timedelta(days=amount)
            except ValueError:
                pass

    raise ValueError(f"Cannot parse datetime: '{s}'")
