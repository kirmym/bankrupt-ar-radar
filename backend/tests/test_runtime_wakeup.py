"""Regression tests for event-driven worker wakeups and telemetry."""
from __future__ import annotations

import asyncio

import pytest

from src.runtime import _enrich_once, _loop, _score_once, worker_status_snapshot


@pytest.mark.asyncio
async def test_wake_event_runs_dependent_worker_before_interval() -> None:
    wake_event = asyncio.Event()
    calls = 0
    second_call = asyncio.Event()

    async def worker() -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            second_call.set()

    task = asyncio.create_task(_loop("wakeup-test", worker, 60, wake_event=wake_event))
    for _ in range(20):
        if calls:
            break
        await asyncio.sleep(0)
    assert calls == 1

    wake_event.set()
    await asyncio.wait_for(second_call.wait(), timeout=1)
    assert worker_status_snapshot()["wakeup-test"]["status"] in {"running", "waiting"}

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_wake_signal_during_worker_is_not_lost() -> None:
    wake_event = asyncio.Event()
    worker_started = asyncio.Event()
    release_worker = asyncio.Event()
    calls = 0

    async def worker() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            worker_started.set()
            await release_worker.wait()

    task = asyncio.create_task(_loop("wakeup-race-test", worker, 60, wake_event=wake_event))
    await asyncio.wait_for(worker_started.wait(), timeout=1)
    wake_event.set()
    release_worker.set()

    for _ in range(20):
        if calls >= 2:
            break
        await asyncio.sleep(0)
    assert calls == 2

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_enrich_failure_releases_score_gate_and_requests_rescore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_enrich() -> None:
        raise RuntimeError("source unavailable")

    monkeypatch.setattr("src.workers.enrich_worker.run_enrich", fail_enrich)
    ready_gate = asyncio.Event()
    score_requested = asyncio.Event()

    with pytest.raises(RuntimeError, match="source unavailable"):
        await _enrich_once(ready_gate, score_requested)

    assert ready_gate.is_set()
    assert score_requested.is_set()


@pytest.mark.asyncio
async def test_score_failure_releases_alert_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail_score() -> int:
        raise RuntimeError("score unavailable")

    monkeypatch.setattr("src.workers.score_worker.run_rescore", fail_score)
    ready_gate = asyncio.Event()
    alert_requested = asyncio.Event()

    with pytest.raises(RuntimeError, match="score unavailable"):
        await _score_once(ready_gate, alert_requested)

    assert ready_gate.is_set()
    assert alert_requested.is_set()
