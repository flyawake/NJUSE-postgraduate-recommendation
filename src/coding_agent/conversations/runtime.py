"""Process-local runtime registry and workspace lease enforcement.

Persistent conversation state lives in SQLite; this class only tracks active
workers, cancellation and bounded concurrency. A restart never replays turns
from here: the repository marks active turns as interrupted on startup.
"""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Callable, Dict, Optional, Tuple

DEFAULT_MAX_WORKERS = 2


class RuntimeRegistryError(Exception):
    def __init__(self, code: str, message: str, *, owner_id: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.owner_id = owner_id


class WorkspaceLeaseManager:
    """First-writer-wins canonical workspace leases.

    The key is the server-resolved canonical path string, not the user input.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._leases: Dict[str, str] = {}

    @staticmethod
    def _key(workspace_key: str) -> str:
        """Accept either a canonical key or the equivalent local path spelling."""
        return os.path.normcase(os.path.abspath(workspace_key))

    def acquire(self, workspace_key: str, conversation_id: str) -> bool:
        workspace_key = self._key(workspace_key)
        with self._lock:
            if workspace_key in self._leases:
                return False
            self._leases[workspace_key] = conversation_id
            return True

    def release(self, workspace_key: str, conversation_id: str) -> None:
        workspace_key = self._key(workspace_key)
        with self._lock:
            if self._leases.get(workspace_key) == conversation_id:
                del self._leases[workspace_key]

    def owner(self, workspace_key: str) -> Optional[str]:
        workspace_key = self._key(workspace_key)
        with self._lock:
            return self._leases.get(workspace_key)

    def count(self) -> int:
        with self._lock:
            return len(self._leases)


class RuntimeRegistry:
    def __init__(self, *, max_workers: int = DEFAULT_MAX_WORKERS) -> None:
        self._lock = threading.RLock()
        self._max_workers = max_workers
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="coding-agent-turn"
        )
        self._leases = WorkspaceLeaseManager()
        self._runtimes: Dict[str, Dict[str, object]] = {}
        self._max_workers_config = max_workers

    @property
    def capabilities(self) -> Dict[str, int]:
        return {
            "max_concurrent_turns": self._max_workers_config,
            "max_workers": self._max_workers_config,
        }

    def submit(
        self,
        conversation_id: str,
        workspace_key: str,
        *,
        turn_id: str,
        run_id: str,
        target: Callable[[], None],
        cancel_event: Optional[threading.Event] = None,
        on_finish: Optional[Callable[[], None]] = None,
    ) -> Tuple[str, Optional[str]]:
        """Start one worker under a workspace lease.

        Returns ``(owner_conversation_id or None, error_code or None)`` where
        a non-None owner means ``workspace_busy``.
        """
        with self._lock:
            if conversation_id in self._runtimes:
                raise RuntimeRegistryError(
                    "conversation_busy",
                    "该会话已有正在运行的 turn",
                    owner_id=conversation_id,
                )
            if not self._leases.acquire(workspace_key, conversation_id):
                owner = self._leases.owner(workspace_key)
                raise RuntimeRegistryError(
                    "workspace_busy",
                    "同一工作区已有其他会话在运行",
                    owner_id=owner,
                )
            cancel_event = cancel_event or threading.Event()
            future = self._executor.submit(
                self._run_wrapper,
                conversation_id,
                workspace_key,
                turn_id,
                run_id,
                cancel_event,
                target,
                on_finish,
            )
            self._runtimes[conversation_id] = {
                "turn_id": turn_id,
                "run_id": run_id,
                "workspace_key": workspace_key,
                "cancel_event": cancel_event,
                "future": future,
            }
            return conversation_id, None

    def cancel(self, conversation_id: str) -> bool:
        with self._lock:
            runtime = self._runtimes.get(conversation_id)
            if runtime is None:
                return False
            event = runtime.get("cancel_event")
            if isinstance(event, threading.Event):
                event.set()
            return True

    def is_active(self, conversation_id: str) -> bool:
        with self._lock:
            return conversation_id in self._runtimes

    def active_count(self) -> int:
        with self._lock:
            return len(self._runtimes)

    def workspace_owner(self, workspace_key: str) -> Optional[str]:
        return self._leases.owner(workspace_key)

    def shutdown(self, timeout: float = 5.0) -> None:
        with self._lock:
            runtimes = list(self._runtimes.values())
            for runtime in runtimes:
                event = runtime.get("cancel_event")
                if isinstance(event, threading.Event):
                    event.set()
            futures = [
                runtime.get("future")
                for runtime in self._runtimes.values()
                if isinstance(runtime.get("future"), Future)
            ]
        deadline = time.monotonic() + max(0.0, timeout)
        for future in futures:
            try:
                future.result(timeout=max(0.0, deadline - time.monotonic()))
            except FutureTimeoutError:
                break
            except Exception:
                # The persistent service owns terminal recovery. Shutdown
                # must continue even if a worker already failed internally.
                continue
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _run_wrapper(
        self,
        conversation_id: str,
        workspace_key: str,
        turn_id: str,
        run_id: str,
        cancel_event: threading.Event,
        target: Callable[[], None],
        on_finish: Optional[Callable[[], None]] = None,
    ) -> None:
        try:
            target()
        finally:
            with self._lock:
                self._runtimes.pop(conversation_id, None)
            self._leases.release(workspace_key, conversation_id)
            if on_finish is not None:
                on_finish()
