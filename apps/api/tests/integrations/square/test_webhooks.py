import json

import pytest

from app.integrations.providers.square import WebhookParseError, parse_event, verify_signature

KEY = "sbx-signature-key"
URL = "https://example.com/webhooks/square"
BODY = (
    b'{"merchant_id":"MLJUNIPER","type":"order.updated","event_id":"evt-1",'
    b'"data":{"type":"order","id":"O1"}}'
)
# Independent vector: printf '%s' "$URL$BODY" | openssl dgst -sha256 -hmac "$KEY" -binary | base64
SIG = "B9y7YhCibtNbD+v6JxuumQYjQuxuT+JEB8zfKFsAzCs="


def test_known_vector() -> None:
    assert verify_signature(KEY, URL, BODY, SIG)


def test_tamper_detection() -> None:
    assert not verify_signature(KEY, URL, BODY + b" ", SIG)
    assert not verify_signature(KEY, URL + "/", BODY, SIG)
    assert not verify_signature("other-key", URL, BODY, SIG)
    assert not verify_signature(KEY, URL, BODY, SIG[:-2] + "AA")
    assert not verify_signature(KEY, URL, BODY, "")
    assert not verify_signature(KEY, URL, BODY, None)


def test_parse_event() -> None:
    ev = parse_event(BODY)
    assert (ev.event_id, ev.type, ev.merchant_id) == ("evt-1", "order.updated", "MLJUNIPER")
    assert (ev.object_type, ev.object_id) == ("order", "O1")


def test_parse_event_location_and_created_at() -> None:
    body = json.dumps({
        "merchant_id": "M", "type": "order.updated", "event_id": "e", "created_at": "2026-01-01T00:00:00Z",
        "data": {"type": "order", "id": "O", "object": {"order_updated": {"location_id": "L1"}}},
    }).encode()  # fmt: skip
    ev = parse_event(body)
    assert ev.location_id == "L1" and ev.created_at is not None


@pytest.mark.parametrize("body", [b"not json", b"[]", b'{"type":"x"}'])
def test_parse_event_invalid(body: bytes) -> None:
    with pytest.raises(WebhookParseError):
        parse_event(body)
