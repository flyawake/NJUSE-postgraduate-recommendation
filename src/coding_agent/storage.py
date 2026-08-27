"""Small atomic-JSON helpers shared by user-level config stores.

Both ``config.json`` and ``credentials.json`` live under the user's
``CODING_AGENT_HOME`` directory. Writes go to a temporary file in the same
directory, are flushed/fsynced and then atomically replaced with
``os.replace`` so a crash can never leave a half-written config behind.
Permissions are best-effort tightened on POSIX (0700 dir / 0600 file);
Windows relies on the user directory ACLs and is documented as plaintext.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict


class StorageError(Exception):
    """A config/credential file could not be read or written safely."""


def ensure_home(home: Path) -> None:
    """Create the user config directory and best-effort 0700 on POSIX."""
    try:
        home.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StorageError(f"cannot create config directory {home}: {exc}") from exc
    if os.name != "nt":
        try:
            os.chmod(home, 0o700)
        except OSError:
            pass


def atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    """Atomically write ``data`` as JSON to ``path``.

    On failure the original file (if any) is left untouched. Raises
    :class:`StorageError` with a message that never contains secrets.
    """
    directory = path.parent
    ensure_home(directory)
    try:
        payload = json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2).encode(
            "utf-8"
        )
    except (TypeError, ValueError) as exc:  # pragma: no cover - programming error
        raise StorageError(f"cannot serialize config data: {exc}") from exc
    tmp = directory / f".{path.name}.{os.getpid()}.tmp"
    try:
        with open(tmp, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
        os.replace(tmp, path)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise StorageError(f"cannot write config file {path}: {exc}") from exc


def load_json(path: Path) -> Dict[str, Any]:
    """Load a JSON object; raise :class:`StorageError` on any problem."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise StorageError(f"config file not found: {path}") from exc
    except OSError as exc:
        raise StorageError(f"cannot read config file {path}: {exc}") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StorageError(f"config file is not valid JSON: {path}") from exc
    if not isinstance(data, dict):
        raise StorageError(f"config file must contain a JSON object: {path}")
    return data
