"""Square webhook signature verification and event parsing."""

import base64
import hashlib
import hmac
import json
from typing import Any

from app.integrations.providers.square.types import WebhookEvent, parse_ts

SIGNATURE_HEADER = "x-square-hmacsha256-signature"


class WebhookParseError(ValueError):
    pass


def compute_signature(signature_key: str, notification_url: str, body: bytes) -> str:
    mac = hmac.new(
        signature_key.encode("utf-8"), notification_url.encode("utf-8") + body, hashlib.sha256
    )
    return base64.b64encode(mac.digest()).decode("ascii")


def verify_signature(
    signature_key: str, notification_url: str, body: bytes, header_value: str | None
) -> bool:
    """Square scheme: base64(HMAC-SHA256(key, url + raw body)), constant-time compare."""
    if not header_value:
        return False
    expected = compute_signature(signature_key, notification_url, body)
    return hmac.compare_digest(expected.encode("ascii"), header_value.strip().encode("utf-8"))


def parse_event(body: bytes) -> WebhookEvent:
    try:
        doc: Any = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise WebhookParseError(f"invalid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise WebhookParseError("webhook body must be a JSON object")
    event_id = doc.get("event_id")
    event_type = doc.get("type")
    merchant_id = doc.get("merchant_id")
    if not (
        isinstance(event_id, str) and isinstance(event_type, str) and isinstance(merchant_id, str)
    ):
        raise WebhookParseError("missing event_id, type or merchant_id")
    data = doc.get("data")
    data = data if isinstance(data, dict) else {}
    object_type = data.get("type") if isinstance(data.get("type"), str) else None
    object_id = data.get("id") if isinstance(data.get("id"), str) else None
    location_id: str | None = None
    obj = data.get("object")
    if isinstance(obj, dict):
        for inner in obj.values():
            if isinstance(inner, dict) and isinstance(inner.get("location_id"), str):
                location_id = inner["location_id"]
                break
    created = doc.get("created_at")
    return WebhookEvent(
        event_id=event_id,
        type=event_type,
        merchant_id=merchant_id,
        object_type=object_type,
        object_id=object_id,
        created_at=parse_ts(created) if isinstance(created, str) else None,
        location_id=location_id,
    )
