"""Tool-confirmed change collector for write/edit calls.

The collector is a neutral observer inserted into ToolExecutor. It captures
the first before state and the last after state for every workspace-relative
path touched by a successful WRITE tool, then finalizes a net change set.
Failure outcomes never update the after state, and a file that was modified
then restored to its original bytes is removed from the net summary.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..artifacts.store import ArtifactStore, ArtifactTooLargeError
from ..conversations.domain import (
    ChangeCoverage,
    ChangeSource,
    ChangeType,
)
from ..conversations.store import SQLiteConversationRepository
from ..tools.base import PreparedCall, ToolEffect, ToolOutcome
from ..tools.paths import normalize_rel, resolve_inside
from .diff import MAX_FILE_BYTES, build_diff

MAX_TURN_ARTIFACT_BYTES = 20 * 1024 * 1024
MAX_PROBE_FILES = 2_000
MAX_PROBE_BYTES = 20 * 1024 * 1024
MAX_PROBE_SECONDS = 0.75
_PROBE_IGNORED_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
}


@dataclass(frozen=True)
class _Snapshot:
    exists: bool
    data: Optional[bytes]
    digest: Optional[str]
    status: str = "available"


@dataclass
class _FileState:
    before: _Snapshot
    after: _Snapshot
    source: str = ChangeSource.TOOL_CONFIRMED.value
    old_relative_path: Optional[str] = None

    @property
    def net_type(self) -> Optional[str]:
        if self.old_relative_path is not None:
            return ChangeType.RENAMED.value
        if not self.before.exists and not self.after.exists:
            return None
        if not self.before.exists and self.after.exists:
            return ChangeType.CREATED.value
        if self.before.exists and not self.after.exists:
            return ChangeType.DELETED.value
        if self.before.digest is None or self.after.digest is None:
            # A successful WRITE occurred but capture could not prove net
            # equality. Report the confirmed path with incomplete preview
            # instead of silently dropping a potentially changed file.
            return ChangeType.MODIFIED.value
        if self.before.digest != self.after.digest:
            return ChangeType.MODIFIED.value
        return None


class ToolChangeCollector:
    def __init__(self, workspace_root: Path, artifact_store: ArtifactStore) -> None:
        self._root = workspace_root.resolve()
        self._artifacts = artifact_store
        self._files: Dict[str, _FileState] = {}
        self._command_before: Optional[Dict[str, _Snapshot]] = None
        self._command_head: Optional[str] = None
        self._coverage = ChangeCoverage.COMPLETE.value

    def before_execute(self, prepared: PreparedCall) -> None:
        if prepared.tool_name == "run_command" and prepared.error is None:
            self._command_before, complete = self._scan_workspace()
            self._command_head = self._read_git_head_token()
            if not complete:
                self._coverage = ChangeCoverage.INCOMPLETE.value
            return
        if prepared.spec is None or prepared.spec.effect is not ToolEffect.WRITE:
            return
        if prepared.error is not None:
            return
        rel = self._extract_rel(prepared)
        if rel is None:
            return
        if rel in self._files:
            return  # keep the first before snapshot for this turn
        snapshot = self._capture(rel)
        state = _FileState(
            before=snapshot,
            after=snapshot,
        )
        self._files[rel] = state

    def after_execute(self, prepared: PreparedCall, outcome: ToolOutcome) -> None:
        if prepared.tool_name == "run_command":
            self._capture_command_changes(outcome)
            return
        if prepared.spec is None or prepared.spec.effect is not ToolEffect.WRITE:
            return
        if not outcome.ok:
            return
        rel = self._extract_rel(prepared)
        if rel is None:
            return
        state = self._files.setdefault(
            rel,
            _FileState(
                before=_Snapshot(False, None, None),
                after=_Snapshot(False, None, None),
            ),
        )
        state.after = self._capture(rel)

    def finalize(
        self,
        repository: SQLiteConversationRepository,
        *,
        conversation_id: str,
        turn_id: str,
        status: str = "final",
        coverage: Optional[str] = None,
    ) -> Dict[str, Any]:
        files: List[Dict[str, Any]] = []
        additions = 0
        deletions = 0
        artifact_bytes = 0
        for rel, state in sorted(self._files.items()):
            change_type = state.net_type
            if change_type is None:
                continue
            warnings = [
                value
                for value in (state.before.status, state.after.status)
                if value != "available"
            ]
            before_blob = after_blob = None
            preview_status = "available"

            def persist(snapshot: _Snapshot) -> Optional[str]:
                nonlocal artifact_bytes, preview_status
                if snapshot.data is None:
                    return None
                if artifact_bytes + len(snapshot.data) > MAX_TURN_ARTIFACT_BYTES:
                    preview_status = "turn_budget_exceeded"
                    warnings.append("turn_artifact_budget_exceeded")
                    return None
                try:
                    blob_id = self._artifacts.put(snapshot.data)
                except ArtifactTooLargeError:
                    preview_status = "too_large"
                    warnings.append("artifact_too_large")
                    return None
                artifact_bytes += len(snapshot.data)
                return blob_id

            before_blob = persist(state.before)
            after_blob = persist(state.after)
            before_text: Optional[str] = None
            after_text: Optional[str] = None
            if state.before.data is not None:
                try:
                    before_text = state.before.data.decode("utf-8")
                except UnicodeDecodeError:
                    before_text = None
            if state.after.data is not None:
                try:
                    after_text = state.after.data.decode("utf-8")
                except UnicodeDecodeError:
                    after_text = None
            is_binary = (state.before.data is not None and before_text is None) or (
                state.after.data is not None and after_text is None
            )
            if is_binary:
                preview_status = "binary_preview_unsupported"
            elif (
                state.before.status == "too_large" or state.after.status == "too_large"
            ):
                preview_status = "too_large"
            elif (
                state.before.status != "available" or state.after.status != "available"
            ):
                preview_status = "capture_failed"

            diff = (
                build_diff(
                    before_text if state.before.exists else None,
                    after_text if state.after.exists else None,
                )
                if preview_status == "available"
                else build_diff(None, None)
            )
            line_additions = (
                diff.additions
                if before_text is not None or after_text is not None
                else 0
            )
            line_deletions = (
                diff.deletions
                if before_text is not None or after_text is not None
                else 0
            )
            additions += line_additions
            deletions += line_deletions
            files.append(
                {
                    "relative_path": rel,
                    "old_relative_path": state.old_relative_path,
                    "change_type": change_type,
                    "source": state.source,
                    "before_blob_id": before_blob,
                    "after_blob_id": after_blob,
                    "before_sha": state.before.digest,
                    "after_sha": state.after.digest,
                    "before_byte_count": len(state.before.data)
                    if state.before.data is not None
                    else 0,
                    "after_byte_count": len(state.after.data)
                    if state.after.data is not None
                    else 0,
                    "additions": line_additions,
                    "deletions": line_deletions,
                    "binary": is_binary,
                    "preview_status": preview_status,
                    "warnings": sorted(set(warnings)),
                }
            )
        resolved_coverage = coverage or self._coverage
        change_set_id = hashlib.sha256(
            f"{conversation_id}:{turn_id}".encode("utf-8")
        ).hexdigest()
        orphaned_blobs = repository.save_change_set(
            change_set_id=change_set_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            status=status,
            additions=additions,
            deletions=deletions,
            file_count=len(files),
            coverage=resolved_coverage,
            files=files,
        )
        for blob_id in orphaned_blobs:
            self._artifacts.delete(blob_id)
        return {
            "id": change_set_id,
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "status": status,
            "additions": additions,
            "deletions": deletions,
            "file_count": len(files),
            "coverage": resolved_coverage,
            "files": files,
        }

    def _extract_rel(self, prepared: PreparedCall) -> Optional[str]:
        args = prepared.normalized_args
        path = args.get("path")
        if not isinstance(path, str) or not path:
            return None
        try:
            return normalize_rel(path)
        except ValueError:
            return None

    def _capture(self, rel: str) -> _Snapshot:
        try:
            target = resolve_inside(self._root, rel, must_exist=False)
        except ValueError:
            return _Snapshot(False, None, None, "path_invalid")
        if not target.is_file():
            return _Snapshot(False, None, None)
        try:
            size = target.stat().st_size
            if size > MAX_FILE_BYTES:
                digest = hashlib.sha256()
                with target.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(64 * 1024), b""):
                        digest.update(chunk)
                return _Snapshot(True, None, digest.hexdigest(), "too_large")
            data = target.read_bytes()
            return _Snapshot(True, data, self._hash(data))
        except OSError:
            return _Snapshot(True, None, None, "read_failed")

    @staticmethod
    def _hash(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _capture_command_changes(self, outcome: ToolOutcome) -> None:
        before = self._command_before
        self._command_before = None
        if before is None:
            self._coverage = ChangeCoverage.INCOMPLETE.value
            return
        after, complete = self._scan_workspace()
        if not complete or not outcome.ok:
            # A failed command can still have performed side effects. We keep
            # every difference we observed, but do not claim full coverage.
            self._coverage = ChangeCoverage.INCOMPLETE.value
        if self._command_head != self._read_git_head_token():
            # A changed HEAD invalidates ordinary dirty-worktree comparison:
            # report the detected paths but fail closed on completeness.
            self._coverage = ChangeCoverage.INCOMPLETE.value
        self._command_head = None
        for rel in sorted(set(before) | set(after)):
            old = before.get(rel, _Snapshot(False, None, None))
            new = after.get(rel, _Snapshot(False, None, None))
            if old.exists == new.exists and old.digest == new.digest:
                continue
            existing = self._files.get(rel)
            if existing is None:
                self._files[rel] = _FileState(
                    before=old,
                    after=new,
                    source=ChangeSource.COMMAND_DETECTED.value,
                )
            else:
                existing.after = new
        self._coalesce_detected_renames()

    def _coalesce_detected_renames(self) -> None:
        deleted: Dict[str, List[str]] = {}
        created: Dict[str, List[str]] = {}
        for rel, state in self._files.items():
            if state.source != ChangeSource.COMMAND_DETECTED.value:
                continue
            if state.before.exists and not state.after.exists and state.before.digest:
                deleted.setdefault(state.before.digest, []).append(rel)
            elif not state.before.exists and state.after.exists and state.after.digest:
                created.setdefault(state.after.digest, []).append(rel)
        for digest in set(deleted) & set(created):
            old_paths = deleted[digest]
            new_paths = created[digest]
            # Multiple identical files are ambiguous; report create/delete
            # rather than inventing a rename relationship.
            if len(old_paths) != 1 or len(new_paths) != 1:
                continue
            old_rel = old_paths[0]
            new_rel = new_paths[0]
            old_state = self._files.pop(old_rel)
            new_state = self._files[new_rel]
            new_state.before = old_state.before
            new_state.old_relative_path = old_rel

    def _scan_workspace(self) -> tuple[Dict[str, _Snapshot], bool]:
        snapshots: Dict[str, _Snapshot] = {}
        started = time.monotonic()
        bytes_read = 0
        file_count = 0
        try:
            for directory, dirnames, filenames in os.walk(self._root):
                dirnames[:] = sorted(
                    name for name in dirnames if name not in _PROBE_IGNORED_DIRS
                )
                for filename in sorted(filenames):
                    file_count += 1
                    if (
                        file_count > MAX_PROBE_FILES
                        or time.monotonic() - started > MAX_PROBE_SECONDS
                    ):
                        return snapshots, False
                    target = Path(directory) / filename
                    try:
                        size = target.stat().st_size
                        if bytes_read + size > MAX_PROBE_BYTES:
                            return snapshots, False
                        data = target.read_bytes()
                    except OSError:
                        return snapshots, False
                    bytes_read += len(data)
                    rel = target.relative_to(self._root).as_posix()
                    if len(data) > MAX_FILE_BYTES:
                        snapshots[rel] = _Snapshot(
                            True, None, self._hash(data), "too_large"
                        )
                    else:
                        snapshots[rel] = _Snapshot(True, data, self._hash(data))
        except OSError:
            return snapshots, False
        return snapshots, True

    def _read_git_head_token(self) -> Optional[str]:
        """Read HEAD without invoking a command or counting .git internals."""

        dot_git = self._root / ".git"
        git_dir = dot_git
        try:
            if dot_git.is_file():
                marker = dot_git.read_text(encoding="utf-8").strip()
                if not marker.lower().startswith("gitdir:"):
                    return None
                git_dir = (self._root / marker.split(":", 1)[1].strip()).resolve()
            head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
            if not head.startswith("ref:"):
                return head
            ref = head.split(":", 1)[1].strip()
            loose = git_dir / Path(ref)
            if loose.is_file():
                return f"{ref}:{loose.read_text(encoding='utf-8').strip()}"
            packed = git_dir / "packed-refs"
            if packed.is_file():
                for line in packed.read_text(encoding="utf-8").splitlines():
                    if line and not line.startswith(("#", "^")):
                        digest, _, name = line.partition(" ")
                        if name == ref:
                            return f"{ref}:{digest}"
            return f"{ref}:unknown"
        except (OSError, RuntimeError, UnicodeDecodeError):
            return None
