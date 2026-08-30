"""run_command tool: bounded local subprocess execution.

``shell=False`` is mandatory. This tool enforces workspace cwd, a 1-120 s
timeout and head/tail output retention. It is explicitly not a sandbox:
any executable can still touch resources outside the workspace.
"""

from __future__ import annotations

import codecs
import os
import signal
import subprocess
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

from .base import (
    COMMAND_FAILED,
    COMMAND_NOT_FOUND,
    PATH_NOT_ALLOWED,
    TIMEOUT,
    TOOL_ABORTED,
    ToolEffect,
    ToolExecutionError,
    ToolSpec,
)
from .paths import PathAccessError, existing_directory, normalize_rel

VALID_PURPOSES = ("inspect", "verify", "other")
DEFAULT_TIMEOUT = 30
MIN_TIMEOUT = 1
MAX_TIMEOUT = 120
HEAD_CHARS = 4_000
TAIL_CHARS = 6_000
POLL_INTERVAL = 0.2
MAX_ARGV_ITEMS = 128
MAX_ARG_CHARS = 8_192
MAX_ARGV_CHARS = 32_768

DESCRIPTION = (
    "Run a local command inside the workspace without a shell. argv must be "
    "a non-empty string array, cwd must stay inside the workspace, timeout is "
    "1-120 seconds, and purpose is inspect/verify/other. stdout/stderr keep "
    "head 4000 + tail 6000 characters each and report omitted amounts. A "
    "non-zero exit code is still a successful observation."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "argv": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "description": "Command and arguments without a shell.",
        },
        "cwd": {
            "type": "string",
            "description": "Workspace-relative working directory. Default '.'.",
        },
        "timeout_seconds": {
            "type": "number",
            "description": "Timeout between 1 and 120 seconds. Default 30.",
        },
        "purpose": {
            "type": "string",
            "enum": list(VALID_PURPOSES),
            "description": "inspect | verify | other.",
        },
    },
    "required": ["argv"],
    "additionalProperties": False,
}


def _validate(args: Dict) -> Dict:
    argv = args.get("argv")
    if not isinstance(argv, list) or not argv:
        raise ToolExecutionError.invalid_argument(
            "argv must be a non-empty array of strings"
        )
    if any(not isinstance(part, str) or part == "" for part in argv):
        raise ToolExecutionError.invalid_argument(
            "every argv element must be a non-empty string"
        )
    if len(argv) > MAX_ARGV_ITEMS:
        raise ToolExecutionError.invalid_argument(
            f"argv may contain at most {MAX_ARGV_ITEMS} elements"
        )
    if (
        any(len(part) > MAX_ARG_CHARS for part in argv)
        or sum(map(len, argv)) > MAX_ARGV_CHARS
    ):
        raise ToolExecutionError.invalid_argument(
            "argv exceeds the bounded argument size"
        )
    cwd = args.get("cwd", ".")
    if not isinstance(cwd, str) or not cwd.strip():
        raise ToolExecutionError.invalid_argument("cwd must be a non-empty string")
    try:
        normalize_rel(cwd)
    except ValueError as exc:
        raise ToolExecutionError(PATH_NOT_ALLOWED, str(exc)) from exc
    timeout = args.get("timeout_seconds", DEFAULT_TIMEOUT)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ToolExecutionError.invalid_argument("timeout_seconds must be a number")
    if not (MIN_TIMEOUT <= timeout <= MAX_TIMEOUT):
        raise ToolExecutionError.invalid_argument(
            f"timeout_seconds must be between {MIN_TIMEOUT} and {MAX_TIMEOUT}"
        )
    purpose = args.get("purpose", "other")
    if purpose not in VALID_PURPOSES:
        raise ToolExecutionError.invalid_argument(
            f"purpose must be one of {', '.join(VALID_PURPOSES)}"
        )
    return {
        "argv": list(argv),
        "cwd": cwd,
        "timeout_seconds": float(timeout),
        "purpose": purpose,
    }


def _terminate_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                shell=False,
            )
            if result.returncode == 0:
                return
        except Exception:
            pass
        try:
            proc.kill()
        except OSError:
            pass
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def _truncate(text: str, head: int = HEAD_CHARS, tail: int = TAIL_CHARS):
    if len(text) <= head + tail:
        return text, False, 0
    omitted = len(text) - head - tail
    return text[:head] + text[-tail:], True, omitted


class _BoundedTextCapture:
    """Thread-confined streaming head/tail capture with constant memory."""

    def __init__(self, head: int = HEAD_CHARS, tail: int = TAIL_CHARS) -> None:
        self._head_limit = head
        self._tail_limit = tail
        self._head = ""
        self._tail = ""
        self._total = 0

    def append(self, text: str) -> None:
        if not text:
            return
        self._total += len(text)
        if len(self._head) < self._head_limit:
            take = min(self._head_limit - len(self._head), len(text))
            self._head += text[:take]
            text = text[take:]
        if text:
            self._tail = (self._tail + text)[-self._tail_limit :]

    def result(self) -> Tuple[str, bool, int]:
        kept = self._head + self._tail
        omitted = max(0, self._total - len(kept))
        return kept, omitted > 0, omitted


