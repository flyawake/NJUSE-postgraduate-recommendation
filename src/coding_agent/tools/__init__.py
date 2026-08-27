"""Default MVP tool set assembly."""

from __future__ import annotations

from typing import Callable, Optional

from .base import ToolSpec
from .edit_file_tool import build_edit_spec
from .glob_tool import build_glob_spec
from .grep_tool import build_grep_spec
from .observation import FileObservationTracker
from .paths import Workspace
from .read_file_tool import build_read_spec
from .registry import ToolRegistry
from .run_command_tool import build_run_spec
from .write_file_tool import build_write_spec

MVP_TOOL_NAMES = (
    "glob",
    "grep",
    "read_file",
    "write_file",
    "edit_file",
    "run_command",
)


def build_default_tools(
    workspace: Workspace,
    tracker: FileObservationTracker,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    specs: tuple[ToolSpec, ...] = (
        build_glob_spec(workspace.root),
        build_grep_spec(workspace.root),
        build_read_spec(workspace.root, tracker),
        build_write_spec(workspace.root, tracker),
        build_edit_spec(workspace.root, tracker),
        build_run_spec(workspace.root, is_cancelled),
    )
    for spec in specs:
        registry.register(spec)
    if registry.names() != MVP_TOOL_NAMES:
        raise RuntimeError(f"unexpected MVP tool set: {registry.names()}")
    return registry
