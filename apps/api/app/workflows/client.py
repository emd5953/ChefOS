"""Temporal client shared by the API and the worker."""

import asyncio

from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.contrib.pydantic import pydantic_data_converter

from app.config import get_settings

TASK_QUEUE = "chefos-default"

_client: Client | None = None
_lock = asyncio.Lock()


async def get_temporal_client() -> Client:
    """Connect lazily and cache. The tracing interceptor parents workflow spans on the API span."""
    global _client
    async with _lock:
        if _client is None:
            settings = get_settings()
            _client = await Client.connect(
                settings.temporal_address,
                namespace=settings.temporal_namespace,
                interceptors=[TracingInterceptor()],
                data_converter=pydantic_data_converter,
            )
        return _client
