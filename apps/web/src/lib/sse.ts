// Minimal fetch-based SSE client (EventSource can't POST). Shares the base path,
// credentials and CSRF handling of the api client.

import { ApiError, type ProblemDetails } from "./api";

export type SseMessage = { event: string; data: string };

export type SseOptions = {
  signal?: AbortSignal;
  onEvent: (msg: SseMessage) => void;
  /** CSRF token from the auth session (api.ts keeps its copy private). */
  csrfToken?: string | null;
};

const TERMINAL = new Set(["answer", "error"]);

/** Parse one SSE block (text between blank lines). Returns null for comment-only blocks. */
function parseBlock(block: string): SseMessage | null {
  let event = "message";
  const data: string[] = [];
  let seen = false;
  for (const line of block.split("\n")) {
    if (line === "" || line.startsWith(":")) continue;
    const i = line.indexOf(":");
    const field = i === -1 ? line : line.slice(0, i);
    let value = i === -1 ? "" : line.slice(i + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") {
      event = value;
      seen = true;
    } else if (field === "data") {
      data.push(value);
      seen = true;
    }
  }
  return seen ? { event, data: data.join("\n") } : null;
}

export async function streamSse(url: string, body: unknown, opts: SseOptions): Promise<void> {
  const headers: Record<string, string> = {
    Accept: "text/event-stream, application/problem+json",
    "Content-Type": "application/json",
  };
  if (opts.csrfToken) headers["X-CSRF-Token"] = opts.csrfToken;

  let res: Response;
  try {
    res = await fetch(url, {
      method: "POST",
      headers,
      credentials: "include",
      body: JSON.stringify(body),
      signal: opts.signal,
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError(0, { title: "Network error", code: "unavailable" });
  }

  const isStream = (res.headers.get("content-type") ?? "").includes("text/event-stream");
  if (!res.ok || !isStream || !res.body) {
    let problem: ProblemDetails = {};
    try {
      problem = (await res.json()) as ProblemDetails;
    } catch {
      problem = { title: res.statusText || "Request failed" };
    }
    throw new ApiError(res.ok ? 502 : res.status, problem);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finished = false;

  const flush = (final: boolean) => {
    // Normalise line endings. A trailing \r may be the first half of a \r\n split across
    // chunks, so hold it back until the next chunk (unless the stream is done).
    let tail = "";
    if (!final && buffer.endsWith("\r")) {
      tail = "\r";
      buffer = buffer.slice(0, -1);
    }
    buffer = buffer.replace(/\r\n?/g, "\n");
    const blocks = buffer.split("\n\n");
    buffer = final ? "" : (blocks.pop() ?? "") + tail;
    for (const block of blocks) {
      const msg = parseBlock(block);
      if (!msg) continue;
      if (TERMINAL.has(msg.event)) finished = true;
      opts.onEvent(msg);
    }
  };

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      flush(false);
    }
    buffer += decoder.decode();
    flush(true);
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    if (cause instanceof ApiError) throw cause;
    if (opts.signal?.aborted) throw cause;
    throw new ApiError(0, { title: "Network error", code: "unavailable" });
  }

  if (!finished) {
    throw new ApiError(0, {
      title: "Network error",
      code: "unavailable",
      detail: "The connection closed before Sous finished.",
    });
  }
}
