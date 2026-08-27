// Playwright global teardown: kill server children started by the setup.
import { spawnSync } from "node:child_process";

export default async function globalTeardown(): Promise<void> {
  const state = globalThis.__E2E_STATE__;
  if (!state) return;
  for (const child of [state.app, state.fake]) {
    if (child && !child.killed) {
      // On Windows kill() only terminates the direct process; /T kills the
      // whole tree (uv -> uvicorn python child, python -> fake model).
      if (process.platform === "win32") {
        spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], {
          stdio: "ignore",
        });
      } else {
        child.kill("SIGKILL");
      }
    }
  }
  globalThis.__E2E_STATE__ = undefined;
}
