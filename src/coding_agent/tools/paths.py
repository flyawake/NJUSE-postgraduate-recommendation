"""Path guard for all workspace file access.

Every file tool resolves relative paths through this guard before touching
the filesystem. It rejects absolute paths, drive-relative paths, ``..``
traversal and symlink escapes. This is best-effort single-process guarding,
not a sandbox.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class PathAccessError(ValueError):
    """A path violates the workspace boundary."""


class PathNotFoundError(PathAccessError):
    """The requested path does not exist."""


class PathIsDirectoryError(PathAccessError):
    """The requested path is a directory where a file was required."""


@dataclass(frozen=True)
class Workspace:
    root: Path

    def __post_init__(self) -> None:
        if not self.root.is_absolute():
            raise ValueError("workspace root must be absolute")


def make_workspace(root: Path) -> Workspace:
    resolved = root.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError(f"workspace is not a directory: {root}")
    return Workspace(resolved)


def normalize_rel(rel: str) -> str:
    """Validate and normalize a user-supplied relative path.

    Returns a forward-slash relative string such as ``src/app.py``.
    """
    if not isinstance(rel, str) or not rel.strip():
        raise PathAccessError("path must be a non-empty relative path")
    value = rel.strip()
    if os.path.isabs(value) or Path(value).is_absolute():
        raise PathAccessError(f"absolute paths are not allowed: {value!r}")
    drive, _ = os.path.splitdrive(value)
    if drive:
        raise PathAccessError(f"drive-qualified paths are not allowed: {value!r}")
    try:
        parts = Path(value).parts
    except (ValueError, OSError) as exc:
        raise PathAccessError(f"invalid path {value!r}") from exc
    if any(part == ".." for part in parts):
        raise PathAccessError(f"'..' traversal is not allowed: {value!r}")
    if not parts:
        return "."
    return "/".join(parts)


def resolve_inside(root: Path, rel: str, *, must_exist: bool = False) -> Path:
    """Resolve ``rel`` under ``root`` and reject anything escaping the root.

    Symlink components are resolved with ``strict=False``; if the resolved
    path lands outside the workspace (or a symlink loop breaks resolution)
    the access is rejected.
    """
    normalized = normalize_rel(rel)
    candidate = root / Path(*normalized.split("/"))
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise PathAccessError(f"cannot resolve path {normalized!r} safely") from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PathAccessError(f"path escapes the workspace: {normalized!r}") from exc
    if must_exist and not resolved.exists():
        raise PathNotFoundError(f"path does not exist: {normalized}")
    return resolved


def existing_directory(root: Path, rel: str) -> Path:
    path = resolve_inside(root, rel, must_exist=True)
    if not path.is_dir():
        raise PathAccessError(f"path is not a directory: {rel}")
    return path


def existing_file(root: Path, rel: str) -> Path:
    path = resolve_inside(root, rel, must_exist=True)
    if path.is_dir():
        raise PathIsDirectoryError(f"path is a directory: {rel}")
    return path
