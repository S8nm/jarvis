"""
Unit tests for IntentRouter — classification, routing, budget gating.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from llm.router import IntentRouter, RouteDecision

# Patch ANTHROPIC_API_KEY for tests that need Claude routing to work
_MOCK_KEY = "sk-test-fake-key-for-testing"


# ──────────────────────────── Fixtures ──────────────────────────

@pytest.fixture
def router():
    """Router with mocked API key so Claude routes aren't gated."""
    with patch("config.ANTHROPIC_API_KEY", _MOCK_KEY):
        yield IntentRouter()


@pytest.fixture
def router_no_key():
    """Router with no API key — Claude routes should downgrade."""
    with patch("config.ANTHROPIC_API_KEY", ""):
        yield IntentRouter()


@pytest.fixture
def router_with_budget():
    tracker = MagicMock()
    tracker.can_afford.return_value = (True, "")
    with patch("config.ANTHROPIC_API_KEY", _MOCK_KEY):
        yield IntentRouter(cost_tracker=tracker)


@pytest.fixture
def router_over_budget():
    tracker = MagicMock()
    tracker.can_afford.return_value = (False, "Daily limit: $5.00/$5.00")
    with patch("config.ANTHROPIC_API_KEY", _MOCK_KEY):
        yield IntentRouter(cost_tracker=tracker)


# ──────────────────────────── Greeting Tests ──────────────────────────

class TestGreetings:
    @pytest.mark.asyncio
    async def test_hello(self, router):
        d = await router.classify("Hello")
        assert d.target == "ollama"
        assert d.intent_type == "greeting"

    @pytest.mark.asyncio
    async def test_hey_jarvis(self, router):
        d = await router.classify("Hey Jarvis")
        assert d.target == "ollama"
        assert d.intent_type == "greeting"

    @pytest.mark.asyncio
    async def test_good_morning(self, router):
        d = await router.classify("Good morning")
        assert d.target == "ollama"
        assert d.intent_type == "greeting"

    @pytest.mark.asyncio
    async def test_thanks(self, router):
        d = await router.classify("Thanks")
        assert d.target == "ollama"
        assert d.intent_type == "greeting"

    @pytest.mark.asyncio
    async def test_long_greeting_not_matched(self, router):
        """Long sentences starting with 'hello' are not greetings."""
        d = await router.classify("Hello can you help me write a Python script for data analysis")
        assert d.intent_type != "greeting"


# ──────────────────────────── Direct Tool Tests ──────────────────────────

class TestDirectTool:
    @pytest.mark.asyncio
    async def test_weather(self, router):
        d = await router.classify("What's the weather in London")
        assert d.target == "tool_direct"
        assert d.tool_hint == "weather.current"
        assert "london" in d.tool_args_hint.get("location", "").lower()

    @pytest.mark.asyncio
    async def test_calendar_today(self, router):
        d = await router.classify("What's on my calendar today")
        assert d.target == "tool_direct"
        assert d.tool_hint == "calendar.today"

    @pytest.mark.asyncio
    async def test_add_note(self, router):
        d = await router.classify("Add a note: buy groceries tomorrow")
        assert d.target == "tool_direct"
        assert d.tool_hint == "notes.add"
        assert "groceries" in d.tool_args_hint.get("content", "").lower()

    @pytest.mark.asyncio
    async def test_list_notes(self, router):
        d = await router.classify("Show my notes")
        assert d.target == "tool_direct"
        assert d.tool_hint == "notes.list"

    @pytest.mark.asyncio
    async def test_pi_status(self, router):
        d = await router.classify("Check the Pi")
        assert d.target == "tool_direct"
        assert d.tool_hint == "pi.system_info"


# ──────────────────────────── Coding Tests ──────────────────────────

class TestCoding:
    @pytest.mark.asyncio
    async def test_simple_coding(self, router):
        d = await router.classify("Write a function to reverse a string")
        assert d.intent_type == "coding"
        # Short coding task -> Ollama
        assert d.target == "ollama"

    @pytest.mark.asyncio
    async def test_complex_coding(self, router):
        d = await router.classify(
            "Design a distributed system architecture for a microservice-based "
            "e-commerce platform with event sourcing and CQRS pattern"
        )
        assert d.target == "claude"

    @pytest.mark.asyncio
    async def test_debug_request(self, router):
        d = await router.classify("Debug this code for me")
        assert d.intent_type == "coding"

    @pytest.mark.asyncio
    async def test_code_review(self, router):
        d = await router.classify("Code review this pull request")
        assert d.intent_type == "coding"


