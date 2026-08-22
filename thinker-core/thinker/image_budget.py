"""Concurrency-safe reservation ledger for a bounded image run."""

from __future__ import annotations

import threading


class BudgetLedger:
    def __init__(self, limit_usd: float):
        if limit_usd < 0:
            raise ValueError("budget limit cannot be negative")
        self.limit_usd = limit_usd
        self._reserved: dict[str, float] = {}
        self._committed = 0.0
        self._lock = threading.Lock()

    def reserve(self, request_id: str, estimated_max_usd: float) -> bool:
        if estimated_max_usd < 0:
            raise ValueError("reservation cannot be negative")
        with self._lock:
            if request_id in self._reserved:
                return self._reserved[request_id] == estimated_max_usd
            if (
                self._committed + sum(self._reserved.values()) + estimated_max_usd
                > self.limit_usd + 1e-12
            ):
                return False
            self._reserved[request_id] = estimated_max_usd
            return True

    def commit(self, request_id: str, actual_usd: float | None = None) -> None:
        with self._lock:
            reserved = self._reserved.pop(request_id, 0.0)
            self._committed += reserved if actual_usd is None else actual_usd

    def release(self, request_id: str) -> None:
        with self._lock:
            self._reserved.pop(request_id, None)

    @property
    def committed_usd(self) -> float:
        with self._lock:
            return self._committed

    @property
    def remaining_usd(self) -> float:
        with self._lock:
            return self.limit_usd - self._committed - sum(self._reserved.values())
