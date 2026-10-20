import httpx
import pytest

from app import health


async def test_healthz(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_readyz_ok(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fine() -> None:
        return None

    monkeypatch.setattr(health, "_check_temporal", fine)
    r = await client.get("/readyz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "checks": {"db": "ok", "temporal": "ok"}}


async def test_readyz_503_when_temporal_down_but_healthz_ok(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def down() -> None:
        raise ConnectionError("temporal down")

    monkeypatch.setattr(health, "_check_temporal", down)
    r = await client.get("/readyz")
    assert r.status_code == 503
    assert r.json()["checks"] == {"db": "ok", "temporal": "error"}
    assert (await client.get("/healthz")).status_code == 200
