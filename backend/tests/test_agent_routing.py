"""
Unit tests for Agent routing integration — verifies _process_text dispatches
correctly based on router decisions and rate limiting.
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))


# ──────────────────────────── Helpers ──────────────────────────

def _make_agent():
    """Create a JarvisAgent with all external dependencies mocked."""
    mock_tracker = MagicMock()
    mock_tracker.can_afford.return_value = (True, "")
    with patch("agent.SpeechToText"), \
         patch("agent.TextToSpeech"), \
         patch("agent.get_cost_tracker", return_value=mock_tracker):
        from agent import JarvisAgent
        agent = JarvisAgent()

    # Mock broadcast so we can inspect messages
    agent._broadcast = AsyncMock()
    agent._loop = asyncio.get_event_loop()

    # Mock TTS so we don't try to speak
    agent.tts.speak = AsyncMock()
    agent.personaplex_active = True  # Skip TTS entirely

    return agent


def _mock_ollama_stream(agent, response_text: str):
    """Mock the Ollama LLM to stream a canned response."""
    async def fake_stream(text):
        for word in response_text.split():
            yield word + " "
    agent.llm.stream_response = fake_stream


def _mock_ollama_stream_from_messages(agent, response_text: str):
    """Mock Ollama's stream_response_from_messages."""
    async def fake_stream(messages, save_to_history=False):
        for word in response_text.split():
            yield word + " "
    agent.llm.stream_response_from_messages = fake_stream


def _mock_claude_stream(agent, response_text: str):
    """Mock the Claude client to stream a canned response."""
    async def fake_stream(text, conversation_history=None):
        for word in response_text.split():
            yield word + " "
    agent._claude_client.stream_response = fake_stream


def _get_broadcast_types(agent) -> list[str]:
    """Extract all broadcast message types from mock calls."""
    types = []
    for call in agent._broadcast.call_args_list:
        import json
        msg = json.loads(call[0][0])
        types.append(msg["type"])
    return types


def _get_broadcast_data(agent, msg_type: str) -> dict:
    """Get the data dict from a specific broadcast message type."""
    import json
    for call in agent._broadcast.call_args_list:
        msg = json.loads(call[0][0])
        if msg["type"] == msg_type:
            return msg["data"]
    return {}


# ──────────────────────────── Routing Tests ──────────────────────────

class TestOllamaRouting:
    @pytest.mark.asyncio
    async def test_greeting_routes_to_ollama(self):
        agent = _make_agent()
        _mock_ollama_stream(agent, "Good evening, sir.")

        await agent._process_text("Hello")

        # Should have route_decision broadcast
        route = _get_broadcast_data(agent, "route_decision")
        assert route["target"] == "ollama"
        assert route["intent_type"] == "greeting"

    @pytest.mark.asyncio
    async def test_simple_question_routes_to_ollama(self):
        agent = _make_agent()
        _mock_ollama_stream(agent, "I am JARVIS, sir.")

        await agent._process_text("Who are you")

        route = _get_broadcast_data(agent, "route_decision")
        assert route["target"] == "ollama"

    @pytest.mark.asyncio
    async def test_ollama_response_in_conversation_log(self):
        agent = _make_agent()
        _mock_ollama_stream(agent, "Hello sir.")

        await agent._process_text("Hi")

        assert len(agent.conversation_log) == 1
        assert agent.conversation_log[0]["role"] == "assistant"
        assert "Hello" in agent.conversation_log[0]["content"]


