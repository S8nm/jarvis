"""
Jarvis Protocol — Intent Router

Classifies incoming queries and routes to the optimal LLM backend.
Hybrid approach: fast rule-based heuristics (<1ms) + optional Ollama tiebreaker (<150ms).

Routing tiers:
  1. TOOL_DIRECT — Skip LLM, execute tool directly (clear intent patterns)
  2. OLLAMA     — Simple queries, chitchat, greetings (fast, free)
  3. CLAUDE     — Complex reasoning, coding, analysis (expensive, powerful)
"""
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from config import _cfg, ANTHROPIC_API_KEY

logger = logging.getLogger("jarvis.llm.router")

# ──────────────────────────── Config ────────────────────────────
_router_cfg = _cfg("router", {})
if not isinstance(_router_cfg, dict):
    _router_cfg = {}

_ENABLED = _router_cfg.get("enabled", True)
_COMPLEX_WORD_THRESHOLD = _router_cfg.get("complex_word_threshold", 80)
_SIMPLE_WORD_THRESHOLD = _router_cfg.get("simple_word_threshold", 15)


# ──────────────────────────── Data Types ────────────────────────────

@dataclass
class RouteDecision:
    """Result of the router's classification."""
    target: str             # "ollama" | "claude" | "tool_direct"
    confidence: float       # 0.0 - 1.0
    intent_type: str        # "chitchat" | "greeting" | "question" | "action" | "planning" | "coding" | "analysis"
    reason: str             # Human-readable explanation for logging
    tool_hint: str = ""     # If tool_direct, which tool to call
    tool_args_hint: dict = field(default_factory=dict)  # Pre-parsed args for direct dispatch
    classification_ms: float = 0.0


# ──────────────────────────── Router ────────────────────────────

