"""Sous orchestration: model rounds, tool execution, grounding check, deterministic extras.

The model picks tools and explains what they return. Scope, numbers, evidence and limitations
are all produced by code. Prompts and tool results are never logged.
"""

import json
import re
import time
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace
from sqlalchemy.ext.asyncio import AsyncSession

from app.context import request_id_var
from app.identity.models import Location
from app.integrations.providers.square.business_date import get_zone
from app.security.sessions import AuthContext
from app.sous import prompt, tools, validator
from app.sous.llm import LLMProvider, Message
from app.sous.schemas import AskIn, Evidence, Kind, Period, SousAnswer, ToolUse
from app.sous.tools import ToolResult

MAX_ROUNDS = 4
MAX_TOOL_CALLS = 6

_tracer = trace.get_tracer("chefos.sous")
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)
_STREAM_NAMES = {
    "orders": "Orders",
    "timecards": "Timecards (labor)",
    "catalog": "Menu catalog",
    "team_members": "Team members",
}


def strip_think(text: str) -> str:
    """Drop qwen-style reasoning blocks (a stray closing tag keeps only what follows it)."""
    text = _THINK.sub("", text)
    return text.rsplit("</think>", 1)[-1].replace("<think>", "").strip()


def plain_text(text: str) -> str:
    """Light markdown clean-up: the contract says plain text."""
    text = text.replace("**", "").replace("__", "").replace("`", "")
    return re.sub(r"(?m)^\s*(?:#{1,6}|>)\s+", "", text).strip()


def split_kind(text: str) -> tuple[Kind, str]:
    for marker, kind in prompt.PREFIXES.items():
        if text.upper().startswith(marker):
            return kind, text[len(marker) :].strip()  # type: ignore[return-value]
    return "answer", text


def _assistant(content: str, calls: list[Any] | None = None) -> Message:
    msg: Message = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = [
            {"function": {"name": c.name, "arguments": c.arguments}} for c in calls
        ]
    return msg


def _tool_message(name: str, data: dict[str, Any]) -> Message:
    return {
        "role": "tool",
        "tool_name": name,
        "content": json.dumps(data, ensure_ascii=False, default=str),
    }


def _fmt_time(ts: datetime, tz: str) -> str:
    return ts.astimezone(get_zone(tz)).strftime("%Y-%m-%d %H:%M %Z")


def limitations(results: list[ToolResult], tz: str) -> list[str]:
    """One line per stale, failing or not connected stream touched, then one per failed tool."""
    seen: dict[str, tuple[str, datetime | None]] = {}
    for r in results:
        if not r.ok:
            continue
        for f in r.freshness:
            if f.stream in r.streams and f.status != "fresh":
                seen[f.stream] = (f.status, f.data_as_of)
            elif f.stream in r.streams:
                seen.pop(f.stream, None)
    lines: list[str] = []
    for stream, (status, as_of) in seen.items():
        label = _STREAM_NAMES.get(stream, stream)
        if status == "not_connected":
            lines.append(f"{label} data is not connected for this location.")
        elif as_of is None:
            lines.append(f"{label} data is {status} and has not synced yet.")
        else:
            lines.append(f"{label} data is {status}; last updated {_fmt_time(as_of, tz)}.")
    lines += [
        f"Tool {r.tool} failed: {r.data.get('error', 'unknown error')}" for r in results if not r.ok
    ]
    return lines


def _period(results: list[ToolResult], tz: str) -> Period | None:
    """The latest period any tool covered (the first one on ties)."""
    ranged = [r.period for r in results if r.ok and r.period]
    if not ranged:
        return None
    first, last = max(ranged, key=lambda p: p[1])
    return Period(from_=first, to=last, timezone=tz)


def _evidence(results: list[ToolResult]) -> list[Evidence]:
    seen: set[tuple[str, str]] = set()
    out: list[Evidence] = []
    for r in results:
        for e in r.evidence:
            if (e.source, e.label) not in seen:
                seen.add((e.source, e.label))
                out.append(e)
    return out


def _evidence_only_line(evidence: list[Evidence]) -> str:
    if evidence:
        return (
            "I couldn't produce a written answer I could verify, so here are the figures I found."
        )
    return "I couldn't produce an answer I could verify. Try rephrasing or narrowing the question."


