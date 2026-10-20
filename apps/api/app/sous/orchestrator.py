"""Sous orchestration: model rounds, tool execution, grounding check, deterministic extras.

The model picks tools and explains what they return. Scope, numbers, evidence and limitations
are all produced by code. Prompts and tool results are never logged.
"""

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, date, datetime
from typing import Any

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import set_span_in_context
from sqlalchemy.ext.asyncio import AsyncSession

from app.context import request_id_var
from app.db import scoped_session
from app.errors import ProblemError
from app.identity.models import Location
from app.integrations.providers.square.business_date import business_date, get_zone
from app.security.sessions import AuthContext
from app.sous import fallback, prompt, tools, validator
from app.sous.llm import LLMProvider, LLMReply, Message
from app.sous.schemas import (
    AskIn,
    Evidence,
    Heartbeat,
    Kind,
    Period,
    SousAnswer,
    SousEvent,
    StatusEvent,
    ToolEvent,
    ToolUse,
)
from app.sous.tools import ToolResult

MAX_ROUNDS = 4
MAX_TOOL_CALLS = 6
HEARTBEAT_SECONDS = 10.0

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

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
    """One line per stale, failing or not connected stream touched, one per failed tool, and one
    if item names were hidden."""
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
    hidden = sum(r.hidden_names for r in results if r.ok)
    if hidden:
        lines.append(
            f"{hidden} item name(s) looked like instructions to an AI and were hidden. "
            "Check these items in Square."
        )
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


def tool_label(name: str, args: dict[str, Any]) -> str:
    """What a tool step did, for the stream ("Checked sales for Oct 12 to Oct 18")."""
    span = ""
    try:
        first, last = date.fromisoformat(args["from"]), date.fromisoformat(args["to"])
        span = (
            f" for {first:%b} {first.day}"
            if first == last
            else f" for {first:%b} {first.day} \u2013 {last:%b} {last.day}"
        )
    except KeyError, TypeError, ValueError:
        pass
    compare = {"previous_period": " vs the period before", "previous_year": " vs last year"}
    return {
        "get_sales_summary": f"Checked sales{span}{compare.get(str(args.get('compare')), '')}",
        "get_daily_sales": f"Checked daily sales{span}",
        "get_top_items": f"Checked top items{span}",
        "get_today": "Checked today's sales so far",
        "get_data_freshness": "Checked how current the data is",
    }.get(name, f"Ran {name}")


async def _call_tool(
    name: str,
    args: dict[str, Any],
    open_session: SessionFactory,
    ctx: AuthContext,
    loc: Location,
    now: datetime,
    model: str,
    parent: Context,
) -> tuple[ToolResult, ToolEvent]:
    with _tracer.start_as_current_span("sous.tool", context=parent) as span:
        t0 = time.perf_counter()
        # A session per tool call: no connection is held while the model is thinking.
        async with open_session() as db:
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
    event = ToolEvent(name=name, args=args, ms=ms, ok=result.ok, label=tool_label(name, args))
    return result, event


async def _chat(
    provider: LLMProvider, messages: list[Message], specs: list[dict[str, Any]]
) -> AsyncIterator[Heartbeat | LLMReply]:
    """One model call, yielding a heartbeat every HEARTBEAT_SECONDS until the reply arrives."""
    task = asyncio.ensure_future(provider.chat(messages, specs))
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=HEARTBEAT_SECONDS)
            if done:
                yield task.result()
                return
            yield Heartbeat()
    finally:
        if not task.done():
            task.cancel()  # the client went away


