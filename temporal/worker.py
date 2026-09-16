from __future__ import annotations


class TemporalWorker:
    """Production worker facade."""

    async def run(self) -> None:
        from temporal.sdk_worker import run_worker
        await run_worker()