async def _call_tool(
    name: str,
    args: dict[str, Any],
    db: AsyncSession,
    ctx: AuthContext,
    loc: Location,
    now: datetime,
    model: str,
) -> tuple[ToolResult, ToolUse]:
    with _tracer.start_as_current_span("sous.tool") as span:
        t0 = time.perf_counter()
        result = await tools.run_tool(name, args, db, ctx.org_id, loc, now)
        ms = round((time.perf_counter() - t0) * 1000)
        span.set_attributes(
            {
                "sous.tool": name,
                "sous.prompt_version": prompt.PROMPT_VERSION,
                "sous.model": model,
                "sous.ok": result.ok,
                "sous.ms": ms,
            }
        )
    return result, ToolUse(name=name, args=args, ms=ms, ok=result.ok)


async def ask(
    db: AsyncSession,
    ctx: AuthContext,
    loc: Location,
    req: AskIn,
    provider: LLMProvider,
    now: datetime | None = None,
) -> SousAnswer:
    now = now or datetime.now(UTC)
    with _tracer.start_as_current_span("sous.ask") as span:
        t0 = time.perf_counter()
        answer = await _ask(db, ctx, loc, req, provider, now)
        span.set_attributes(
            {
                "sous.prompt_version": prompt.PROMPT_VERSION,
                "sous.model": provider.model,
                "sous.kind": answer.kind,
                "sous.tool_calls": len(answer.tools_used),
                "sous.ok": answer.kind != "evidence_only",
                "sous.ms": round((time.perf_counter() - t0) * 1000),
            }
        )
        return answer


async def _ask(
    db: AsyncSession,
    ctx: AuthContext,
    loc: Location,
    req: AskIn,
    provider: LLMProvider,
    now: datetime,
) -> SousAnswer:
    messages: list[Message] = [
        {"role": "system", "content": prompt.build_system_prompt(loc, now)},
        *({"role": t.role, "content": t.content} for t in req.history),
        {"role": "user", "content": req.question},
    ]
    specs = tools.tool_specs()
    used: list[ToolUse] = []
    results: list[ToolResult] = []
    retried = False

    def finish(kind: Kind, text: str) -> SousAnswer:
        reported = kind in ("answer", "evidence_only")
        return SousAnswer(
            kind=kind,
            answer=text,
            period=_period(results, loc.timezone) if reported else None,
            evidence=_evidence(results) if reported else [],
            limitations=limitations(results, loc.timezone) if reported else [],
            tools_used=used,
            model=provider.model,
            prompt_version=prompt.PROMPT_VERSION,
            request_id=request_id_var.get(),
        )

    for round_no in range(MAX_ROUNDS):
        reply = await provider.chat(messages, specs)
        if reply.tool_calls:
            if round_no == MAX_ROUNDS - 1:
                break  # no rounds left to use the results
            messages.append(_assistant(reply.content, reply.tool_calls))
            for call in reply.tool_calls:
                if len(used) >= MAX_TOOL_CALLS:
                    limit = {"error": "Tool call limit reached. Answer with the results you have."}
                    messages.append(_tool_message(call.name, limit))
                    continue
                result, use = await _call_tool(
                    call.name, call.arguments, db, ctx, loc, now, provider.model
                )
                results.append(result)
                used.append(use)
                messages.append(_tool_message(call.name, result.data))
            continue

        kind, text = split_kind(plain_text(strip_think(reply.content)))
        bad: list[str] = []
        if kind == "answer" and text:
            bad = validator.ungrounded_numbers(text, (r.data for r in results if r.ok))
        if text and not bad:
            return finish(kind, text)
        if retried:
            break
        retried = True
        messages.append(_assistant(reply.content))
        messages.append(
            {
                "role": "user",
                "content": (
                    "These numbers in your answer do not appear in the tool results: "
                    f"{', '.join(bad)}. Rewrite the answer using only numbers exactly as the "
                    "tool results give them. Call a tool if you need other numbers."
                    if bad
                    else "Your reply was empty. Answer the question."
                ),
            }
        )

    evidence = _evidence(results)
    return finish("evidence_only", _evidence_only_line(evidence))
