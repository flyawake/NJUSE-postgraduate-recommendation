"""task_004: persistent multi-turn Conversation domain and storage.

This package owns the durable data plane: Conversation/Turn lifecycle,
canonical append-only history, public event projection, transaction-safe
migrations and runtime registry adapters. It deliberately does not contain
AgentLoop or ToolExecutor logic; those are injected as a loop builder.
"""

from __future__ import annotations
