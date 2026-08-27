"""Programmatic Uvicorn runner for ``coding-agent ui``.

Global handler: the app starts loopback-only, discovers the actual port when
``--port 0`` is used, prints the URL, opens the system browser unless
``--no-browser`` was requested and shuts the active run down gracefully.
"""

from __future__ import annotations

import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional

from ..provider_config import default_home
from .app import create_app
from .controller import RunController


def static_dir() -> Path:
    return Path(__file__).resolve().parent / "static"


def build_app(
    controller: Optional[RunController] = None, session_token: Optional[str] = None
):
    resolved = controller or RunController(home=default_home())
    return create_app(
        controller=resolved,
        static_dir=static_dir(),
        session_token=session_token,
    )


def run_ui(port: int = 0, no_browser: bool = False) -> int:
    """Run the GUI server until Ctrl+C. Returns an exit code."""
    import uvicorn

    app = build_app()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="coding-agent-ui", daemon=True)
    thread.start()

    deadline = time.monotonic() + 15.0
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        print("GUI 服务启动失败（端口可能被占用或被拒绝）", flush=True)
        server.should_exit = True
        thread.join(timeout=5)
        return 1

    actual_port = _actual_port(server) or port
    url = f"http://127.0.0.1:{actual_port}/"
    print(f"Coding Agent GUI 已启动：{url}", flush=True)
    if not no_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 - browser open must never kill the server
            print("未能自动打开浏览器，请手动访问上面的地址。", flush=True)

    try:
        while thread.is_alive():
            thread.join(timeout=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    return 0


def _actual_port(server) -> Optional[int]:
    try:
        sockets = server.servers
        if sockets:
            return int(sockets[0].sockets[0].getsockname()[1])
    except Exception:  # noqa: BLE001 - best effort for tests
        return None
    return None
