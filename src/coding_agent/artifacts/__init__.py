"""Content-addressed artifact storage for turn-scoped file snapshots.

Artifacts are stored under ``CODING_AGENT_HOME/artifacts`` and referenced only
by digest. They are never part of conversation lists, SSE or bootstrap; they
are fetched exclusively through the turn change preview API.
"""

from __future__ import annotations
