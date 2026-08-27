"""Local GUI service for task_002.

The web package is the presentation/control adapter over AgentLoop: it owns
the HTTP server, RunController (worker thread + cancellation + bounded event
store) and the whitelisted public DTOs. It never reimplements agent logic.
"""

from __future__ import annotations
