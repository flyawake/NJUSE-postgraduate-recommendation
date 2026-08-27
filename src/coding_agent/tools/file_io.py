"""Atomic write helper shared by write_file and edit_file.

The write goes to a temporary file in the same directory, is flushed and
closed, then atomically replaces the target with ``os.replace``. On failure
the temporary file is removed and the original stays untouched.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .base import WRITE_FAILED, ToolExecutionError


def atomic_write_bytes(target: Path, data: bytes) -> None:
    parent = target.parent
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=str(parent)
        )
    except OSError as exc:
        raise ToolExecutionError(
            WRITE_FAILED,
            f"cannot create temporary file in {parent}: {exc}",
        ) from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    except BaseException as exc:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        if isinstance(exc, ToolExecutionError):
            raise
        raise ToolExecutionError(WRITE_FAILED, f"cannot write {target}: {exc}") from exc