class TestClaudeRouting:
    @pytest.mark.asyncio
    async def test_complex_analysis_routes_to_claude(self):
        agent = _make_agent()
        _mock_claude_stream(agent, "TCP uses a three-way handshake...")

        with patch("config.ANTHROPIC_API_KEY", "sk-test-key"):
            await agent._process_text("Explain how TCP three-way handshake works in detail")

        route = _get_broadcast_data(agent, "route_decision")
        assert route["target"] == "claude"

    @pytest.mark.asyncio
    async def test_claude_response_syncs_to_ollama_history(self):
        agent = _make_agent()
        _mock_claude_stream(agent, "Here is my analysis.")

        with patch("config.ANTHROPIC_API_KEY", "sk-test-key"):
            await agent._process_text("Explain quantum computing in depth")

        # Should sync to Ollama's conversation_history
        assert len(agent.llm.conversation_history) >= 2
        assert agent.llm.conversation_history[-2]["role"] == "user"
        assert agent.llm.conversation_history[-1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_claude_no_key_falls_to_ollama(self):
        agent = _make_agent()
        _mock_ollama_stream(agent, "Let me explain...")

        with patch("config.ANTHROPIC_API_KEY", ""):
            await agent._process_text("Explain quantum computing in depth")

        route = _get_broadcast_data(agent, "route_decision")
        # Should downgrade to ollama when no API key
        assert route["target"] == "ollama"


class TestDirectToolRouting:
    @pytest.mark.asyncio
    async def test_weather_routes_to_direct_tool(self):
        agent = _make_agent()
        _mock_ollama_stream_from_messages(agent, "The weather in London is sunny, sir.")

        with patch("agent.execute_tool", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = {"temperature": 20, "condition": "sunny"}
            await agent._process_text("What's the weather in London")

        route = _get_broadcast_data(agent, "route_decision")
        assert route["target"] == "tool_direct"
        assert route["tool_hint"] == "weather.current"

    @pytest.mark.asyncio
    async def test_direct_tool_executes_tool(self):
        agent = _make_agent()
        _mock_ollama_stream_from_messages(agent, "It's sunny, sir.")

        with patch("agent.execute_tool", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = {"temp": 20}
            await agent._process_text("What's the weather in London")

        mock_exec.assert_called_once()
        tool_name = mock_exec.call_args[0][0]
        assert tool_name == "weather.current"

    @pytest.mark.asyncio
    async def test_direct_tool_broadcasts_tool_events(self):
        agent = _make_agent()
        _mock_ollama_stream_from_messages(agent, "Done, sir.")

        with patch("agent.execute_tool", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = {"status": "ok"}
            await agent._process_text("Check the Pi")

        types = _get_broadcast_types(agent)
        assert "tool_executing" in types
        assert "tool_result" in types


# ──────────────────────────── Rate Limiting Tests ──────────────────────────

class TestRateLimiting:
    @pytest.mark.asyncio
    async def test_rate_limited_returns_message(self):
        agent = _make_agent()
        # Force rate limiter to reject
        agent._rate_limiter.check = MagicMock(return_value=(False, {
            "source": "text", "retry_after_sec": 5.0
        }))

        await agent._process_text("Hello")

        types = _get_broadcast_types(agent)
        assert "rate_limited" in types
        # Should have a response_complete with rate limit message
        complete = _get_broadcast_data(agent, "response_complete")
        assert "rapidly" in complete["text"]

    @pytest.mark.asyncio
    async def test_rate_limited_skips_llm(self):
        agent = _make_agent()
        agent._rate_limiter.check = MagicMock(return_value=(False, {}))
        agent.llm.stream_response = AsyncMock()

        await agent._process_text("Hello")

        # LLM should NOT have been called
        agent.llm.stream_response.assert_not_called()


# ──────────────────────────── Tool Execution in LLM Response ──────────────────────────

class TestToolsInLLMResponse:
    @pytest.mark.asyncio
    async def test_ollama_with_tool_calls(self):
        agent = _make_agent()

        # Ollama response that contains a tool call
        tool_response = 'Let me check. ```tool\n{"tool": "notes.list", "args": {}}\n```'
        _mock_ollama_stream(agent, tool_response)
        _mock_ollama_stream_from_messages(agent, "You have 3 notes, sir.")

        with patch("agent.execute_tool", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = {"notes": [{"content": "test"}]}
            await agent._process_text("Show my notes")

        # Tool should have been executed
        mock_exec.assert_called_once()

        # Should have tool_executing and tool_result broadcasts
        types = _get_broadcast_types(agent)
        assert "tool_executing" in types
        assert "tool_result" in types


# ──────────────────────────── Broadcast Contract ──────────────────────────

class TestBroadcastContract:
    @pytest.mark.asyncio
    async def test_response_complete_includes_route(self):
        agent = _make_agent()
        _mock_ollama_stream(agent, "Hello, sir.")

        await agent._process_text("Hi")

        complete = _get_broadcast_data(agent, "response_complete")
        assert "route" in complete
        assert complete["route"] == "ollama"

    @pytest.mark.asyncio
    async def test_route_decision_broadcast_fields(self):
        agent = _make_agent()
        _mock_ollama_stream(agent, "Hi.")

        await agent._process_text("Hello")

        route = _get_broadcast_data(agent, "route_decision")
        assert "target" in route
        assert "intent_type" in route
        assert "confidence" in route
        assert "classification_ms" in route

    @pytest.mark.asyncio
    async def test_source_voice_passed_through(self):
        """Voice source should be used for rate limiting."""
        agent = _make_agent()
        _mock_ollama_stream(agent, "Hello.")
        agent._rate_limiter.check = MagicMock(return_value=(True, {}))

        await agent._process_text("Hi", source="voice")

        agent._rate_limiter.check.assert_called_with("voice")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
