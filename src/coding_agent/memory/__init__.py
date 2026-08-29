"""task_007: local, controllable, explainable cross-conversation memory.

This package owns the memory domain, lexical indexes, secret policy and the
MemoryService facade. It deliberately does not depend on embeddings, cloud
services or agent frameworks; persistence lives in the same SQLite state.db
schema chain as Conversation/Turn.
"""

from __future__ import annotations
