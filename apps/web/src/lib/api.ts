// Typed fetch client for the ChefOS API. The browser only talks to the Next.js
// origin: /api/* is rewritten to the API (see next.config.ts).

export type ProblemDetails = {
  type?: string;
  title?: string;
  status?: number;
  code?: string;
  detail?: string;
  request_id?: string;
  errors?: Record<string, string>;
};

export type ApiErrorCode =
  | "validation_error"
  | "unauthenticated"
  | "session_expired"
  | "csrf_failed"
  | "forbidden"
  | "not_found"
  | "conflict"
  | "rate_limited"
  | "unavailable"
  | "internal_error";

export class ApiError extends Error {
  readonly status: number;
  readonly code: ApiErrorCode | string;
  readonly title: string;
  readonly detail: string | undefined;
  readonly errors: Record<string, string>;
  readonly requestId: string | undefined;

  constructor(status: number, problem: ProblemDetails = {}) {
    const title = problem.title ?? "Request failed";
    super(problem.detail ?? title);
    this.name = "ApiError";
    this.status = status;
    this.code = problem.code ?? defaultCode(status);
    this.title = title;
    this.detail = problem.detail;
    this.errors = problem.errors ?? {};
    this.requestId = problem.request_id;
  }
}

function defaultCode(status: number): string {
  if (status === 401) return "unauthenticated";
  if (status === 403) return "forbidden";
  if (status === 404) return "not_found";
  if (status === 409) return "conflict";
  if (status === 422) return "validation_error";
  if (status === 0 || status === 502 || status === 503 || status === 504) return "unavailable";
  return "internal_error";
}

export function isApiError(e: unknown): e is ApiError {
  return e instanceof ApiError;
}

/** Human message safe to show in the UI. */
export function errorMessage(e: unknown): string {
  if (isApiError(e)) {
    if (e.code === "unavailable") return "The ChefOS API is unreachable right now.";
    return e.detail ?? e.title;
  }
  return "Something went wrong.";
}

// CSRF token is held in memory only (mirrors AuthProvider state).
let csrfToken: string | null = null;
export function setCsrfToken(token: string | null) {
  csrfToken = token;
}

const UNSAFE = new Set(["POST", "PATCH", "PUT", "DELETE"]);

export type ApiOptions = {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  query?: Record<string, string | number | boolean | null | undefined>;
  signal?: AbortSignal;
  /** Redirect to /login on 401 when inside /app or /my (default true). */
  redirectOn401?: boolean;
};

function buildUrl(path: string, query?: ApiOptions["query"]): string {
  const url = `/api${path.startsWith("/") ? path : `/${path}`}`;
  if (!query) return url;
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== null) sp.set(k, String(v));
  }
  const qs = sp.toString();
  return qs ? `${url}?${qs}` : url;
}

export async function api<T>(path: string, opts: ApiOptions = {}): Promise<T> {
  const method = opts.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json, application/problem+json" };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (UNSAFE.has(method) && csrfToken) headers["X-CSRF-Token"] = csrfToken;

  let res: Response;
  try {
    res = await fetch(buildUrl(path, opts.query), {
      method,
      headers,
      credentials: "include",
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      signal: opts.signal,
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError(0, { title: "Network error", code: "unavailable" });
  }

  if (res.ok) {
    if (res.status === 204) return undefined as T;
    const text = await res.text();
    return (text ? JSON.parse(text) : undefined) as T;
  }

  let problem: ProblemDetails = {};
  try {
    problem = (await res.json()) as ProblemDetails;
  } catch {
    problem = { title: res.statusText || "Request failed" };
  }
  const err = new ApiError(res.status, problem);

  if (
    res.status === 401 &&
    opts.redirectOn401 !== false &&
    typeof window !== "undefined" &&
    (window.location.pathname.startsWith("/app") || window.location.pathname.startsWith("/my"))
  ) {
    setCsrfToken(null);
    // Deliberate hard navigation: outside React, and it drops all in-memory state.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.href = "/login";
  }
  throw err;
}
