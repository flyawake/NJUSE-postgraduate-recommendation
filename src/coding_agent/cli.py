"""Command line entry point.

The CLI is the only component that prints. It owns argparse, environment
config, exit codes and Ctrl+C handling; all agent logic lives in AgentLoop.
"""

from __future__ import annotations

import argparse
import sys
import threading
from typing import Any, Callable, Dict, Optional, Sequence

from .agent import AgentLoop
from .completion import CompletionPolicy
from .config import Config, load_config
from .context import ContextManager
from .errors import ConfigError
from .model_client import OpenAIModelClient
from .models import AgentEvent, RunStatus, VerificationStatus
from .tools import build_default_tools
from .tools.executor import ToolExecutor
from .tools.observation import FileObservationTracker
from .tools.paths import Workspace
from .tools.policy import WorkspaceToolPolicy

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INTERRUPTED = 130
MAX_PRINTED_TEXT = 4_000

AgentFactory = Callable[[Config, Callable[[], bool], "ConsoleEventSink"], AgentLoop]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="coding-agent",
        description=(
            "Run a local coding agent inside a trusted workspace. The agent "
            "reads files, edits them and runs verification commands through a "
            "local tool loop; it is not a security sandbox."
        ),
    )
    parser.add_argument(
        "--workspace",
        required=True,
        help="Workspace directory the agent may access (required).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="OpenAI-compatible model name; overrides OPENAI_MODEL.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="OpenAI-compatible base URL; overrides OPENAI_BASE_URL.",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help="Maximum logical model steps (1-50, default 20).",
    )
    parser.add_argument("task", help="The programming task for the agent.")
    return parser


class ConsoleEventSink:
    """Redacting console renderer for structured events.

    Payloads come from AgentLoop already summarized; this sink only adds a
    final width cap so one tool result can never flood the terminal.
    """

    _LABELS = {
        "run_started": "run started",
        "step_started": "model request",
        "model_retry": "model retry",
        "assistant_received": "assistant received",
        "tool_started": "tool started",
        "tool_finished": "tool finished",
        "completion_deferred": "completion deferred",
        "run_finished": "run finished",
    }

    def emit(self, event: AgentEvent) -> None:
        label = self._LABELS.get(event.type, event.type)
        detail = self._format_payload(event)
        line = f"[step {event.step}] {label}"
        if detail:
            line += f" - {detail}"
        print(line[:400])

    @staticmethod
    def _format_payload(event: AgentEvent) -> str:
        payload = event.payload
        if event.type == "step_started":
            return (
                f"projected {payload.get('char_count')}/{payload.get('budget')} chars"
            )
        if event.type == "model_retry":
            return (
                f"attempt {payload.get('attempt')} failed, "
                f"retrying as attempt {payload.get('next_attempt')}: "
                f"{payload.get('reason', '')}"
            )
        if event.type == "assistant_received":
            return (
                f"text={payload.get('text_chars')} chars, "
                f"tool_calls={payload.get('tool_call_count')}"
            )
        if event.type == "tool_started":
            return f"{payload.get('name')}({payload.get('arguments', '')})"
        if event.type == "tool_finished":
            return str(payload.get("summary", ""))
        if event.type == "completion_deferred":
            return f"verification={payload.get('verification_status')}"
        if event.type == "run_finished":
            return (
                f"{payload.get('status')}/{payload.get('stop_reason')} "
                f"verification={payload.get('verification_status')}"
            )
        if event.type == "run_started":
            return f"task_chars={payload.get('task_chars')}"
        return ""


def _default_agent_factory(
    config: Config,
    is_cancelled: Callable[[], bool],
    event_sink: ConsoleEventSink,
) -> AgentLoop:
    workspace = Workspace(config.workspace)
    tracker = FileObservationTracker()
    registry = build_default_tools(workspace, tracker, is_cancelled)
    executor = ToolExecutor(registry, WorkspaceToolPolicy(), is_cancelled)
    model_client = OpenAIModelClient(
        api_key=config.api_key,
        model=config.model,
        base_url=config.base_url,
    )
    return AgentLoop(
        model_client=model_client,
        tool_registry=registry,
        tool_executor=executor,
        context_manager=ContextManager(config.char_budget),
        completion_policy=CompletionPolicy(),
        event_sink=event_sink,
        max_steps=config.max_steps,
        is_cancelled=is_cancelled,
    )


def _run_with_keyboard_interrupt(
    loop: AgentLoop, task: str, cancel_event: threading.Event
) -> Optional[Any]:
    box: Dict[str, Any] = {}

    def target() -> None:
        box["result"] = loop.run(task)

    thread = threading.Thread(target=target, name="coding-agent-loop", daemon=True)
    thread.start()
    try:
        while thread.is_alive():
            thread.join(timeout=0.2)
    except KeyboardInterrupt:
        cancel_event.set()
        try:
            thread.join(timeout=30)
        except KeyboardInterrupt:
            print("已强制中断，正在退出。", file=sys.stderr)
            return None
    return box.get("result")


def _print_result(result) -> None:
    print()
    print(
        f"验证状态：{result.verification_status.value}"
        f"（变更文件：{len(result.mutated_paths)} 个）"
    )
    if result.final_text:
        text = result.final_text
        if len(text) > MAX_PRINTED_TEXT:
            text = (
                text[:MAX_PRINTED_TEXT]
                + f"\n…（已截断，共 {len(result.final_text)} 字符）"
            )
        print(f"最终答复：\n{text}")
    if (
        result.status is RunStatus.SUCCESS
        and result.mutated_paths
        and result.verification_status
        in (VerificationStatus.FAILED, VerificationStatus.NOT_RUN)
    ):
        print(
            "⚠ 完成但未验证/验证失败：修改已写入，但没有成功的 verify 命令支撑。",
            file=sys.stderr,
        )
    if result.status is RunStatus.ERROR:
        detail = result.details.get("reason") or result.details.get("message") or ""
        print(
            f"运行失败：{result.stop_reason.value}"
            + (f"（{detail}）" if detail else ""),
            file=sys.stderr,
        )


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    agent_factory: Optional[AgentFactory] = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = load_config(
            workspace=args.workspace,
            model=args.model,
            base_url=args.base_url,
            max_steps=args.max_steps,
        )
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return EXIT_ERROR

    cancel_event = threading.Event()
    sink = ConsoleEventSink()
    factory = agent_factory or _default_agent_factory
    try:
        loop = factory(config, cancel_event.is_set, sink)
    except Exception as exc:
        print(f"初始化失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR

    result = _run_with_keyboard_interrupt(loop, args.task, cancel_event)
    if result is None:
        return EXIT_INTERRUPTED
    _print_result(result)
    if result.status is RunStatus.INTERRUPTED:
        return EXIT_INTERRUPTED
    if result.status is RunStatus.ERROR:
        return EXIT_ERROR
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
