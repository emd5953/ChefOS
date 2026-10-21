"use client";

import { ArrowUp, Check, RotateCw, Sparkles, Square, Trash2, TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, isApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { useLocationScope } from "@/lib/location-scope";
import { useSousStatus } from "@/lib/queries";
import { streamSse } from "@/lib/sse";
import type {
  SousAnswer,
  SousAskInput,
  SousErrorEvent,
  SousStatusEvent,
  SousToolEvent,
  SousTurn,
} from "@/lib/types";
import { useReducedMotion } from "@/lib/use-reduced-motion";
import { AnswerView } from "./answer";
import { BriefingCard } from "./briefing";

const MAX_CHARS = 500;
const COUNTER_FROM = 400;
const MAX_HISTORY = 10;
const FALLBACK_MODEL = "qwen3:8b";

const STARTERS = [
  "How did last week compare to the week before?",
  "What was labor % yesterday?",
  "What were our top 5 items this month?",
  "How are we doing today so far?",
  "Which channel grew the most vs last year this month?",
  "Is our data up to date?",
];

type Message =
  | { id: number; role: "user"; text: string }
  | { id: number; role: "assistant"; state: "pending"; status: string; steps: SousToolEvent[] }
  | { id: number; role: "assistant"; state: "done"; answer: SousAnswer }
  | { id: number; role: "assistant"; state: "error"; error: unknown; question: string };

type ErrorInfo = { text: string; unavailable: boolean; retry: boolean };

function describeError(e: unknown): ErrorInfo {
  if (isApiError(e)) {
    if (e.status === 429 || e.code === "rate_limited")
      return { text: "You’re asking quickly — try again in a minute.", unavailable: false, retry: true };
    if (e.status === 503 && e.code === "sous_unavailable")
      return { text: "", unavailable: true, retry: true };
    if (e.status === 403)
      return { text: "You don’t have access to this location.", unavailable: false, retry: false };
  }
  return { text: "Something went wrong while answering. Please try again.", unavailable: false, retry: true };
}

export function Sous() {
  const { current, isLoading } = useLocationScope();
  return (
    <>
      <PageHeader
        overline={current ? `Sous · ${current.name}` : "Sous"}
        title="Sous"
        description={
          current
            ? `Today’s briefing for ${current.name}, and answers about sales, labor and menu items.`
            : "Today’s briefing, and answers about sales, labor and menu items."
        }
      />
      {current ? (
        // Keyed so the briefing and conversation reset when the location changes.
        <>
          <BriefingCard key={`briefing-${current.id}`} locationId={current.id} />
          <Chat key={current.id} locationId={current.id} />
        </>
      ) : isLoading ? (
        <Skeleton className="h-64 w-full rounded-card" />
      ) : (
        <p className="text-[13px] text-foreground/55">Add a location to start asking questions.</p>
      )}
    </>
  );
}

function OllamaNotice({ model }: { model: string }) {
  return (
    <div role="status" className="mb-4 rounded-xl border border-border bg-muted px-4 py-3 text-[13px] leading-relaxed text-foreground/65">
      <p className="font-semibold text-foreground">The assistant isn’t running</p>
      <p className="mt-1">
        Sous runs locally with Ollama, so your data stays on this machine. To enable it, run:
      </p>
      <code className="mt-2 block overflow-x-auto rounded-lg bg-background px-3 py-2 font-mono text-[12px] text-foreground">
        brew install ollama &amp;&amp; ollama pull {model}
      </code>
    </div>
  );
}

function BreakerNotice() {
  return (
    <div role="status" className="mb-4 rounded-xl border border-border bg-muted px-4 py-3 text-[13px] leading-relaxed text-foreground/65">
      <p className="font-semibold text-foreground">The assistant isn’t responding</p>
      <p className="mt-1">
        The assistant model isn’t responding. Simple questions like “sales yesterday” still work.
      </p>
    </div>
  );
}

function parseEvent<T>(data: string): T | null {
  try {
    return JSON.parse(data) as T;
  } catch {
    return null;
  }
}

function Chat({ locationId }: { locationId: string }) {
  const status = useSousStatus();
  const { csrfToken } = useAuth();
  const reduced = useReducedMotion();
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [announce, setAnnounce] = useState("");
  const nextId = useRef(1);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const model = status.data?.model ?? FALLBACK_MODEL;
  const checking = status.isPending;
  const breakerOpen = status.data?.breaker === "open";
  const unavailable = !checking && (status.isError || !status.data?.available);
  const pending = messages.some((m) => m.role === "assistant" && m.state === "pending");
  // Stay enabled while unavailable: the deterministic fallback may still answer.
  const disabled = checking;

  // Unmount (including a location change, which re-keys Chat) aborts the in-flight stream.
  useEffect(() => () => abortRef.current?.abort(), []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "end" });
  }, [messages, reduced]);

  const run = useCallback(
    (question: string, prior: Message[]) => {
      // `prior` ends with the question being asked; history is everything before it.
      const history: SousTurn[] = prior
        .slice(0, -1)
        .flatMap((m): SousTurn[] =>
          m.role === "user"
            ? [{ role: "user", content: m.text }]
            : m.state === "done"
              ? [{ role: "assistant", content: m.answer.answer }]
              : [],
        )
        .slice(-MAX_HISTORY);
      const id = nextId.current++;
      setMessages([...prior, { id, role: "assistant", state: "pending", status: "Thinking", steps: [] }]);
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      const fail = (error: unknown) => {
        setMessages((ms) =>
          ms.map((m) => (m.id === id ? { id, role: "assistant", state: "error", error, question } : m)),
        );
        if (isApiError(error) && error.code === "sous_unavailable") void status.refetch();
      };
      const input: SousAskInput = { question, location_id: locationId, history };
      streamSse("/api/v1/sous/ask/stream", input, {
        signal: controller.signal,
        csrfToken,
        onEvent: ({ event, data }) => {
          if (controller.signal.aborted) return;
          if (event === "status") {
            const e = parseEvent<SousStatusEvent>(data);
            if (!e) return;
            setMessages((ms) =>
              ms.map((m) => (m.id === id && m.role === "assistant" && m.state === "pending" ? { ...m, status: e.label } : m)),
            );
          } else if (event === "tool") {
            const e = parseEvent<SousToolEvent>(data);
            if (!e) return;
            setMessages((ms) =>
              ms.map((m) =>
                m.id === id && m.role === "assistant" && m.state === "pending" ? { ...m, steps: [...m.steps, e] } : m,
              ),
            );
          } else if (event === "answer") {
            const answer = parseEvent<SousAnswer>(data);
            if (!answer) return fail(new Error("Malformed answer"));
            setMessages((ms) => ms.map((m) => (m.id === id ? { id, role: "assistant", state: "done", answer } : m)));
            setAnnounce(answer.answer);
          } else if (event === "error") {
            const e = parseEvent<SousErrorEvent>(data);
            fail(new ApiError(e?.status ?? 500, { code: e?.code, detail: e?.detail }));
          }
        },
      })
        .catch((error: unknown) => {
          // Aborted by Stop, clear, or unmount: not an error.
          if (controller.signal.aborted) return;
          fail(error);
        })
        .finally(() => {
          if (abortRef.current === controller) abortRef.current = null;
        });
    },
    [csrfToken, locationId, status],
  );

  const send = useCallback(
    (raw: string) => {
      const question = raw.trim();
      if (!question || question.length > MAX_CHARS || pending || disabled) return;
      const user: Message = { id: nextId.current++, role: "user", text: question };
      setDraft("");
      run(question, [...messages, user]);
    },
    [messages, pending, disabled, run],
  );

  const retry = useCallback(
    (id: number, question: string) => {
      // Drop the failed row; the question stays in the thread as the last user turn.
      const idx = messages.findIndex((m) => m.id === id);
      run(question, messages.slice(0, idx));
    },
    [messages, run],
  );

  const stop = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setMessages((ms) => ms.filter((m) => !(m.role === "assistant" && m.state === "pending")));
    inputRef.current?.focus();
  };

  const clear = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setMessages([]);
    setAnnounce("");
    inputRef.current?.focus();
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    send(draft);
  };
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send(draft);
    }
  };

  const over = draft.length > MAX_CHARS;
  const canSend = !disabled && !pending && draft.trim().length > 0 && !over;

  return (
    <div className="mx-auto w-full max-w-3xl">
      {breakerOpen ? <BreakerNotice /> : unavailable && <OllamaNotice model={model} />}

      <Card className="flex flex-col">
        <div className="min-h-[320px] space-y-5 p-4 sm:p-6">
          {messages.length === 0 ? (
            <div className="py-6 text-center">
              <div className="mx-auto mb-4 grid size-12 place-items-center rounded-2xl bg-muted text-foreground/60">
                <Sparkles className="size-5" aria-hidden />
              </div>
              <h2 className="text-[15px] font-semibold tracking-[-0.025em]">What would you like to know?</h2>
              <p className="mx-auto mt-1.5 max-w-sm text-[13px] leading-relaxed text-foreground/55">
                Try one of these. Every answer comes with the numbers behind it.
              </p>
              <div className="mt-6 flex flex-wrap justify-center gap-2">
                {STARTERS.map((s) => (
                  <button
                    key={s}
                    type="button"
                    disabled={disabled}
                    onClick={() => send(s)}
                    className="rounded-full border border-border bg-background px-3.5 py-2 text-left text-[13px] font-medium text-foreground/75 transition-[background-color,border-color,transform] duration-150 hover:border-foreground/25 hover:bg-muted active:scale-[0.98] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:pointer-events-none disabled:opacity-45"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <ol className="space-y-5" aria-label="Conversation">
              {messages.map((m) => (
                <li key={m.id} className={cn(!reduced && "animate-fade-up")}>
                  {m.role === "user" ? (
                    <div className="flex justify-end">
                      <p className="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl rounded-br-md bg-foreground px-4 py-2.5 text-[14px] leading-relaxed text-background">
                        <span className="sr-only">You: </span>
                        {m.text}
                      </p>
                    </div>
                  ) : (
                    <div className="max-w-[95%]">
                      <p className="eyebrow mb-1.5 text-[10px]">Sous</p>
                      {m.state === "pending" ? (
                        <Thinking status={m.status} steps={m.steps} onStop={stop} />
                      ) : m.state === "done" ? (
                        <AnswerView answer={m.answer} />
                      ) : (
                        <ErrorRow error={m.error} model={model} onRetry={() => retry(m.id, m.question)} />
                      )}
                    </div>
                  )}
                </li>
              ))}
            </ol>
          )}
          <div ref={endRef} />
        </div>

        <div className="sr-only" aria-live="polite" aria-atomic="true">
          {announce}
        </div>

        <form onSubmit={onSubmit} className="border-t border-border p-3 sm:p-4">
          <label htmlFor="sous-input" className="sr-only">
            Ask Sous a question
          </label>
          <div className="flex items-end gap-2">
            <textarea
              id="sous-input"
              ref={inputRef}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={onKeyDown}
              disabled={disabled}
              rows={2}
              aria-invalid={over || undefined}
              aria-describedby="sous-hint"
              placeholder="Ask a question…"
              className="min-h-11 w-full resize-none rounded-xl border border-input bg-background px-3.5 py-2.5 text-sm text-foreground placeholder:text-foreground/35 transition-[border-color,box-shadow] duration-150 hover:border-foreground/40 focus-visible:border-accent focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-accent/15 disabled:opacity-50 aria-[invalid=true]:border-destructive"
            />
            <Button type="submit" size="icon" disabled={!canSend} aria-label="Send question" className="shrink-0">
              <ArrowUp aria-hidden />
            </Button>
          </div>
          <div className="mt-2 flex items-center justify-between gap-3 text-[11px] text-foreground/45">
            <p id="sous-hint">Enter to send, Shift+Enter for a new line.</p>
            <div className="flex items-center gap-3">
              {draft.length >= COUNTER_FROM && (
                <span className={cn("tabular-nums", over && "font-semibold text-destructive")}>
                  {draft.length}/{MAX_CHARS}
                </span>
              )}
              {messages.length > 0 && (
                <button
                  type="button"
                  onClick={clear}
                  className="inline-flex items-center gap-1 rounded font-medium hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                >
                  <Trash2 className="size-3" aria-hidden /> Clear conversation
                </button>
              )}
            </div>
          </div>
        </form>
      </Card>
    </div>
  );
}

function Thinking({ status, steps, onStop }: { status: string; steps: SousToolEvent[]; onStop: () => void }) {
  const [secs, setSecs] = useState(0);
  useEffect(() => {
    const start = Date.now();
    const id = window.setInterval(() => setSecs(Math.floor((Date.now() - start) / 1000)), 1000);
    return () => window.clearInterval(id);
  }, []);
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3">
        <p role="status" className="flex items-center gap-2 text-[13px] text-foreground/55">
          <span aria-hidden className="size-1.5 animate-pulse rounded-full bg-accent motion-reduce:animate-none" />
          {status}… {secs}s
        </p>
        <Button variant="outline" size="sm" onClick={onStop}>
          <Square aria-hidden /> Stop
        </Button>
      </div>
      {steps.length > 0 && (
        <ul aria-label="Steps so far" className="space-y-1 text-[12px] text-foreground/55">
          {steps.map((t, i) => (
            <li key={i} className="flex items-center gap-1.5">
              {t.ok ? (
                <Check className="size-3 shrink-0 text-foreground/45" aria-label="Done" />
              ) : (
                <TriangleAlert className="size-3 shrink-0 text-warning" aria-label="Problem" />
              )}
              <span className="min-w-0 break-words">{t.label}</span>
              <span className="font-mono text-[11px] text-foreground/40">{t.ms} ms</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ErrorRow({ error, model, onRetry }: { error: unknown; model: string; onRetry: () => void }) {
  const info = describeError(error);
  return (
    <div role="alert" className="space-y-2">
      {info.unavailable ? <OllamaNotice model={model} /> : <p className="text-[13px] text-destructive">{info.text}</p>}
      {info.retry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RotateCw aria-hidden /> Try again
        </Button>
      )}
    </div>
  );
}
