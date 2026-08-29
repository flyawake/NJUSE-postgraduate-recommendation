"""task_007 quantitative smoke benchmark for 2,000 mixed-scope memories.

This is intentionally a small, deterministic local benchmark against the same
SQLite backend used in production. It checks the B2 budget (warm p95 <= 50 ms,
cold p95 <= 150 ms) and B3 scope isolation on a 2,000-entry fixture.
"""

from __future__ import annotations

import time

from coding_agent.conversations.store import SQLiteConversationRepository
from coding_agent.memory.service import MemoryService


def _insert_fixture(memory: MemoryService, count_per_scope: int = 500) -> None:
    for index in range(count_per_scope):
        memory.create_confirmed_memory(
            scope_type="workspace",
            scope_key="C:/repo-a",
            kind="fact",
            content=f"repo-a unique fact {index} fastapi python package tooling",
        )
        memory.create_confirmed_memory(
            scope_type="workspace",
            scope_key="C:/repo-b",
            kind="fact",
            content=f"repo-b private fact {index} rust cargo build system",
        )
        memory.create_confirmed_memory(
            scope_type="global",
            scope_key="global",
            kind="preference",
            content=f"global preference {index} prefer concise answers",
        )
        memory.create_confirmed_memory(
            scope_type="conversation",
            scope_key=f"conv-{index % 20}",
            kind="decision",
            content=f"conversation decision {index} choose sqlite",
        )


def test_2000_entries_warm_and_cold_budget(tmp_path):
    db_path = tmp_path / "perf-state.db"
    repo = SQLiteConversationRepository(db_path)
    repo.initialize()
    memory = MemoryService(repo)
    _insert_fixture(memory)

    # Warm-up.
    for _ in range(5):
        memory.search("fastapi", scope_type="workspace", scope_key="C:/repo-a")

    warm_times = []
    for _ in range(30):
        started = time.perf_counter()
        results = memory.search(
            "fastapi", scope_type="workspace", scope_key="C:/repo-a", limit=6
        )
        warm_times.append(time.perf_counter() - started)
        assert all(item["scope_key"] == "C:/repo-a" for item in results)

    warm_times.sort()
    p95_warm = warm_times[int(len(warm_times) * 0.95) - 1]
    assert p95_warm <= 0.05, f"warm p95 too slow: {p95_warm:.4f}s"

    # Cold: repeatedly reopen the same database in a fresh repository/connection.
    repo.close()
    cold_times = []
    for _ in range(20):
        cold_repo = SQLiteConversationRepository(db_path)
        cold_repo.initialize()
        cold_memory = MemoryService(cold_repo)
        started = time.perf_counter()
        cold_results = cold_memory.search(
            "fastapi", scope_type="workspace", scope_key="C:/repo-a", limit=6
        )
        cold_times.append(time.perf_counter() - started)
        assert cold_results
        assert all(item["scope_key"] == "C:/repo-a" for item in cold_results)
        cold_repo.close()
    cold_times.sort()
    p95_cold = cold_times[int(len(cold_times) * 0.95) - 1]
    assert p95_cold <= 0.15, f"cold p95 too slow: {p95_cold:.4f}s"
