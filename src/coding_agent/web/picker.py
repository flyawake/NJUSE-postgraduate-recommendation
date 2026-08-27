"""Native OS folder picker for the workspace field.

Browsers cannot reveal the absolute path of a local folder from a web page;
in the GUI the server runs on the same machine, so the reliable approach is
an OS-native "select folder" dialog hosted by the local server. The dialog
runs in a Starlette threadpool thread (the endpoint is a sync ``def``), so
the HTTP event loop is never blocked while the user browses.

The picker is injected into ``create_app`` so tests stay offline: in tests a
fake callable returns a tmp_path or None (cancelled).
"""

from __future__ import annotations

from typing import Callable, Optional

#: Per-process dialog guard: tkinter widgets are not thread-safe, so a
#: picker callback is invoked once at a time (one dialog per click anyway).
PickFolder = Callable[[], Optional[str]]


class PickerUnavailableError(RuntimeError):
    """The native dialog could not be opened on this machine."""


def pick_folder() -> Optional[str]:
    """Open the native directory dialog; return the absolute path or None.

    Raises :class:`PickerUnavailableError` when no toolkit is present
    (headless server, minimal Python install).
    """
    if _tkinter_available():
        return _pick_tkinter()
    # Best-effort fallback error, never a fabricated path.
    raise PickerUnavailableError("当前环境无法打开系统文件夹选择窗口（缺少 tkinter）")


def _tkinter_available() -> bool:
    try:
        import tkinter  # noqa: F401

        return True
    except Exception:  # ImportError or a broken Tcl/Tk install
        return False


def _pick_tkinter() -> Optional[str]:
    from tkinter import Tk, filedialog

    root = Tk()
    root.withdraw()
    try:
        try:
            root.attributes("-topmost", True)
        except Exception:  # noqa: BLE001 - cosmetic only
            pass
        selected = filedialog.askdirectory(title="选择工作区")
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001 - a destroyed root must not crash
            pass
    return str(selected) if selected else None