async def events(
    ctx: AuthContext,
    loc: Location,
    req: AskIn,
    provider: LLMProvider,
    now: datetime | None = None,
    *,
    open_session: SessionFactory | None = None,
) -> AsyncIterator[SousEvent]:
    """Progress events for one question, ending with the SousAnswer (or raising ProblemError)."""
    now = now or datetime.now(UTC)
    sessions = open_session or (lambda: scoped_session(ctx.org_id))
    # Not a current span: an async generator can't keep a context attached across yields.
    span = _tracer.start_span("sous.ask")
    t0 = time.perf_counter()
    answer: SousAnswer | None = None
    try:
        async for event in _run(ctx, loc, req, provider, now, sessions, set_span_in_context(span)):
            if isinstance(event, SousAnswer):
                answer = event
            yield event
    finally:
        span.set_attributes(
            {
                "sous.prompt_version": prompt.PROMPT_VERSION,
                "sous.model": provider.model,
                "sous.kind": answer.kind if answer else "error",
                "sous.mode": answer.mode if answer else "none",
                "sous.tool_calls": len(answer.tools_used) if answer else 0,
                "sous.ok": answer is not None and answer.kind != "evidence_only",
                "sous.ms": round((time.perf_counter() - t0) * 1000),
            }
        )
        span.end()


async def ask(
    ctx: AuthContext,
    loc: Location,
    req: AskIn,
    provider: LLMProvider,
    now: datetime | None = None,
    *,
    open_session: SessionFactory | None = None,
) -> SousAnswer:
    """The non-streaming form: drain events() and return the answer."""
    async for event in events(ctx, loc, req, provider, now, open_session=open_session):
        if isinstance(event, SousAnswer):
            return event
    raise RuntimeError("Sous finished without an answer")


async def _run(
    ctx: AuthContext,
    loc: Location,
    req: AskIn,
    provider: LLMProvider,
    now: datetime,
    sessions: SessionFactory,
    parent: Context,
) -> AsyncIterator[SousEvent]:
    try:
        async for event in _model_run(ctx, loc, req, provider, now, sessions, parent):
            yield event
    except ProblemError as exc:
        if exc.code != "sous_unavailable":
            raise
        today = business_date(now, loc.timezone, loc.business_day_start)
        plan = fallback.match(req.question, today)
        if plan is None:
            raise
        yield StatusEvent(label="The assistant model isn't responding, so using a quick answer")
        result, event = await _call_tool(
            plan.tool, plan.args, sessions, ctx, loc, now, "fallback", parent
        )
        yield event
        if not result.ok:
            raise
        yield SousAnswer(
            kind="answer",
            answer=fallback.render(plan, result.data),
            period=_period([result], loc.timezone),
            evidence=_evidence([result]),
            limitations=[fallback.LIMITATION, *limitations([result], loc.timezone)],
            tools_used=[ToolUse(**event.model_dump(exclude={"label"}))],
            model="fallback",
            prompt_version=prompt.PROMPT_VERSION,
            request_id=request_id_var.get(),
            mode="fallback",
        )


async def _model_run(
    ctx: AuthContext,
    loc: Location,
    req: AskIn,
    provider: LLMProvider,
    now: datetime,
    sessions: SessionFactory,
    parent: Context,
) -> AsyncIterator[SousEvent]:
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

    yield StatusEvent(label="Thinking")
    for round_no in range(MAX_ROUNDS):
        reply: LLMReply | None = None
        async for item in _chat(provider, messages, specs):
            if isinstance(item, Heartbeat):
                yield item
            else:
                reply = item
        assert reply is not None
        if reply.tool_calls:
            if round_no == MAX_ROUNDS - 1:
                break  # no rounds left to use the results
            messages.append(_assistant(reply.content, reply.tool_calls))
            for call in reply.tool_calls:
                if len(used) >= MAX_TOOL_CALLS:
                    limit = {"error": "Tool call limit reached. Answer with the results you have."}
                    messages.append(_tool_message(call.name, limit))
                    continue
                result, event = await _call_tool(
                    call.name, call.arguments, sessions, ctx, loc, now, provider.model, parent
                )
                results.append(result)
                used.append(ToolUse(**event.model_dump(exclude={"label"})))
                messages.append(_tool_message(call.name, result.data))
                yield event
            yield StatusEvent(label="Reading the results")
            continue

        kind, text = split_kind(plain_text(strip_think(reply.content)))
        bad: list[str] = []
        if kind == "answer" and text:
            bad = validator.ungrounded_numbers(text, (r.data for r in results if r.ok))
        if text and not bad:
            yield finish(kind, text)
            return
        if retried:
            break
        retried = True
        yield StatusEvent(label="Double-checking the numbers")
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
    yield finish("evidence_only", _evidence_only_line(evidence))