def _drain_pipe(pipe, capture: _BoundedTextCapture) -> None:
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    try:
        try:
            while True:
                chunk = pipe.read(64 * 1024)
                if not chunk:
                    break
                capture.append(decoder.decode(chunk, final=False))
            capture.append(decoder.decode(b"", final=True))
        except (OSError, ValueError):
            # The owner may close a stuck pipe after the process exits. The
            # bounded output captured before closure remains valid.
            pass
    finally:
        try:
            pipe.close()
        except (OSError, ValueError):
            pass


def _join_readers(readers, pipes) -> None:
    for reader in readers:
        reader.join(timeout=5)
    for reader, pipe in zip(readers, pipes):
        if not reader.is_alive():
            continue
        try:
            pipe.close()
        except (OSError, ValueError):
            pass
        reader.join(timeout=1)


def _wait_with_captures(
    proc: subprocess.Popen,
    deadline: float,
    is_cancelled: Callable[[], bool],
) -> Tuple[Tuple[str, bool, int], Tuple[str, bool, int]]:
    stdout_pipe = getattr(proc, "stdout", None)
    stderr_pipe = getattr(proc, "stderr", None)
    # Compatibility for small fake Popen implementations used by embedders.
    # Real subprocesses always take the streaming path below.
    if stdout_pipe is None or stderr_pipe is None:
        stdout, stderr = proc.communicate()
        stdout_text = (stdout or b"").decode("utf-8", errors="replace")
        stderr_text = (stderr or b"").decode("utf-8", errors="replace")
        return _truncate(stdout_text), _truncate(stderr_text)

    stdout_capture = _BoundedTextCapture()
    stderr_capture = _BoundedTextCapture()
    readers = [
        threading.Thread(
            target=_drain_pipe, args=(stdout_pipe, stdout_capture), daemon=True
        ),
        threading.Thread(
            target=_drain_pipe, args=(stderr_pipe, stderr_capture), daemon=True
        ),
    ]
    for reader in readers:
        reader.start()

    failure: Optional[ToolExecutionError] = None
    while proc.poll() is None:
        if is_cancelled():
            failure = ToolExecutionError(
                TOOL_ABORTED,
                "command aborted by user cancellation",
                recovery_hint="re-run a narrower command if needed",
            )
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            failure = ToolExecutionError(
                TIMEOUT,
                "command exceeded its configured timeout",
                recovery_hint="reduce scope, increase timeout_seconds or fix a hung command",
            )
            break
        try:
            proc.wait(timeout=min(POLL_INTERVAL, remaining))
        except subprocess.TimeoutExpired:
            continue

    if failure is not None:
        _terminate_tree(proc)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _terminate_tree(proc)
        _join_readers(readers, (stdout_pipe, stderr_pipe))
        raise failure

    _join_readers(readers, (stdout_pipe, stderr_pipe))
    return stdout_capture.result(), stderr_capture.result()


def _handle(
    args: Dict,
    workspace_root,
    is_cancelled: Callable[[], bool],
) -> Dict[str, Any]:
    rel_cwd = normalize_rel(args["cwd"])
    try:
        cwd_path = existing_directory(workspace_root, args["cwd"])
    except PathAccessError as exc:
        raise ToolExecutionError(PATH_NOT_ALLOWED, str(exc)) from exc

    try:
        proc = subprocess.Popen(
            args["argv"],
            cwd=str(cwd_path),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            start_new_session=(os.name != "nt"),
        )
    except FileNotFoundError as exc:
        raise ToolExecutionError(
            COMMAND_NOT_FOUND,
            f"command not found: {args['argv'][0]}",
            recovery_hint="install the command or fix the executable name",
        ) from exc
    except (PermissionError, OSError) as exc:
        raise ToolExecutionError(
            COMMAND_FAILED,
            f"cannot start command ({type(exc).__name__})",
            recovery_hint="check executable permissions",
        ) from exc

    deadline = time.monotonic() + args["timeout_seconds"]
    stdout_result, stderr_result = _wait_with_captures(proc, deadline, is_cancelled)
    stdout_kept, stdout_truncated, stdout_omitted = stdout_result
    stderr_kept, stderr_truncated, stderr_omitted = stderr_result
    return {
        "argv": args["argv"],
        "cwd": rel_cwd,
        "timeout_seconds": args["timeout_seconds"],
        "purpose": args["purpose"],
        "returncode": proc.returncode,
        "stdout": stdout_kept,
        "stderr": stderr_kept,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "stdout_omitted": stdout_omitted,
        "stderr_omitted": stderr_omitted,
    }


def build_run_spec(
    workspace_root,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> ToolSpec:
    cancel = is_cancelled or (lambda: False)
    return ToolSpec(
        name="run_command",
        description=DESCRIPTION,
        schema=SCHEMA,
        effect=ToolEffect.EXECUTE,
        validator=_validate,
        handler=lambda args: _handle(args, workspace_root, cancel),
    )
