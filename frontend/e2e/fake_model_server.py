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
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

GREET_OLD = "# TODO: return greeting\n    pass"
# The sentinel is deliberately embedded in the tool payload; the UI/API
# redaction must keep it out of the DOM even though it reaches the file.
E2E_SENTINEL = "E2E-SENTINEL-9f3c1"
GREET_NEW = f'    return f"Hello, {{name}}!"  # {E2E_SENTINEL}'
FINAL_ANSWER = "已完成：greet 已实现并通过 py_compile 验证。"
_RETRY_COUNTS: dict[str, int] = {}
_RETRY_LOCK = threading.Lock()


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


def _is_sse_stress_run(messages: list[dict]) -> bool:
    """Keep the performance E2E offline while exercising a long SSE tail."""
    return any(
        message.get("role") == "user"
        and "SSE_STRESS" in str(message.get("content") or "")
        for message in messages
    )


def _stress_response(messages: list[dict]) -> dict:
    tool_results = sum(1 for message in messages if message.get("role") == "tool")
    if tool_results >= 13:
        return _assistant_text("SSE stress run completed.")
    # Each signature is unique so the real duplicate-call guard remains on.
    index = tool_results + 1
    return _assistant_with_calls(
        [
            _tool_call(
                f"stress_{index}",
                "glob",
                {"pattern": f"**/*{index}.py", "path": "."},
            )
        ]
    )


def next_response(messages: list[dict]) -> dict:
    if _is_sse_stress_run(messages):
        return _stress_response(messages)
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

    def _send_stream(self, message: dict, include_reasoning: bool = True) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        def emit(delta: dict, finish_reason: str | None = None) -> None:
            payload = {
                "id": "chatcmpl-fake",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "fake-model",
                "choices": [
                    {
                        "index": 0,
                        "delta": delta,
                        "finish_reason": finish_reason,
                    }
                ],
            }
            data = json.dumps(payload).encode("utf-8")
            self.wfile.write(b"data: " + data + b"\n\n")
            self.wfile.flush()

        # Provider-visible reasoning: the fake returns a short
        # reasoning_content unless the no-reasoning endpoint is used, so both
        # the Think UI and the honest no-reasoning fallback can be tested.
        if include_reasoning:
            emit({"content": None, "reasoning_content": "Fake visible reasoning"})
        content = message.get("content")
        if isinstance(content, str) and content:
            emit({"content": content, "tool_calls": None})
        for call in message.get("tool_calls") or []:
            emit(
                {
                    "content": None,
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": call.get("id"),
                            "type": "function",
                            "function": call.get("function"),
                        }
                    ],
                }
            )
        finish_reason = "tool_calls" if message.get("tool_calls") else "stop"
        emit({"content": None, "tool_calls": None}, finish_reason)
        self.close_connection = True

    def _send_truncated_stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        payload = {
            "id": "chatcmpl-retry",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "fake-retry",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "content": "abandoned partial",
                        "reasoning_content": "abandoned thought",
                    },
                    "finish_reason": None,
                }
            ],
        }
        self.wfile.write(b"data: " + json.dumps(payload).encode("utf-8") + b"\n\n")
        self.wfile.flush()
        self.close_connection = True

    def _send_responses_stream(self, input_items: list[dict]) -> None:
        has_tool_output = any(
            item.get("type") == "function_call_output" for item in input_items
        )
        has_reasoning = any(item.get("type") == "reasoning" for item in input_items)
        if has_tool_output and not has_reasoning:
            self._send({"error": "missing reasoning continuation"}, 400)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        def emit(payload: dict) -> None:
            self.wfile.write(b"data: " + json.dumps(payload).encode("utf-8") + b"\n\n")
            self.wfile.flush()

        emit(
            {
                "type": "response.reasoning_summary_text.delta",
                "sequence_number": 1,
                "item_id": "rs_fake",
                "output_index": 0,
                "summary_index": 0,
                "delta": "Responses visible summary",
            }
        )
        emit(
            {
                "type": "response.output_item.done",
                "sequence_number": 2,
                "output_index": 0,
                "item": {
                    "type": "reasoning",
                    "id": "rs_fake",
                    "encrypted_content": "opaque-fake-ciphertext",
                    "summary": [
                        {
                            "type": "summary_text",
                            "text": "Responses visible summary",
                        }
                    ],
                    "status": "completed",
                },
            }
        )
        if not has_tool_output:
            emit(
                {
                    "type": "response.output_item.added",
                    "sequence_number": 2,
                    "output_index": 1,
                    "item": {
                        "type": "function_call",
                        "id": "fc_fake",
                        "call_id": "call_responses_1",
                        "name": "glob",
                        "arguments": "",
                        "status": "in_progress",
                    },
                }
            )
            emit(
                {
                    "type": "response.function_call_arguments.delta",
                    "sequence_number": 3,
                    "item_id": "fc_fake",
                    "output_index": 1,
                    "delta": '{"pattern":"**/*.py","path":"."}',
                }
            )
        else:
            emit(
                {
                    "type": "response.output_text.delta",
                    "sequence_number": 2,
                    "item_id": "msg_fake",
                    "output_index": 1,
                    "content_index": 0,
                    "delta": "Responses 闭环完成。",
                    "logprobs": [],
                }
            )
        emit(
            {
                "type": "response.completed",
                "sequence_number": 4,
                "response": {
                    "id": "resp_fake",
                    "object": "response",
                    "created_at": 0,
                    "status": "completed",
                    "error": None,
                    "incomplete_details": None,
                    "instructions": None,
                    "max_output_tokens": None,
                    "model": "fake-responses",
                    "output": [],
                    "parallel_tool_calls": True,
                    "previous_response_id": None,
                    "reasoning": {"effort": "low", "summary": "auto"},
                    "store": False,
                    "temperature": None,
                    "text": {"format": {"type": "text"}},
                    "tool_choice": "auto",
                    "tools": [],
                    "top_p": None,
                    "truncation": "disabled",
                    "usage": {
                        "input_tokens": 1,
                        "input_tokens_details": {"cached_tokens": 0},
                        "output_tokens": 1,
                        "output_tokens_details": {"reasoning_tokens": 1},
                        "total_tokens": 2,
                    },
                    "metadata": {},
                },
            }
        )
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True

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
        if self.path.endswith("/v1-responses/responses"):
            self._send_responses_stream(body.get("input") or [])
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
        if self.path.endswith("/v1-retry/chat/completions"):
            key = next(
                (
                    str(message.get("content") or "")
                    for message in messages
                    if message.get("role") == "user"
                ),
                "retry",
            )
            with _RETRY_LOCK:
                count = _RETRY_COUNTS.get(key, 0)
                _RETRY_COUNTS[key] = count + 1
            if count == 0:
                self._send_truncated_stream()
            else:
                self._send_stream(_assistant_text("Retry final answer."))
            return
        is_shot = self.path.endswith("/v1-shot/chat/completions")
        message = next_shot_response(messages) if is_shot else next_response(messages)
        include_reasoning = not self.path.endswith("/v1-no-reasoning/chat/completions")
        if body.get("stream"):
            self._send_stream(message, include_reasoning=include_reasoning)
            return
        self._send(_choice(message, "stop"))


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
