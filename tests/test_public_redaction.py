from __future__ import annotations

import pytest

from coding_agent.public_redaction import (
    PUBLIC_TOOL_TARGET_MAX_CHARS,
    bound_public_tool_target,
    public_tool_target,
    redact_public_run_result,
)


@pytest.mark.parametrize(
    ("tool_name", "args", "prefix"),
    [
        ("read_file", {"path": "src/" + "x" * 10_000}, "src/"),
        ("write_file", {"path": "out/" + "y" * 10_000}, "out/"),
        ("grep", {"path": ".", "pattern": "needle" * 2_000}, "needle"),
        ("glob", {"path": ".", "pattern": "**/" + "z" * 10_000}, "**/"),
    ],
)
def test_public_tool_target_is_semantic_and_bounded(
    tool_name: str, args: dict, prefix: str
) -> None:
    target = public_tool_target(tool_name, args)
    assert target is not None
    assert target.startswith(prefix)
    assert len(target) == PUBLIC_TOOL_TARGET_MAX_CHARS
    assert target.endswith("…")


def test_public_tool_target_removes_controls_and_command_operands() -> None:
    sentinel = "SECRET-SENTINEL"
    assert bound_public_tool_target("src/a.py\n\x00spoofed") == "src/a.py spoofed"
    target = public_tool_target(
        "run_command",
        {
            "argv": [
                "C:\\Python\\python.exe",
                f"--token={sentinel}",
                sentinel,
            ]
        },
    )
    assert target == "python.exe"
    assert sentinel not in target


def test_search_target_prefers_pattern_over_workspace_dot() -> None:
    assert public_tool_target("glob", {"path": ".", "pattern": "**/*.py"}) == "**/*.py"
    assert public_tool_target("grep", {"path": ".", "pattern": "TODO"}) == "TODO"


def test_public_run_result_drops_internal_details_fail_closed() -> None:
    result = redact_public_run_result(
        {
            "status": "ERROR",
            "stop_reason": "INTERNAL_ERROR",
            "details": {"message": "C:/secret/path?token=SECRET"},
            "future_internal_field": "SECRET",
        }
    )
    assert result == {"status": "ERROR", "stop_reason": "INTERNAL_ERROR"}
