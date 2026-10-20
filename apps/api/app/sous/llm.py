"""LLM providers behind one small interface. Ollama runs locally; FakeProvider replays scripts."""

import copy
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx
import structlog

from app.config import get_settings
from app.errors import ProblemError
from app.sous.schemas import BreakerState

log = structlog.get_logger()

Message = dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMReply:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass(frozen=True)
class ProviderHealth:
    reachable: bool
    model_pulled: bool


class LLMProvider(Protocol):
    name: Literal["ollama", "fake"]
    model: str
    breaker: CircuitBreaker | None

    async def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> LLMReply: ...

    async def health(self) -> ProviderHealth: ...


def sous_unavailable() -> ProblemError:
    return ProblemError(
        503,
        "sous_unavailable",
        "Sous can't reach its language model right now. Try again in a moment.",
        title="Sous unavailable",
    )


class CircuitBreaker:
    """Fail fast while the model is down instead of making every question wait for a timeout.

    closed -> open after `threshold` consecutive failures; open -> half_open after `cooldown`
    seconds, which lets exactly one trial call through; its result closes or reopens the breaker.
    State is per process (see docs/specs/sous.md, v1.1).
    """

    def __init__(
        self,
        threshold: int = 3,
        cooldown: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.threshold = threshold
        self.cooldown = cooldown
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._trial_in_flight = False

    @property
    def state(self) -> BreakerState:
        if self._opened_at is None:
            return "closed"
        if self._clock() - self._opened_at >= self.cooldown:
            return "half_open"
        return "open"

    def allow(self) -> bool:
        """Whether a call may go out now. In half_open, only the first caller gets through."""
        state = self.state
        if state == "closed":
            return True
        if state == "half_open" and not self._trial_in_flight:
            self._trial_in_flight = True
            return True
        return False

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None
        self._trial_in_flight = False

    def record_failure(self) -> None:
        self._failures += 1
        if self._trial_in_flight or self._failures >= self.threshold:
            self._opened_at = self._clock()
        self._trial_in_flight = False

    def release(self) -> None:
        """A call ended with no verdict (cancelled): free the half-open trial slot."""
        self._trial_in_flight = False

    def reset(self) -> None:
        self.record_success()


BREAKER = CircuitBreaker()


class OllamaProvider:
    name: Literal["ollama", "fake"] = "ollama"

    def __init__(
        self, url: str, model: str, timeout: float, breaker: CircuitBreaker | None = None
    ) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.breaker: CircuitBreaker | None = breaker or BREAKER

    async def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> LLMReply:
        breaker = self.breaker or BREAKER
        if not breaker.allow():
            raise sous_unavailable()
        try:
            reply = await self._chat(messages, tools)
        except ProblemError:
            breaker.record_failure()
            raise
        except BaseException:
            # Cancelled (the client went away): no verdict on the model, free the trial slot.
            breaker.release()
            raise
        breaker.record_success()
        return reply

    async def _chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> LLMReply:
        body = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "stream": False,
            "think": False,
            "options": {"temperature": 0},
        }
        try:
            # Connecting fails fast; a slow model gets the full read timeout.
            timeout = httpx.Timeout(self.timeout, connect=3.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(f"{self.url}/api/chat", json=body)
            r.raise_for_status()
            message = r.json()["message"]
            calls = [_tool_call(tc) for tc in message.get("tool_calls") or []]
            return LLMReply(content=message.get("content") or "", tool_calls=calls)
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
            # Never log the prompt or the reply; the error type is enough to debug.
            log.warning("sous_provider_error", provider="ollama", error=type(exc).__name__)
            raise sous_unavailable() from exc

    async def health(self) -> ProviderHealth:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(f"{self.url}/api/tags")
            r.raise_for_status()
            models = r.json().get("models") or []
        except httpx.HTTPError, ValueError, AttributeError:
            return ProviderHealth(reachable=False, model_pulled=False)
        names = {str(m.get(k)) for m in models if isinstance(m, dict) for k in ("name", "model")}
        pulled = self.model in names or (":" not in self.model and f"{self.model}:latest" in names)
        return ProviderHealth(reachable=True, model_pulled=pulled)


def _tool_call(raw: dict[str, Any]) -> ToolCall:
    fn = raw["function"]
    args = fn.get("arguments") or {}
    if isinstance(args, str):  # some models send JSON text instead of an object
        try:
            args = json.loads(args)
        except ValueError:
            args = {}
    return ToolCall(name=str(fn["name"]), arguments=args if isinstance(args, dict) else {})


class FakeProvider:
    """Replays scripted replies in order. `calls` records the messages of each `chat` call."""

    name: Literal["ollama", "fake"] = "fake"
    breaker: CircuitBreaker | None = None

    def __init__(self, replies: list[LLMReply], model: str = "fake-model") -> None:
        self.model = model
        self._replies = list(replies) or [
            LLMReply(content="UNSUPPORTED: The fake provider has no scripted replies.")
        ]
        self.calls: list[list[Message]] = []

    async def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> LLMReply:
        self.calls.append(copy.deepcopy(messages))
        if len(self.calls) > len(self._replies):
            raise AssertionError("FakeProvider ran out of scripted replies")
        return self._replies[len(self.calls) - 1]

    async def health(self) -> ProviderHealth:
        return ProviderHealth(reachable=True, model_pulled=True)


def get_provider() -> LLMProvider:
    s = get_settings()
    if s.sous_provider == "fake":
        return FakeProvider([], model=s.sous_model)
    return OllamaProvider(s.ollama_url, s.sous_model, float(s.sous_timeout_seconds))
