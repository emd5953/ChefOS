"""Per-request context shared by logging, errors, and audit."""

from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
