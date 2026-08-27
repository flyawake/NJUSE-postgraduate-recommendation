#!/usr/bin/env python3
"""Fake OpenAI-compatible Chat Completions server for Playwright E2E.

Serves ``POST /v1/chat/completions`` (and ``/v1-slow/chat/completions`` which
sleeps a few seconds per request) with a scripted trajectory driven by the
last tool result in the conversation:

    glob("**/*.py") -> grep("TODO") -> read_file -> edit_file -> verify -> answer

The seed workspace contains ``hello.py`` with a ``# TODO: return greeting``
marker, so the fixed old/new strings always match after the read. Returns the
exact OpenAI-style JSON shape expected by the project's model adapter.
"""

from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

GREET_OLD = "# TODO: return greeting\n    pass"
# The sentinel is deliberately embedded in the tool payload; the UI/API
# redaction must keep it out of the DOM even though it reaches the file.
E2E_SENTINEL = "E2E-SENTINEL-9f3c1"
GREET_NEW = f'    return f"Hello, {{name}}!"  # {E2E_SENTINEL}'
FINAL_ANSWER = "已完成：greet 已实现并通过 py_compile 验证。"


def _tool_call(call_id: str, name: str, arguments: dict) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments, ensure_ascii=False, sort_keys=True),
        },
    }


def _choice(message: dict, finish_reason: str) -> dict:
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "created": 0,
        "model": "fake-model",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
    }


def _assistant_with_calls(calls: list[dict]) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": calls,
    }


def _assistant_text(text: str) -> dict:
    return {"role": "assistant", "content": text, "tool_calls": None}


def _classify_last_tool_result(messages: list[dict]) -> str | None:
    """Return the kind of the latest tool result, or None when none yet."""
    last = None
    for message in messages:
        if message.get("role") == "tool":
            last = message
    if last is None:
        return None
    try:
        content = json.loads(last.get("content") or "{}")
    except json.JSONDecodeError:
        return "unknown"
    data = content.get("data") or {}
    if not content.get("ok"):
        return "error"
    if "lines" in data:
        return "read_file"
    if "replacements" in data:
        return "edit_file"
    if "argv" in data:
        return "run_command"
    matches = data.get("matches")
    if isinstance(matches, list) and matches and isinstance(matches[0], dict):
        return "grep"
    if isinstance(matches, list) and matches and isinstance(matches[0], str):
        return "glob"
    return "unknown"


def next_response(messages: list[dict]) -> dict:
    kind = _classify_last_tool_result(messages)

    if kind is None:
        return _assistant_with_calls(
            [_tool_call("call_1", "glob", {"pattern": "**/*.py", "path": "."})]
        )
    if kind == "glob":
        return _assistant_with_calls(
            [
                _tool_call(
                    "call_2",
                    "grep",
                    {"pattern": "TODO", "path": ".", "include": "*.py"},
                )
            ]
        )
    if kind == "grep":
        # Recover the file path from the grep matches (first entry).
        file_path = "hello.py"
        for message in reversed(messages):
            if message.get("role") == "tool":
                try:
                    data = json.loads(message.get("content") or "{}").get("data") or {}
                except json.JSONDecodeError:
                    continue
                matches = data.get("matches") or []
                if matches and isinstance(matches[0], dict) and matches[0].get("file"):
                    file_path = matches[0]["file"]
                break
        return _assistant_with_calls(
            [
                _tool_call(
                    "call_3",
                    "read_file",
                    {"path": file_path, "offset": 1, "limit": 200},
                )
            ]
        )
    if kind == "read_file":
        return _assistant_with_calls(
            [
                _tool_call(
                    "call_4",
                    "edit_file",
                    {
                        "path": "hello.py",
                        "old_string": GREET_OLD,
                        "new_string": GREET_NEW,
                    },
                )
            ]
        )
    if kind == "edit_file":
        return _assistant_with_calls(
            [
                _tool_call(
                    "call_5",
                    "run_command",
                    {
                        # Use the PATH resolver (not the interpreter's
                        # absolute path) so runs stay machine-agnostic. The
                        # extra operands deliberately carry the sentinel;
                        # Python ignores them while public argv redaction is
                        # exercised all the way through the browser DOM.
                        "argv": [
                            "python",
                            "-c",
                            "import py_compile; py_compile.compile('hello.py', doraise=True)",
                            f"FOO={E2E_SENTINEL}",
                            f"--header=Bearer-{E2E_SENTINEL}",
                            E2E_SENTINEL,
                        ],
                        "cwd": ".",
                        "timeout_seconds": 30,
                        "purpose": "verify",
                    },
                )
            ]
        )
    if kind == "run_command":
        # exit code 0 => verified; then give the final answer.
        return _assistant_text(FINAL_ANSWER)
    # Any unexpected state (e.g. an earlier tool error) re-runs the glob.
    return _assistant_with_calls(
        [_tool_call("call_1", "glob", {"pattern": "**/*.py", "path": "."})]
    )


def next_shot_response(messages: list[dict]) -> dict:
    """Variant for screenshot capture: first tool is a slow run_command so the
    running screenshot shows EXECUTING_TOOLS with non-zero counters."""
    kind = _classify_last_tool_result(messages)
    if kind is None:
        return _assistant_with_calls(
            [
                _tool_call(
                    "shot_1",
                    "run_command",
                    {
                        "argv": ["python", "-c", "import time; time.sleep(3)"],
                        "cwd": ".",
                        "timeout_seconds": 10,
                        "purpose": "inspect",
                    },
                )
            ]
        )
    if kind == "run_command":
        # The screenshot trajectory has two commands: an initial sleep used
        # to capture a truthful running frame, then the real py_compile
        # verification. Only the first one should lead into the normal tool
        # sequence; treating the verifier the same caused an endless
        # glob/.../verify loop that ended at MAX_STEPS.
        for message in reversed(messages):
            if message.get("role") != "tool":
                continue
            try:
                argv = (
                    json.loads(message.get("content") or "{}").get("data") or {}
                ).get("argv") or []
            except json.JSONDecodeError:
                argv = []
            if any("py_compile" in str(part) for part in argv):
                return _assistant_text(FINAL_ANSWER)
            break
        return _assistant_with_calls(
            [_tool_call("shot_2", "glob", {"pattern": "**/*.py", "path": "."})]
        )
    return next_response(messages)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        # Keep server logs small and secret-free during tests.
        return

    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        if self.path == "/health":
            self._send({"status": "ok"})
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._send({"error": "bad request"}, 400)
            return
        if self.path.endswith("/v1-shot/chat/completions"):
            time.sleep(0.5)
        elif self.path.endswith("/v1-slow/chat/completions"):
            time.sleep(4.0)
        elif not self.path.endswith("/chat/completions"):
            self._send({"error": "not found"}, 404)
            return
        else:
            # Give the UI streaming assertions a comfortable window per step
            # while keeping the whole closed loop under ~8 seconds.
            time.sleep(0.5)
        messages = body.get("messages") or []
        if self.path.endswith("/v1-shot/chat/completions"):
            self._send(_choice(next_shot_response(messages), "stop"))
            return
        self._send(_choice(next_response(messages), "stop"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"fake model listening on 127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
