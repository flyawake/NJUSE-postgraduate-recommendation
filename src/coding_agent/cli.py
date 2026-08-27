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
from .config import (
    Config,
    load_config,
    load_config_from_connection,
    resolve_connection,
)
from .context import ContextManager
from .credentials import CredentialError, CredentialService
from .errors import ConfigError
from .model_client import OpenAIModelClient
from .models import AgentEvent, RunStatus, VerificationStatus
from .provider_config import ProfileError, ProfileStore, default_home
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
    parser.add_argument(
        "--profile",
        default=None,
        help="Profile ID from the user-level profile store; overrides the active profile and env fallback.",
    )
    parser.add_argument("task", help="The programming task for the agent.")
    return parser


def build_ui_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="coding-agent ui",
        description="Start the local Coding Agent GUI (loopback only).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Port to listen on (default 0 = pick a free port).",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the system browser automatically.",
    )
    return parser


def build_config_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="coding-agent config",
        description="Inspect the user-level profile store (read-only).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="List saved profiles and their credential state.")
    show = sub.add_parser("show", help="Show one profile descriptor.")
    show.add_argument("profile_id", help="Profile ID to show.")
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


def _resolve_config(args) -> Config:
    """Legacy CLI: explicit profile > active profile > legacy OPENAI_* env.

    ``--model``/``--base-url`` only override this run and never write back.
    """
    workspace = args.workspace
    if args.profile:
        home = default_home()
        store = ProfileStore(home)
        credentials = CredentialService(home)
        profiles = store.load().profiles
        connection = resolve_connection(
            profiles=profiles,
            active_profile=store.load().active_profile,
            explicit_profile=args.profile,
            env=dict(__import__("os").environ),
            credential_resolver=credentials.resolve,
        )
        return load_config_from_connection(
            workspace,
            connection,
            model=args.model,
            base_url=args.base_url,
            max_steps=args.max_steps,
        )
    return load_config(
        workspace=workspace,
        model=args.model,
        base_url=args.base_url,
        max_steps=args.max_steps,
    )


def _run_config_command(args) -> int:
    home = default_home()
    store = ProfileStore(home)
    credentials = CredentialService(home)
    try:
        config = store.load()
    except (ProfileError, ConfigError) as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return EXIT_ERROR
    if args.command == "list":
        profiles = sorted(config.profiles.values(), key=lambda p: p.id)
        if not profiles:
            print("没有已保存的 profile。请运行 `coding-agent ui` 在设置页创建。")
            return EXIT_OK
        for profile in profiles:
            info = (
                credentials.info(profile.credential_ref)
                if profile.credential_ref
                else None
            )
            active = " *" if profile.id == config.active_profile else ""
            credential_state = "未配置"
            if info is not None:
                if not info.configured:
                    credential_state = "缺凭据"
                elif info.source == "env":
                    credential_state = "环境变量（只读）"
                else:
                    credential_state = "本地（可写）"
            print(
                f"{profile.id:<32} {profile.provider_id:<10} "
                f"{profile.model:<28} {credential_state}{active}"
            )
        return EXIT_OK
    if args.command == "show":
        profile = config.profiles.get(args.profile_id)
        if profile is None:
            print(f"profile 不存在：{args.profile_id}", file=sys.stderr)
            return EXIT_ERROR
        info = (
            credentials.info(profile.credential_ref) if profile.credential_ref else None
        )
        descriptor = profile.to_dict()
        descriptor["credential"] = (
            {
                "configured": info.configured,
                "source": info.source,
                "writable": info.writable,
            }
            if info
            else {"configured": False, "source": None, "writable": True}
        )
        descriptor["active"] = profile.id == config.active_profile
        print(__import__("json").dumps(descriptor, ensure_ascii=False, indent=2))
        return EXIT_OK
    return EXIT_ERROR


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    agent_factory: Optional[AgentFactory] = None,
) -> int:
    argv_list = list(sys.argv[1:] if argv is None else argv)
    if argv_list and argv_list[0] in ("ui", "config"):
        if argv_list[0] == "ui":
            from .web.server import run_ui

            args = build_ui_parser().parse_args(argv_list[1:])
            return run_ui(port=args.port, no_browser=args.no_browser)
        args = build_config_parser().parse_args(argv_list[1:])
        return _run_config_command(args)
    parser = build_parser()
    args = parser.parse_args(argv_list)
    try:
        config = _resolve_config(args)
    except (ConfigError, ProfileError, CredentialError) as exc:
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