# ──────────────────────────── Analysis Tests ──────────────────────────

class TestAnalysis:
    @pytest.mark.asyncio
    async def test_explain_how(self, router):
        d = await router.classify("Explain how TCP three-way handshake works in detail")
        assert d.target == "claude"
        assert d.intent_type == "analysis"

    @pytest.mark.asyncio
    async def test_compare(self, router):
        d = await router.classify("Compare and contrast REST vs GraphQL")
        assert d.target == "claude"

    @pytest.mark.asyncio
    async def test_pros_and_cons(self, router):
        d = await router.classify("What are the pros and cons of microservices")
        assert d.target == "claude"

    @pytest.mark.asyncio
    async def test_research(self, router):
        d = await router.classify("Research the latest developments in quantum computing")
        assert d.target == "claude"


# ──────────────────────────── Planning Tests ──────────────────────────

class TestPlanning:
    @pytest.mark.asyncio
    async def test_plan_for(self, router):
        d = await router.classify("Create a plan for migrating our database")
        assert d.target == "claude"
        assert d.intent_type == "planning"

    @pytest.mark.asyncio
    async def test_step_by_step(self, router):
        d = await router.classify("Give me a step-by-step guide to setting up Docker")
        assert d.target == "claude"

    @pytest.mark.asyncio
    async def test_how_should_i(self, router):
        d = await router.classify("How should I approach building a REST API")
        assert d.target == "claude"


# ──────────────────────────── Explicit Claude Request ──────────────────────────

class TestExplicitClaude:
    @pytest.mark.asyncio
    async def test_ask_claude(self, router):
        d = await router.classify("Ask Claude about quantum computing")
        assert d.target == "claude"
        assert d.confidence >= 0.9

    @pytest.mark.asyncio
    async def test_use_claude(self, router):
        d = await router.classify("Use Claude to help with this problem")
        assert d.target == "claude"


# ──────────────────────────── Simple / Chitchat Tests ──────────────────────────

class TestSimpleAndChitchat:
    @pytest.mark.asyncio
    async def test_simple_question(self, router):
        d = await router.classify("What time is it")
        assert d.target == "ollama"
        assert d.intent_type == "question"

    @pytest.mark.asyncio
    async def test_who_are_you(self, router):
        d = await router.classify("Who are you")
        assert d.target == "ollama"

    @pytest.mark.asyncio
    async def test_generic_chitchat(self, router):
        d = await router.classify("Tell me something interesting")
        assert d.target == "ollama"

    @pytest.mark.asyncio
    async def test_short_unclear(self, router):
        """Short ambiguous input defaults to Ollama."""
        d = await router.classify("hmm okay")
        assert d.target == "ollama"


# ──────────────────────────── Budget Gating Tests ──────────────────────────

class TestBudgetGating:
    @pytest.mark.asyncio
    async def test_claude_allowed_within_budget(self, router_with_budget):
        d = await router_with_budget.classify("Explain quantum entanglement in depth")
        assert d.target == "claude"

    @pytest.mark.asyncio
    async def test_claude_downgraded_over_budget(self, router_over_budget):
        d = await router_over_budget.classify("Explain quantum entanglement in depth")
        assert d.target == "ollama"  # Downgraded!
        assert "budget" in d.reason.lower()

    @pytest.mark.asyncio
    async def test_no_api_key_downgrades(self, router_no_key):
        d = await router_no_key.classify("Explain quantum entanglement in depth")
        assert d.target == "ollama"
        assert "api key" in d.reason.lower()


# ──────────────────────────── Performance Tests ──────────────────────────

class TestPerformance:
    @pytest.mark.asyncio
    async def test_classification_under_5ms(self, router):
        """Rule-based classification should be fast."""
        import time
        start = time.perf_counter()
        for _ in range(100):
            await router.classify("What's the weather in London")
        elapsed = (time.perf_counter() - start) * 1000
        avg = elapsed / 100
        assert avg < 5.0, f"Avg classification time {avg:.2f}ms exceeds 5ms"


# ──────────────────────────── Stats Tests ──────────────────────────

class TestStats:
    @pytest.mark.asyncio
    async def test_stats_tracking(self, router):
        await router.classify("Hello")
        await router.classify("Explain quantum computing in depth")
        await router.classify("What's the weather")

        stats = router.get_stats()
        assert stats["total_routes"] == 3
        assert stats["avg_classification_ms"] > 0
        assert sum(stats["tier_distribution"].values()) == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
