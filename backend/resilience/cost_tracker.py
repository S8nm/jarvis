"""
Jarvis Protocol — Claude API Cost Tracker

SQLite-backed usage tracking with daily/monthly budget enforcement.
Persists across restarts so cost data is never lost.

Usage:
    tracker = get_cost_tracker()
    tracker.log_usage("claude-sonnet-4-5-20250929", input_tokens=500, output_tokens=200)
    can_proceed = tracker.can_afford()
    report = tracker.get_report()
"""
import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path

from config import _cfg, DATA_DIR

logger = logging.getLogger("jarvis.resilience.cost_tracker")


# ──────────────────────────── Pricing ────────────────────────────
# Per 1M tokens — update when Anthropic changes pricing
PRICING = {
    "claude-sonnet-4-5-20250929": {
        "input": 3.00,
        "output": 15.00,
        "cache_read": 0.30,
        "cache_write": 3.75,
    },
    "claude-opus-4-6": {
        "input": 15.00,
        "output": 75.00,
        "cache_read": 1.50,
        "cache_write": 18.75,
    },
    "claude-haiku-4-5-20251001": {
        "input": 0.80,
        "output": 4.00,
        "cache_read": 0.08,
        "cache_write": 1.00,
    },
}

_DEFAULT_PRICING = {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75}

# ──────────────────────────── Budget config ────────────────────────────
_budget_cfg = _cfg("claude_budget", {})
if not isinstance(_budget_cfg, dict):
    _budget_cfg = {}

_DAILY_LIMIT = _budget_cfg.get("daily_usd", 5.00)
_MONTHLY_LIMIT = _budget_cfg.get("monthly_usd", 50.00)
_WARN_THRESHOLD = _budget_cfg.get("warn_threshold", 0.80)

DB_PATH = DATA_DIR / "cost_tracking.db"


class CostTracker:
    """SQLite-backed Claude API usage tracker with budget enforcement."""

    def __init__(self, db_path: Path = DB_PATH):
        self._db_path = db_path
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS claude_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                model TEXT NOT NULL,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                cache_read_tokens INTEGER NOT NULL DEFAULT 0,
                cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
                cost_usd REAL NOT NULL DEFAULT 0.0,
                request_type TEXT NOT NULL DEFAULT 'sync',
                summary TEXT DEFAULT ''
            )
        """)
        conn.commit()
        conn.close()
        logger.info(f"Cost tracker initialized: {self._db_path}")

    def calculate_cost(self, model: str, input_tokens: int, output_tokens: int,
                       cache_read: int = 0, cache_creation: int = 0) -> float:
        """Calculate cost in USD from token counts."""
        prices = PRICING.get(model, _DEFAULT_PRICING)
        regular_input = max(0, input_tokens - cache_read - cache_creation)

        cost = (
            (regular_input / 1_000_000) * prices["input"]
            + (output_tokens / 1_000_000) * prices["output"]
            + (cache_read / 1_000_000) * prices["cache_read"]
            + (cache_creation / 1_000_000) * prices["cache_write"]
        )
        return round(cost, 6)

    def log_usage(self, model: str, input_tokens: int, output_tokens: int,
                  cache_read: int = 0, cache_creation: int = 0,
                  request_type: str = "sync", summary: str = "") -> float:
        """
        Log a Claude API call and return the cost.
        Batch calls should use request_type="batch".
        """
        cost = self.calculate_cost(model, input_tokens, output_tokens,
                                   cache_read, cache_creation)
        now = datetime.now().isoformat()

        conn = sqlite3.connect(str(self._db_path))
        conn.execute(
            """INSERT INTO claude_usage
               (timestamp, model, input_tokens, output_tokens,
                cache_read_tokens, cache_creation_tokens, cost_usd,
                request_type, summary)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (now, model, input_tokens, output_tokens,
             cache_read, cache_creation, cost,
             request_type, summary[:200])
        )
        conn.commit()
        conn.close()

        logger.info(
            f"Claude cost: ${cost:.4f} | {model} | "
            f"{input_tokens}in+{output_tokens}out "
            f"(cache: {cache_read}r/{cache_creation}w) | {request_type}"
        )
        return cost

    def get_daily_spend(self) -> float:
        """Get today's total Claude spend."""
        today = date.today().isoformat()
        conn = sqlite3.connect(str(self._db_path))
        result = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM claude_usage WHERE timestamp >= ?",
            (today,)
        ).fetchone()[0]
        conn.close()
        return round(result, 4)

    def get_monthly_spend(self) -> float:
        """Get this month's total Claude spend."""
        month_start = date.today().replace(day=1).isoformat()
        conn = sqlite3.connect(str(self._db_path))
        result = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM claude_usage WHERE timestamp >= ?",
            (month_start,)
        ).fetchone()[0]
        conn.close()
        return round(result, 4)

    def can_afford(self, estimated_tokens: int = 4000, model: str = "") -> tuple[bool, str]:
        """
        Check if budget allows a request.
        Returns (allowed, reason).
        """
        daily = self.get_daily_spend()
        monthly = self.get_monthly_spend()

        if daily >= _DAILY_LIMIT:
            return False, f"Daily budget exhausted: ${daily:.2f}/${_DAILY_LIMIT:.2f}"
        if monthly >= _MONTHLY_LIMIT:
            return False, f"Monthly budget exhausted: ${monthly:.2f}/${_MONTHLY_LIMIT:.2f}"

        return True, ""

    def get_report(self) -> dict:
        """Full report for frontend dashboard."""
        daily_spend = self.get_daily_spend()
        monthly_spend = self.get_monthly_spend()

        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row

        # Today's stats
        today = date.today().isoformat()
        today_row = conn.execute("""
            SELECT COUNT(*) as calls,
                   COALESCE(SUM(input_tokens), 0) as input_tokens,
                   COALESCE(SUM(output_tokens), 0) as output_tokens,
                   COALESCE(SUM(cache_read_tokens), 0) as cache_hits
            FROM claude_usage WHERE timestamp >= ?
        """, (today,)).fetchone()

        # Recent calls (last 10)
        recent = conn.execute("""
            SELECT timestamp, model, input_tokens, output_tokens,
                   cache_read_tokens, cost_usd, request_type, summary
            FROM claude_usage ORDER BY timestamp DESC LIMIT 10
        """).fetchall()

        conn.close()

        daily_warning = daily_spend >= _DAILY_LIMIT * _WARN_THRESHOLD
        monthly_warning = monthly_spend >= _MONTHLY_LIMIT * _WARN_THRESHOLD

        return {
            "today": {
                "spend": daily_spend,
                "calls": today_row["calls"],
                "input_tokens": today_row["input_tokens"],
                "output_tokens": today_row["output_tokens"],
                "cache_hits": today_row["cache_hits"],
            },
            "month": {
                "spend": monthly_spend,
            },
            "budget": {
                "daily_limit": _DAILY_LIMIT,
                "monthly_limit": _MONTHLY_LIMIT,
                "daily_remaining": round(max(0, _DAILY_LIMIT - daily_spend), 4),
                "monthly_remaining": round(max(0, _MONTHLY_LIMIT - monthly_spend), 4),
            },
            "warning": daily_warning or monthly_warning,
            "recent": [dict(r) for r in recent],
        }


# ──────────────────────────── Singleton ────────────────────────────

_instance: CostTracker | None = None


def get_cost_tracker() -> CostTracker:
    """Get or create the global CostTracker singleton."""
    global _instance
    if _instance is None:
        _instance = CostTracker()
    return _instance
