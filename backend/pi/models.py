"""
Jarvis Protocol — Pi Worker Data Models
Task/result contract between PC orchestrator and Pi executor.
"""
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class PiTask:
    """A task to execute on the Raspberry Pi worker."""
    task_name: str
    args: dict = field(default_factory=dict)
    task_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timeout: int = 10  # seconds
    idempotency_key: str = ""  # optional: prevents duplicate execution

    def to_json(self) -> dict:
        return {
            "task_id": self.task_id,
            "task_name": self.task_name,
            "args": self.args,
            "timeout": self.timeout,
            "idempotency_key": self.idempotency_key,
        }


@dataclass
class PiResult:
    """Result from a Pi worker task execution."""
    task_id: str
    ok: bool
    stdout: str = ""
    stderr: str = ""
    data: Any = None
    elapsed_ms: float = 0
    error_code: str = ""  # machine-readable: "timeout", "unreachable", "tool_error", "unknown"

    @classmethod
    def from_json(cls, raw: dict) -> "PiResult":
        return cls(
            task_id=raw.get("task_id", ""),
            ok=raw.get("ok", False),
            stdout=raw.get("stdout", ""),
            stderr=raw.get("stderr", ""),
            data=raw.get("data"),
            elapsed_ms=raw.get("elapsed_ms", 0),
            error_code=raw.get("error_code", ""),
        )

    @classmethod
    def error(cls, task_id: str, message: str, code: str = "unknown") -> "PiResult":
        return cls(task_id=task_id, ok=False, stderr=message, error_code=code)
