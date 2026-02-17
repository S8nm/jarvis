"""
Unit tests for CostTracker — cost calculation, budget enforcement, usage logging.
"""
import sys
from pathlib import Path

import pytest

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from resilience.cost_tracker import CostTracker, PRICING


# ──────────────────────────── Fixtures ──────────────────────────

@pytest.fixture
def tracker(tmp_path):
    """CostTracker with a temporary database."""
    return CostTracker(db_path=tmp_path / "test_costs.db")


# ──────────────────────────── Cost Calculation ──────────────────────────

class TestCostCalculation:
    def test_basic_cost(self, tracker):
        """Simple input + output cost, no cache."""
        cost = tracker.calculate_cost(
            "claude-sonnet-4-5-20250929",
            input_tokens=1000,
            output_tokens=500,
        )
        # input: 1000/1M * 3.00 = 0.003
        # output: 500/1M * 15.00 = 0.0075
        assert abs(cost - 0.0105) < 0.0001

    def test_cached_cost_cheaper(self, tracker):
        """Cache reads should cost less than regular input."""
        cost_no_cache = tracker.calculate_cost(
            "claude-sonnet-4-5-20250929",
            input_tokens=10000,
            output_tokens=1000,
        )
        cost_with_cache = tracker.calculate_cost(
            "claude-sonnet-4-5-20250929",
            input_tokens=10000,
            output_tokens=1000,
            cache_read=8000,
        )
        assert cost_with_cache < cost_no_cache

    def test_unknown_model_uses_default(self, tracker):
        """Unknown model falls back to default pricing."""
        cost = tracker.calculate_cost(
            "some-unknown-model",
            input_tokens=1000,
            output_tokens=500,
        )
        # Should use default pricing (same as sonnet)
        assert cost > 0

    def test_zero_tokens_zero_cost(self, tracker):
        cost = tracker.calculate_cost("claude-sonnet-4-5-20250929", 0, 0)
        assert cost == 0.0

    def test_opus_more_expensive(self, tracker):
        """Opus should cost more than Sonnet for same tokens."""
        sonnet_cost = tracker.calculate_cost(
            "claude-sonnet-4-5-20250929", input_tokens=1000, output_tokens=1000)
        opus_cost = tracker.calculate_cost(
            "claude-opus-4-6", input_tokens=1000, output_tokens=1000)
        assert opus_cost > sonnet_cost


# ──────────────────────────── Usage Logging ──────────────────────────

class TestUsageLogging:
    def test_log_usage_returns_cost(self, tracker):
        cost = tracker.log_usage(
            "claude-sonnet-4-5-20250929",
            input_tokens=1000, output_tokens=500,
        )
        assert cost > 0

    def test_daily_spend_accumulates(self, tracker):
        tracker.log_usage("claude-sonnet-4-5-20250929", 1000, 500)
        tracker.log_usage("claude-sonnet-4-5-20250929", 2000, 1000)

        daily = tracker.get_daily_spend()
        assert daily > 0

    def test_monthly_spend_accumulates(self, tracker):
        tracker.log_usage("claude-sonnet-4-5-20250929", 1000, 500)
        monthly = tracker.get_monthly_spend()
        assert monthly > 0


# ──────────────────────────── Budget Enforcement ──────────────────────────

class TestBudgetEnforcement:
    def test_can_afford_when_empty(self, tracker):
        allowed, reason = tracker.can_afford()
        assert allowed is True
        assert reason == ""

    def test_budget_exhausted_blocks(self, tracker):
        """Massive usage should trigger budget block."""
        # Log many expensive calls to exceed daily limit
        for _ in range(100):
            tracker.log_usage("claude-opus-4-6", 100000, 50000)

        allowed, reason = tracker.can_afford()
        assert allowed is False
        assert "exhausted" in reason.lower()


# ──────────────────────────── Reporting ──────────────────────────

class TestReporting:
    def test_empty_report(self, tracker):
        report = tracker.get_report()
        assert report["today"]["spend"] == 0
        assert report["today"]["calls"] == 0
        assert report["budget"]["daily_limit"] > 0
        assert report["warning"] is False

    def test_report_with_usage(self, tracker):
        tracker.log_usage("claude-sonnet-4-5-20250929", 5000, 2000, summary="test query")
        report = tracker.get_report()
        assert report["today"]["calls"] == 1
        assert report["today"]["spend"] > 0
        assert len(report["recent"]) == 1
        assert report["recent"][0]["summary"] == "test query"

    def test_report_budget_remaining(self, tracker):
        report = tracker.get_report()
        assert report["budget"]["daily_remaining"] == report["budget"]["daily_limit"]
        assert report["budget"]["monthly_remaining"] == report["budget"]["monthly_limit"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