class IntentRouter:
    """
    Hybrid intent classifier and LLM router.
    Rule-based first pass, budget/availability checks, fallback logic.
    """

    def __init__(self, cost_tracker=None):
        self._cost_tracker = cost_tracker
        self._route_count = 0
        self._tier_counts = {"ollama": 0, "claude": 0, "tool_direct": 0}
        self._avg_ms = 0.0

    async def classify(self, user_input: str, conversation_history: list = None) -> RouteDecision:
        """
        Classify user input and return a routing decision.
        The entire pipeline targets < 5ms for rule-based path.
        """
        if not _ENABLED:
            return RouteDecision(
                target="ollama", confidence=1.0, intent_type="default",
                reason="Router disabled"
            )

        start = time.perf_counter()
        decision = self._rule_classify(user_input)

        # Budget/availability gate for Claude
        if decision.target == "claude":
            decision = self._gate_claude(decision)

        decision.classification_ms = (time.perf_counter() - start) * 1000
        self._record(decision)
        return decision

    # ──────────────────────────── Rule Classifier ────────────────────────────

    def _rule_classify(self, text: str) -> RouteDecision:
        """Fast heuristic classification. Pure synchronous, no I/O."""
        text_lower = text.lower().strip()
        word_count = len(text.split())

        # 1. Greetings / farewell — always Ollama
        if self._is_greeting(text_lower, word_count):
            return RouteDecision("ollama", 0.95, "greeting", "Greeting/farewell detected")

        # 2. Direct tool dispatch — clear intent, skip LLM
        tool_match = self._match_direct_tool(text_lower)
        if tool_match:
            name, args, conf = tool_match
            return RouteDecision("tool_direct", conf, "action", f"Direct tool: {name}",
                                 tool_hint=name, tool_args_hint=args)

        # 3. Explicit Claude request — check early, highest priority
        if self._wants_claude(text_lower):
            return RouteDecision("claude", 0.95, "analysis",
                                 "User explicitly requested Claude")

        # 4. Complex code / architecture — Claude (check before regular coding)
        if self._is_complex_code(text_lower):
            return RouteDecision("claude", 0.85, "coding", "Complex coding/architecture task")

        # 5. Analysis / research — Claude
        if self._is_analysis(text_lower):
            return RouteDecision("claude", 0.80, "analysis", "Analysis/research task")

        # 6. Planning / multi-step — Claude
        if self._is_planning(text_lower):
            return RouteDecision("claude", 0.80, "planning", "Planning/multi-step task")

        # 7. Regular coding requests — Ollama for simple
        if self._is_coding(text_lower):
            return RouteDecision("ollama", 0.70, "coding", "Simple coding task")

        # 8. Simple question — Ollama
        if self._is_simple_question(text_lower, word_count):
            return RouteDecision("ollama", 0.80, "question", "Simple question")

        # 9. Long/complex query heuristic
        if word_count > _COMPLEX_WORD_THRESHOLD:
            return RouteDecision("claude", 0.65, "analysis", f"Long query ({word_count} words)")

        # 10. Default: Ollama (chitchat)
        return RouteDecision("ollama", 0.50, "chitchat", "Default: simple/chitchat")

    # ──────────────────────────── Pattern Matchers ────────────────────────────

    _GREETING_RE = re.compile(
        r"^(hey|hi|hello|good\s+(morning|afternoon|evening)|"
        r"what'?s up|howdy|yo|sup|greetings|jarvis|thanks|thank you|"
        r"goodbye|bye|good\s*night|see you|later)\b", re.IGNORECASE
    )

    _SIMPLE_Q_RE = re.compile(
        r"^(what\s+time|what\s+day|what\s+date|how\s+are\s+you|"
        r"who\s+are\s+you|what\s+is\s+your\s+name|tell\s+me\s+a\s+joke|"
        r"what\s+can\s+you\s+do)\b", re.IGNORECASE
    )

    _CODING_RE = re.compile(
        r"\b(write\s+(a\s+)?(code|script|function|class|program|api)|"
        r"debug\s+(this|my|the)|fix\s+(this|my|the)\s+(code|bug|error)|"
        r"refactor|implement\s+\w+|unit\s+test|"
        r"code\s+review|pull\s+request|regex\s+for|sql\s+query)\b", re.IGNORECASE
    )

    _COMPLEX_CODE_RE = re.compile(
        r"\b(architect|design\s+pattern|system\s+design|microservice|"
        r"distributed|concurren|optimize\s+(the|this|my)|scalab|"
        r"data\s+pipeline|machine\s+learning|neural\s+network)\b", re.IGNORECASE
    )

    _ANALYSIS_RE = re.compile(
        r"\b(explain\s+(how|why|in\s+detail|the\s+difference)|"
        r"analyze|compare\s+(and\s+contrast|these|the)|"
        r"pros?\s+and\s+cons?|trade-?offs?|research\s+\w+|"
        r"in[\s-]depth|comprehensive|thorough(ly)?)\b", re.IGNORECASE
    )

    _PLANNING_RE = re.compile(
        r"\b(plan\s+(for|out|a|the)|create\s+a\s+(roadmap|strategy|plan)|"
        r"step[\s-]+by[\s-]+step|how\s+should\s+i\s+(approach|start|build)|"
        r"help\s+me\s+(plan|organize|structure)|"
        r"project\s+plan|action\s+plan|break(down|\s+it\s+down))\b", re.IGNORECASE
    )

    _CLAUDE_REQUEST_RE = re.compile(
        r"\b(ask\s+claude|use\s+claude|claude[,:]?\s+help|send\s+to\s+claude)\b", re.IGNORECASE
    )

    # Direct tool dispatch — high-confidence patterns with inline arg parsing
    _TOOL_PATTERNS = [
        # Weather with location
        (re.compile(r"\bweather\s+(?:in|for|at)\s+(.+)", re.I),
         "weather.current", lambda m: {"location": m.group(1).strip()}),
        # Weather without location
        (re.compile(r"\b(?:what(?:'s| is) the weather|current weather|how(?:'s| is) the weather)\b", re.I),
         "weather.current", lambda m: {"location": "auto"}),
        # Calendar today
        (re.compile(r"\b(?:what(?:'s| is) on my (?:calendar|schedule)|today(?:'s)?\s+(?:calendar|schedule|events))\b", re.I),
         "calendar.today", lambda m: {}),
        # Add note
        (re.compile(r"\b(?:add|create|make)\s+(?:a\s+)?note[:\s]+(.+)", re.I),
         "notes.add", lambda m: {"content": m.group(1).strip(), "tag": "general"}),
        # List notes
        (re.compile(r"\b(?:list|show|read)\s+(?:my\s+)?notes\b", re.I),
         "notes.list", lambda m: {}),
        # Vision
        (re.compile(r"\b(?:look\s+at\s+(?:this|that)|what\s+(?:do|can)\s+you\s+see|activate\s+(?:the\s+)?camera)\b", re.I),
         "vision.look", lambda m: {"prompt": "Describe what you see."}),
        # Pi status
        (re.compile(r"\b(?:pi\s+status|check\s+(?:the\s+)?pi|raspberry\s*pi\s+health)\b", re.I),
         "pi.system_info", lambda m: {"check": "all"}),
    ]

    def _is_greeting(self, text: str, word_count: int) -> bool:
        return bool(self._GREETING_RE.match(text)) and word_count <= 6

    def _is_simple_question(self, text: str, word_count: int) -> bool:
        if word_count > _SIMPLE_WORD_THRESHOLD:
            return False
        return bool(self._SIMPLE_Q_RE.match(text))

    def _is_coding(self, text: str) -> bool:
        return bool(self._CODING_RE.search(text))

    def _is_complex_code(self, text: str) -> bool:
        return bool(self._COMPLEX_CODE_RE.search(text))

    def _is_analysis(self, text: str) -> bool:
        return bool(self._ANALYSIS_RE.search(text))

    def _is_planning(self, text: str) -> bool:
        return bool(self._PLANNING_RE.search(text))

    def _wants_claude(self, text: str) -> bool:
        return bool(self._CLAUDE_REQUEST_RE.search(text))

    def _match_direct_tool(self, text: str) -> Optional[tuple]:
        """Match text to a direct tool dispatch. Returns (tool_name, args, confidence) or None."""
        for pattern, tool_name, arg_builder in self._TOOL_PATTERNS:
            m = pattern.search(text)
            if m:
                try:
                    args = arg_builder(m)
                    return (tool_name, args, 0.90)
                except Exception:
                    return (tool_name, {}, 0.70)
        return None

    # ──────────────────────────── Budget/Availability Gate ────────────────────────────

    def _gate_claude(self, decision: RouteDecision) -> RouteDecision:
        """Check Claude availability and budget. Downgrade to Ollama if needed."""
        # Check API key at runtime (not import-time) for testability
        from config import ANTHROPIC_API_KEY as api_key
        if not api_key:
            return RouteDecision(
                "ollama", decision.confidence, decision.intent_type,
                "Claude API key not set — falling back to Ollama"
            )

        if self._cost_tracker:
            can_afford, reason = self._cost_tracker.can_afford()
            if not can_afford:
                return RouteDecision(
                    "ollama", decision.confidence, decision.intent_type,
                    f"Claude budget exhausted — {reason}"
                )

        return decision

    # ──────────────────────────── Stats ────────────────────────────

    def _record(self, decision: RouteDecision):
        self._route_count += 1
        self._tier_counts[decision.target] = self._tier_counts.get(decision.target, 0) + 1
        n = self._route_count
        self._avg_ms = (self._avg_ms * (n - 1) + decision.classification_ms) / n

    def get_stats(self) -> dict:
        return {
            "enabled": _ENABLED,
            "total_routes": self._route_count,
            "tier_distribution": dict(self._tier_counts),
            "avg_classification_ms": round(self._avg_ms, 3),
        }
