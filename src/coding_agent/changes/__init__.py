"""Turn-scoped change sets: tool-confirmed edits, artifact snapshots, diffs.

ChangeSet is the UI fact source for file review. It is deliberately separate
from ``RunResult.mutated_paths`` (which remains the completion-verification
input) and from git history.
"""

from __future__ import annotations
