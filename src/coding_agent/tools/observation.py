"""File observation and SHA-256 freshness tracking.

Only a successful ``read_file`` establishes an observation. Write tools
recompute the fingerprint immediately before mutating and reject unobserved
or stale targets. Successful writes refresh the observation. This is a
best-effort single-process freshness guard, not a cross-process CAS.
"""

from __future__ import annotations

import hashlib
from typing import Dict, Optional, Tuple


class FileObservationTracker:
    def __init__(self) -> None:
        self._versions: Dict[str, str] = {}

    @staticmethod
    def fingerprint(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def record(self, rel: str, data: bytes) -> str:
        fingerprint = self.fingerprint(data)
        self._versions[rel] = fingerprint
        return fingerprint

    def is_observed(self, rel: str) -> bool:
        return rel in self._versions

    def current_matches(self, rel: str, data: bytes) -> bool:
        return self._versions.get(rel) == self.fingerprint(data)

    def check(self, rel: str, data: bytes) -> Tuple[str, Optional[str]]:
        """Return ``("ok"|"not_observed"|"stale", fingerprint)``."""
        fingerprint = self.fingerprint(data)
        if rel not in self._versions:
            return "not_observed", fingerprint
        if self._versions[rel] != fingerprint:
            return "stale", fingerprint
        return "ok", fingerprint
