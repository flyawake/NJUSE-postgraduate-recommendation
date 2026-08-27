"""run_command tool: bounded local subprocess execution.

``shell=False`` is mandatory. This tool enforces workspace cwd, a 1-120 s
timeout and head/tail output retention. It is explicitly not a sandbox:
any executable can still touch resources outside the workspace.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from typing import Any, Callable, Dict, Optional

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
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                shell=False,
            )
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
            f"cannot start command: {exc}",
            recovery_hint="check executable permissions",
        ) from exc

    deadline = time.monotonic() + args["timeout_seconds"]
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _terminate_tree(proc)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            raise ToolExecutionError(
                TIMEOUT,
                f"command timed out after {args['timeout_seconds']:g}s: {args['argv'][0]}",
                recovery_hint="reduce scope, increase timeout_seconds or fix a hung command",
            )
        try:
            stdout, stderr = proc.communicate(timeout=min(POLL_INTERVAL, remaining))
            break
        except subprocess.TimeoutExpired:
            if is_cancelled():
                _terminate_tree(proc)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                raise ToolExecutionError(
                    TOOL_ABORTED,
                    "command aborted by user cancellation",
                    recovery_hint="re-run a narrower command if needed",
                )

    stdout_text = (stdout or b"").decode("utf-8", errors="replace")
    stderr_text = (stderr or b"").decode("utf-8", errors="replace")
    stdout_kept, stdout_truncated, stdout_omitted = _truncate(stdout_text)
    stderr_kept, stderr_truncated, stderr_omitted = _truncate(stderr_text)
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
