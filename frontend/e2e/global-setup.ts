// Playwright global setup: starts the fake model server and the real
// backend (production build served from web/static), then seeds one
// fast profile and one slow profile through the public API.
//
// Everything lives under temp dirs; no credential of the developer machine is
// used and nothing is written into the repository.
import { spawn, type ChildProcess } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

export interface E2EState {
  app: ChildProcess;
  fake: ChildProcess;
  home: string;
  workspace: string;
  baseUrl: string;
}

declare global {
  var __E2E_STATE__: E2EState | undefined;
}

async function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (typeof address === "object" && address) {
        const port = address.port;
        server.close(() => resolve(port));
      } else {
        server.close(() => reject(new Error("no port")));
      }
    });
  });
}

async function waitForHttp(url: string, timeoutMs = 30_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let lastError: unknown = null;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error(`waitForHttp(${url}) timed out: ${String(lastError)}`);
}

async function createProfile(
  baseUrl: string,
  token: string,
  input: {
    display_name: string;
    base_url: string;
    model: string;
    credential_ref: string;
    secret: string;
    provider_id?: string;
    wire_api?: string;
    reasoning_mode?: string;
  }
): Promise<string> {
  const headers = {
    "Content-Type": "application/json",
    "X-Coding-Agent-Token": token,
  };
  const created = await fetch(`${baseUrl}/api/profiles`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      provider_id: input.provider_id ?? "custom",
      display_name: input.display_name,
      base_url: input.base_url,
      model: input.model,
      credential_ref: input.credential_ref,
      wire_api: input.wire_api ?? "openai_chat_completions",
      reasoning_mode: input.reasoning_mode ?? "auto",
    }),
  });
  if (!created.ok) {
    throw new Error(`create profile failed: ${created.status} ${await created.text()}`);
  }
  const profile = (await created.json()) as { id: string };
  const credential = await fetch(`${baseUrl}/api/profiles/${profile.id}/credential`, {
    method: "PUT",
    headers,
    body: JSON.stringify({ secret: input.secret }),
  });
  if (!credential.ok) {
    throw new Error(`set credential failed: ${credential.status} ${await credential.text()}`);
  }
  return profile.id;
}

export default async function globalSetup(): Promise<() => Promise<void>> {
  const fakePort = await freePort();
  const appPort = await freePort();
  const home = mkdtempSync(path.join(os.tmpdir(), "ca-e2e-home-"));
  const workspace = mkdtempSync(path.join(os.tmpdir(), "ca-e2e-ws-"));
  writeFileSync(
    path.join(workspace, "hello.py"),
    "def greet(name):\n    # TODO: return greeting\n    pass\n",
    "utf-8"
  );
  // A pristine workspace for tests that run after the closed-loop test has
  // already edited the shared workspace.
  const freshWorkspace = mkdtempSync(path.join(os.tmpdir(), "ca-e2e-ws-fresh-"));
  writeFileSync(
    path.join(freshWorkspace, "hello.py"),
    "def greet(name):\n    # TODO: return greeting\n    pass\n",
    "utf-8"
  );

  const fake = spawn("python", ["-u", path.join(root, "frontend", "e2e", "fake_model_server.py"), "--port", String(fakePort)], {
    cwd: root,
    stdio: ["ignore", "pipe", "pipe"],
    shell: false,
  });
  await waitForHttp(`http://127.0.0.1:${fakePort}/health`);

  const app = spawn(
    "uv",
    ["run", "coding-agent", "ui", "--port", String(appPort), "--no-browser"],
    {
      cwd: root,
      env: { ...process.env, CODING_AGENT_HOME: home },
      stdio: ["ignore", "pipe", "pipe"],
      shell: false,
    }
  );

  const baseUrl = `http://127.0.0.1:${appPort}`;
  await waitForHttp(`${baseUrl}/api/health`);

  const bootstrap = await (await fetch(`${baseUrl}/api/bootstrap`)).json() as {
    session_token: string;
  };
  const secret = "E2E-SENTINEL-9f3c1";
  await createProfile(baseUrl, bootstrap.session_token, {
    display_name: "本地假模型",
    base_url: `http://127.0.0.1:${fakePort}/v1`,
    model: "fake-model",
    credential_ref: "fake",
    secret,
  });
  await createProfile(baseUrl, bootstrap.session_token, {
    display_name: "重试假模型",
    base_url: `http://127.0.0.1:${fakePort}/v1-retry`,
    model: "fake-model-retry",
    credential_ref: "fake-retry",
    secret,
  });
  await createProfile(baseUrl, bootstrap.session_token, {
    display_name: "Responses 假模型",
    base_url: `http://127.0.0.1:${fakePort}/v1-responses`,
    model: "fake-model-responses",
    credential_ref: "fake-responses",
    secret,
    provider_id: "openai",
    wire_api: "openai_responses",
    reasoning_mode: "visible",
  });
  await createProfile(baseUrl, bootstrap.session_token, {
    display_name: "慢速假模型",
    base_url: `http://127.0.0.1:${fakePort}/v1-slow`,
    model: "fake-model-slow",
    credential_ref: "fake-slow",
    secret,
  });
  await createProfile(baseUrl, bootstrap.session_token, {
    display_name: "无推理假模型",
    base_url: `http://127.0.0.1:${fakePort}/v1-no-reasoning`,
    model: "fake-model-no-reasoning",
    credential_ref: "fake-no-reasoning",
    secret,
  });

  globalThis.__E2E_STATE__ = {
    app,
    fake,
    home,
    workspace,
    baseUrl,
  };
  process.env.E2E_BASE_URL = baseUrl;
  process.env.E2E_WORKSPACE = workspace;
  process.env.E2E_WORKSPACE_FRESH = freshWorkspace;
  process.env.E2E_APP_PID = String(app.pid);
  process.env.E2E_APP_PORT = String(appPort);
  process.env.E2E_AGENT_HOME = home;

  console.log(`[e2e] backend=${baseUrl} fake=http://127.0.0.1:${fakePort} workspace=${workspace}`);

  return async () => {
    for (const child of [app, fake]) {
      if (child && !child.killed) {
        if (process.platform === "win32") {
          spawn("taskkill", ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore" });
        } else {
          child.kill("SIGKILL");
        }
      }
    }
  };
}
