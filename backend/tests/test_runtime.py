"""Tests for background worker startup ordering."""
from __future__ import annotations

import asyncio

import pytest

from src.runtime import _loop


@pytest.mark.asyncio
async def test_dependent_loop_waits_for_ingest_startup_gate() -> None:
    gate = asyncio.Event()
    started = asyncio.Event()

    async def worker() -> None:
        started.set()

    task = asyncio.create_task(_loop("dependent", worker, 0, gate))
    await asyncio.sleep(0)
    assert started.is_set() is False

    gate.set()
    await asyncio.wait_for(started.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
