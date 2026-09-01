"""Conversation/workspace checkpoint planning and crash-safe file restoration.

The database owns timeline facts and the restore journal.  This module owns
only deterministic workspace state transitions built from persisted turn
ChangeSets.  Every step is compare-before-write: a restore never overwrites a
file whose current digest is not the digest recorded by the active timeline.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from .artifacts.store import ArtifactCorruptError, ArtifactStore
from .tools.file_io import atomic_write_bytes
from .tools.paths import PathAccessError, normalize_rel, resolve_inside


class CheckpointError(Exception):
    def __init__(self, code: str, message: str, *, path: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.path = path


def _file_state(
    *, exists: bool, sha: Optional[str], blob_id: Optional[str]
) -> Dict[str, Any]:
    return {"exists": bool(exists), "sha": sha, "blob_id": blob_id}


def _states_equal(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    return bool(left.get("exists")) == bool(right.get("exists")) and (
        not left.get("exists") or left.get("sha") == right.get("sha")
    )


class WorkspaceCheckpointRestorer:
    """Build, validate, apply, and roll back bounded file restore plans."""

    def __init__(self, workspace_root: Path, artifacts: ArtifactStore) -> None:
        self._root = workspace_root.resolve(strict=True)
        self._artifacts = artifacts

    def build_plan(
        self,
        change_sets: Sequence[Dict[str, Any]],
        *,
        target_turn_id: str,
        future_turn_ids: Sequence[str],
    ) -> Dict[str, Any]:
        blockers: List[Dict[str, Any]] = []
        steps: List[Dict[str, Any]] = []
        for change_set in change_sets:
            turn_id = str(change_set.get("turn_id", ""))
            if change_set.get("coverage") != "complete":
                blockers.append(
                    {
                        "code": "checkpoint_incomplete_changes",
                        "message": "后续轮次的文件变更捕获不完整",
                        "turn_id": turn_id,
                    }
                )
                continue
            for change in change_set.get("files", []):
                try:
                    steps.extend(self._steps_for_change(turn_id, change))
                except CheckpointError as exc:
                    blockers.append(
                        {
                            "code": exc.code,
                            "message": str(exc),
                            "path": exc.path,
                            "turn_id": turn_id,
                        }
                    )

        blockers.extend(self._path_collision_blockers(steps))
        if not blockers:
            blockers.extend(self._validate_plan(steps))
        affected = sorted({str(step["path"]) for step in steps})
        return {
            "version": 1,
            "target_turn_id": target_turn_id,
            "future_turn_ids": list(future_turn_ids),
            "steps": steps,
            "affected_files": affected,
            "blockers": blockers,
        }

    def apply(
        self,
        plan: Dict[str, Any],
        *,
        on_progress: Optional[Callable[[int], None]] = None,
    ) -> int:
        steps = self._validated_steps(plan)
        for index, step in enumerate(steps, start=1):
            current = self._inspect(str(step["path"]))
            original = dict(step["original"])
            target = dict(step["target"])
            if _states_equal(current, target):
                # Idempotent continuation after a process interruption between
                # the atomic file replace and the progress checkpoint.
                if on_progress is not None:
                    on_progress(index)
                continue
            if not _states_equal(current, original):
                raise CheckpointError(
                    "checkpoint_file_conflict",
                    "工作区文件已在检查点之外发生变化",
                    path=str(step["path"]),
                )
            self._write_state(str(step["path"]), target)
            if on_progress is not None:
                on_progress(index)
        return len({str(step["path"]) for step in steps})

    def rollback(self, plan: Dict[str, Any]) -> int:
        """Restore the pre-operation state after a failed or crashed apply."""

        steps = self._validated_steps(plan)
        touched: set[str] = set()
        for step in reversed(steps):
            path = str(step["path"])
            current = self._inspect(path)
            original = dict(step["original"])
            target = dict(step["target"])
            if _states_equal(current, original):
                continue
            if not _states_equal(current, target):
                raise CheckpointError(
                    "checkpoint_recovery_conflict",
                    "恢复操作中断后，文件又被其他操作修改",
                    path=path,
                )
            self._write_state(path, original)
            touched.add(path)
        return len(touched)

    def verify_applied(self, plan: Dict[str, Any]) -> None:
        """Recheck the final workspace projection immediately before DB commit."""

        final: Dict[str, Dict[str, Any]] = {}
        for step in self._validated_steps(plan):
            final[str(step["path"])] = dict(step["target"])
        for path, expected in final.items():
            if not _states_equal(self._inspect(path), expected):
                raise CheckpointError(
                    "checkpoint_file_conflict",
                    "文件在检查点写回后、时间线提交前再次发生变化",
                    path=path,
                )

    def _steps_for_change(
        self, turn_id: str, change: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        change_type = str(change.get("change_type", ""))
        path = self._normalized_path(change.get("relative_path"))
        before = self._persisted_state(
            exists=change_type not in {"created"},
            sha=change.get("before_sha"),
            blob_id=change.get("before_blob_id"),
            path=path,
        )
        after = self._persisted_state(
            exists=change_type not in {"deleted"},
            sha=change.get("after_sha"),
            blob_id=change.get("after_blob_id"),
            path=path,
        )
        if change_type in {"created", "modified", "deleted"}:
            return [
                {
                    "turn_id": turn_id,
                    "path": path,
                    "original": after,
                    "target": before,
                }
            ]
        if change_type == "renamed":
            old_path = self._normalized_path(change.get("old_relative_path"))
            # Rename is represented as two explicit file states.  This avoids
            # relying on rename atomicity across directories and makes crash
            # recovery use the same compare-before-write primitive.
            return [
                {
                    "turn_id": turn_id,
                    "path": path,
                    "original": after,
                    "target": _file_state(exists=False, sha=None, blob_id=None),
                },
                {
                    "turn_id": turn_id,
                    "path": old_path,
                    "original": _file_state(exists=False, sha=None, blob_id=None),
                    "target": before,
                },
            ]
        raise CheckpointError(
            "checkpoint_change_unsupported",
            "检查点包含无法识别的文件变更类型",
            path=path,
        )

    @staticmethod
    def _normalized_path(value: Any) -> str:
        try:
            return normalize_rel(str(value or ""))
        except PathAccessError as exc:
            raise CheckpointError(
                "checkpoint_path_unsafe", "检查点包含不安全的文件路径"
            ) from exc

    @staticmethod
    def _path_collision_blockers(
        steps: Sequence[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Reject aliases and file/directory transitions we cannot replay safely."""

        seen: Dict[str, str] = {}
        paths = sorted({str(step["path"]) for step in steps})
        blockers: List[Dict[str, Any]] = []
        for path in paths:
            key = os.path.normcase(path)
            prior = seen.get(key)
            if prior is not None and prior != path:
                blockers.append(
                    {
                        "code": "checkpoint_path_collision",
                        "message": "多个检查点路径指向同一个工作区文件",
                        "path": path,
                    }
                )
            else:
                seen[key] = path
        split_paths = [(path, tuple(Path(path).parts)) for path in paths]
        for index, (path, parts) in enumerate(split_paths):
            for child, child_parts in split_paths[index + 1 :]:
                if len(parts) < len(child_parts) and child_parts[: len(parts)] == parts:
                    blockers.append(
                        {
                            "code": "checkpoint_path_collision",
                            "message": "检查点包含无法安全重放的文件与目录转换",
                            "path": f"{path} / {child}",
                        }
                    )
        return blockers

    def _persisted_state(
        self,
        *,
        exists: bool,
        sha: Optional[str],
        blob_id: Optional[str],
        path: str,
    ) -> Dict[str, Any]:
        if not exists:
            return _file_state(exists=False, sha=None, blob_id=None)
        if not sha or not blob_id:
            raise CheckpointError(
                "checkpoint_artifact_missing",
                "恢复所需的文件快照不可用",
                path=path,
            )
        if sha != blob_id:
            raise CheckpointError(
                "checkpoint_artifact_mismatch",
                "恢复快照的版本指纹不一致",
                path=path,
            )
        try:
            data = self._artifacts.read(str(blob_id))
        except (FileNotFoundError, ArtifactCorruptError, OSError, ValueError) as exc:
            raise CheckpointError(
                "checkpoint_artifact_unavailable",
                "恢复快照缺失或完整性校验失败",
                path=path,
            ) from exc
        if hashlib.sha256(data).hexdigest() != sha:
            raise CheckpointError(
                "checkpoint_artifact_mismatch",
                "恢复快照的内容指纹不一致",
                path=path,
            )
        return _file_state(exists=True, sha=str(sha), blob_id=str(blob_id))

    def _validate_plan(self, steps: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        virtual: Dict[str, Dict[str, Any]] = {}
        blockers: List[Dict[str, Any]] = []
        for step in steps:
            path = str(step["path"])
            try:
                current = virtual.get(path)
                if current is None:
                    current = self._inspect(path)
                if not _states_equal(current, dict(step["original"])):
                    blockers.append(
                        {
                            "code": "checkpoint_file_conflict",
                            "message": "当前文件与已记录的时间线版本不一致",
                            "path": path,
                            "turn_id": step.get("turn_id"),
                        }
                    )
                    continue
                virtual[path] = dict(step["target"])
            except CheckpointError as exc:
                blockers.append(
                    {
                        "code": exc.code,
                        "message": str(exc),
                        "path": exc.path or path,
                        "turn_id": step.get("turn_id"),
                    }
                )
        return blockers

    def _inspect(self, rel: str) -> Dict[str, Any]:
        normalized = normalize_rel(rel)
        nominal = self._root / Path(*normalized.split("/"))
        self._reject_symlink_components(nominal, normalized)
        try:
            target = resolve_inside(self._root, normalized, must_exist=False)
        except PathAccessError as exc:
            raise CheckpointError(
                "checkpoint_path_unsafe", "检查点路径超出工作区", path=normalized
            ) from exc
        if not target.exists():
            return _file_state(exists=False, sha=None, blob_id=None)
        if not target.is_file():
            raise CheckpointError(
                "checkpoint_path_conflict",
                "需要恢复的文件路径当前不是普通文件",
                path=normalized,
            )
        digest = hashlib.sha256()
        try:
            with target.open("rb") as handle:
                for chunk in iter(lambda: handle.read(64 * 1024), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise CheckpointError(
                "checkpoint_file_unreadable",
                "无法读取当前工作区文件",
                path=normalized,
            ) from exc
        return _file_state(exists=True, sha=digest.hexdigest(), blob_id=None)

    def _write_state(self, rel: str, state: Dict[str, Any]) -> None:
        normalized = normalize_rel(rel)
        nominal = self._root / Path(*normalized.split("/"))
        self._reject_symlink_components(nominal, normalized)
        try:
            target = resolve_inside(self._root, normalized, must_exist=False)
        except PathAccessError as exc:
            raise CheckpointError(
                "checkpoint_path_unsafe", "检查点路径超出工作区", path=normalized
            ) from exc
        if not state.get("exists"):
            try:
                if target.exists():
                    if not target.is_file():
                        raise CheckpointError(
                            "checkpoint_path_conflict",
                            "待删除路径不是普通文件",
                            path=normalized,
                        )
                    target.unlink()
                    self._sync_directory(target.parent)
            except OSError as exc:
                raise CheckpointError(
                    "checkpoint_write_failed", "无法删除工作区文件", path=normalized
                ) from exc
            return
        blob_id = state.get("blob_id")
        expected_sha = state.get("sha")
        if not isinstance(blob_id, str) or not isinstance(expected_sha, str):
            raise CheckpointError(
                "checkpoint_plan_corrupt", "恢复计划缺少文件快照", path=normalized
            )
        try:
            data = self._artifacts.read(blob_id)
        except (FileNotFoundError, ArtifactCorruptError, OSError, ValueError) as exc:
            raise CheckpointError(
                "checkpoint_artifact_unavailable",
                "恢复快照缺失或完整性校验失败",
                path=normalized,
            ) from exc
        if hashlib.sha256(data).hexdigest() != expected_sha:
            raise CheckpointError(
                "checkpoint_artifact_mismatch",
                "恢复快照的内容指纹不一致",
                path=normalized,
            )
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            self._reject_symlink_components(target, normalized)
            atomic_write_bytes(target, data)
        except CheckpointError:
            raise
        except Exception as exc:
            raise CheckpointError(
                "checkpoint_write_failed", "无法写回工作区文件", path=normalized
            ) from exc
        actual = self._inspect(normalized)
        if not _states_equal(actual, state):
            raise CheckpointError(
                "checkpoint_write_verify_failed",
                "文件写回后的完整性校验失败",
                path=normalized,
            )

    def _reject_symlink_components(self, nominal: Path, rel: str) -> None:
        current = self._root
        try:
            parts = nominal.relative_to(self._root).parts
        except ValueError as exc:
            raise CheckpointError(
                "checkpoint_path_unsafe", "检查点路径超出工作区", path=rel
            ) from exc
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise CheckpointError(
                    "checkpoint_symlink_unsupported",
                    "为避免改变链接目标，检查点不自动恢复符号链接路径",
                    path=rel,
                )

    @staticmethod
    def _sync_directory(path: Path) -> None:
        if os.name == "nt":
            return
        try:
            fd = os.open(path, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            # The file mutation already succeeded. Directory fsync is a
            # durability enhancement and is not available on every platform.
            pass

    @staticmethod
    def _validated_steps(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
        if int(plan.get("version", 0)) != 1 or not isinstance(plan.get("steps"), list):
            raise CheckpointError("checkpoint_plan_corrupt", "恢复计划格式无效")
        steps = list(plan["steps"])
        for step in steps:
            if not isinstance(step, dict) or not isinstance(step.get("path"), str):
                raise CheckpointError("checkpoint_plan_corrupt", "恢复计划步骤格式无效")
            if not isinstance(step.get("original"), dict) or not isinstance(
                step.get("target"), dict
            ):
                raise CheckpointError("checkpoint_plan_corrupt", "恢复计划文件状态无效")
        return steps


def summarize_actions(plan: Dict[str, Any]) -> Dict[str, int]:
    """Collapse sequential steps into the final user-visible path actions."""

    initial: Dict[str, Dict[str, Any]] = {}
    final: Dict[str, Dict[str, Any]] = {}
    for step in plan.get("steps", []):
        path = str(step["path"])
        initial.setdefault(path, dict(step["original"]))
        final[path] = dict(step["target"])
    counts = {"create": 0, "modify": 0, "delete": 0}
    for path in sorted(final):
        before = initial[path]
        after = final[path]
        if _states_equal(before, after):
            continue
        if not before.get("exists") and after.get("exists"):
            counts["create"] += 1
        elif before.get("exists") and not after.get("exists"):
            counts["delete"] += 1
        else:
            counts["modify"] += 1
    return counts
