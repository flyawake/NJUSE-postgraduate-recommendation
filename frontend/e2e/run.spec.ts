import { expect, test, type Locator, type Page } from "@playwright/test";
import { spawn, spawnSync, type ChildProcess } from "node:child_process";

const secret = "E2E-SENTINEL-9f3c1";

async function createConversation(
  page: Page,
  workspace: string,
  profileName?: RegExp
): Promise<void> {
  await page.getByTestId("new-conversation").click();
  const dialog = page.getByRole("dialog", { name: "新建对话" });
  await expect(dialog).toBeVisible();
  const input = dialog.getByRole("textbox", { name: "工作区" });
  await input.fill(workspace);
  await expect(dialog.getByText("工作区可用")).toBeVisible({ timeout: 15_000 });
  if (profileName) {
    await dialog.getByRole("combobox", { name: "模型" }).click();
    await page.getByRole("option", { name: profileName }).click();
  }
  await dialog.getByTestId("confirm-create-conversation").click();
  await expect(dialog).not.toBeVisible();
  await expect(page.getByTestId("conversation-task-input")).toBeVisible();
}

async function send(page: Page, message: string): Promise<void> {
  await page.getByTestId("conversation-task-input").fill(message);
  await page.getByTestId("conversation-start").click();
}

function conversationButton(page: Page, title: string): Locator {
  return page.locator('button[data-testid^="conversation-"]').filter({ hasText: title }).first();
}

function conversationRow(page: Page, title: string): Locator {
  return conversationButton(page, title).locator("..");
}

function stopProcessTree(pid: number): void {
  if (process.platform === "win32") {
    spawnSync("taskkill", ["/PID", String(pid), "/T", "/F"], { stdio: "ignore" });
  } else {
    try { process.kill(pid, "SIGKILL"); } catch { /* already stopped */ }
  }
}

async function waitForHttp(url: string, timeoutMs = 30_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      if ((await fetch(url)).ok) return;
    } catch { /* process is still starting */ }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error(`server did not restart at ${url}`);
}

test.describe("Conversation product flow with the Fake Model", () => {
  test("turn output ends with immutable changes and opens the conditional right preview", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("conversation-sidebar")).toBeVisible();
    await expect(page.getByTestId("artifact-preview-pane")).toHaveCount(0);

    await createConversation(page, process.env.E2E_WORKSPACE as string);
    await send(page, "修复 TODO 函数并用 py_compile 验证");

    await expect(page.getByTestId("final-answer")).toContainText(
      "已完成：greet 已实现并通过 py_compile 验证。",
      { timeout: 45_000 }
    );
    await expect(page.getByTestId("turn-change-summary")).toContainText("hello.py");
    await expect(page.getByTestId("turn-change-summary")).toContainText("+1/-2");
    expect(await page.locator("body").innerText()).not.toContain(secret);
    await page.screenshot({ path: "feedback/task_004_evidence/conversation-success-1280x720-zh-light.png" });

    await page.getByTestId("change-file-hello.py").click();
    const preview = page.getByTestId("artifact-preview-pane");
    await expect(preview).toBeVisible();
    await expect(preview.getByTestId("preview-title")).toHaveText("hello.py");
    await expect(preview.getByRole("tab", { name: "差异" })).toHaveAttribute("aria-selected", "true");
    await expect(preview.getByTestId("diff-viewer")).toContainText("def greet");
    await page.screenshot({ path: "feedback/task_004_evidence/file-preview-1280x720-zh-light.png" });

    await preview.getByRole("button", { name: "关闭" }).click();
    await expect(preview).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });

  test("two conversations stay isolated while one runs in the background; archive, restore and delete persist", async ({ page }) => {
    await page.goto("/");
    await createConversation(
      page,
      process.env.E2E_WORKSPACE_FRESH as string,
      /慢速假模型/
    );
    await send(page, "后台慢速任务");
    await expect(page.getByRole("button", { name: "取消运行" })).toBeVisible({ timeout: 15_000 });
    await expect(conversationRow(page, "后台慢速任务").getByLabel("运行中")).toBeVisible();

    await createConversation(page, process.env.E2E_WORKSPACE as string);
    await expect(page.getByText("还没有消息。在下方描述你希望 Agent 完成的工作。")).toBeVisible();
    await expect(conversationRow(page, "后台慢速任务").getByLabel("运行中")).toBeVisible();
    await expect(page.getByTestId("artifact-preview-pane")).toHaveCount(0);

    await conversationButton(page, "后台慢速任务").click();
    const cancel = page.getByRole("button", { name: "取消运行" });
    if (await cancel.isVisible()) await cancel.click();
    await expect(page.getByRole("button", { name: "开始运行" })).toBeVisible({ timeout: 45_000 });

    const emptyRow = conversationRow(page, "新会话");
    await emptyRow.getByRole("button", { name: "归档" }).click();
    await expect(emptyRow).toHaveCount(0);
    await page.getByRole("button", { name: "管理已归档对话" }).click();
    const archivedRow = conversationRow(page, "新会话");
    await expect(archivedRow).toBeVisible();
    await archivedRow.getByRole("button", { name: "恢复" }).click();
    await page.getByRole("button", { name: "返回当前对话" }).click();

    const restored = conversationRow(page, "新会话");
    await restored.getByRole("button", { name: "永久删除" }).click();
    const confirm = page.getByRole("alertdialog");
    await expect(confirm).toContainText("工作区中的项目文件不会被删除");
    await page.screenshot({ path: "feedback/task_004_evidence/delete-confirm-1280x720-zh-light.png" });
    await confirm.getByRole("button", { name: "永久删除" }).click();
    await expect(restored).toHaveCount(0);
  });

  test("three turns survive refresh and keep one conversation context", async ({ page }) => {
    await page.goto("/");
    await createConversation(page, process.env.E2E_WORKSPACE as string);
    let expectedTurns = 0;
    for (const message of ["第一轮追问", "第二轮追问", "第三轮追问"]) {
      await send(page, message);
      expectedTurns += 1;
      await expect(page.getByTestId("conversation-task-input")).toHaveValue("", { timeout: 15_000 });
      await expect(page.locator("article[data-testid^='turn-']")).toHaveCount(expectedTurns, { timeout: 15_000 });
      await expect(page.getByRole("button", { name: "开始运行" })).toBeVisible({ timeout: 45_000 });
    }
    await expect(page.locator("article[data-testid^='turn-']")).toHaveCount(3);
    await page.reload();
    await expect(page.locator("article[data-testid^='turn-']")).toHaveCount(3);
    await expect(page.locator("article[data-testid^='turn-']").first().getByText("第一轮追问")).toBeVisible();
    await expect(page.locator("article[data-testid^='turn-']").last().getByText("第三轮追问")).toBeVisible();
  });

  test("settings, narrow sidebar drawer, language and local privacy copy remain usable", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("open-settings").click();
    await expect(page.getByRole("heading", { name: "模型设置" })).toBeVisible();
    expect(await page.locator("body").innerText()).not.toContain(secret);

    await page.getByTestId("theme-toggle").click();
    await page.getByRole("option", { name: "深色" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await page.screenshot({ path: "feedback/task_004_evidence/settings-1280x720-zh-dark.png" });
    await page.getByTestId("theme-toggle").click();
    await page.getByRole("option", { name: "浅色" }).click();

    await page.setViewportSize({ width: 320, height: 720 });
    await page.getByTestId("locale-toggle").click();
    await page.getByRole("option", { name: "English" }).click();
    await page.getByRole("button", { name: "Expand sidebar" }).last().click();
    await expect(page.getByRole("dialog", { name: "Conversations" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: "feedback/task_004_evidence/narrow-320x720-en-light.png" });
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog", { name: "Conversations" })).not.toBeVisible();
  });

  test("streaming Think block appears and cancel leaves a clean interrupted state", async ({ page }) => {
    await page.goto("/");
    await createConversation(
      page,
      process.env.E2E_WORKSPACE_FRESH as string,
      /慢速假模型/
    );
    await send(page, "流式推理验证");
    await expect(page.getByTestId("think-block")).toBeVisible({ timeout: 15_000 });
    await page.getByTestId("think-block").getByRole("button").click();
    await expect(page.getByTestId("think-block")).toContainText("Fake visible reasoning");
    await page.screenshot({
      path: "feedback/task_005_evidence/streaming-expanded-1280x720-zh-light.png",
    });
    await expect(page.getByRole("button", { name: "取消运行" })).toBeVisible({ timeout: 15_000 });
    await page.getByRole("button", { name: "取消运行" }).click();
    await expect(page.getByRole("button", { name: "开始运行" })).toBeVisible({ timeout: 45_000 });
    // The interrupted attempt keeps its diagnostic Think/stream block but the
    // run returns to a clean startable state.
    await expect(page.getByTestId("think-block")).toHaveCount(1);
    await page.screenshot({
      path: "feedback/task_005_evidence/cancel-interrupted-1280x720-zh-light.png",
    });
  });

  test("no-reasoning provider renders honest fallback without a Think block", async ({ page }) => {
    await page.goto("/");
    await createConversation(page, process.env.E2E_WORKSPACE_FRESH as string, /无推理假模型/);
    await send(page, "无推理回退验证");
    await expect(page.getByTestId("final-answer")).toContainText(
      "已完成：greet 已实现并通过 py_compile 验证。",
      { timeout: 45_000 }
    );
    await expect(page.getByTestId("think-block")).toHaveCount(0);
    await page.screenshot({
      path: "feedback/task_005_evidence/no-reasoning-1280x720-zh-light.png",
    });
  });

  test("partial provider failure freezes the abandoned attempt before a clean retry", async ({ page }) => {
    await page.goto("/");
    await createConversation(page, process.env.E2E_WORKSPACE_FRESH as string, /重试假模型/);
    await send(page, "部分流失败重试验证");
    await expect(page.getByTestId("final-answer")).toContainText("Retry final answer.", {
      timeout: 45_000,
    });
    await expect(page.getByText("该次流式尝试已放弃").first()).toBeVisible();
    await expect(page.getByTestId("think-block")).toHaveCount(2);
    await page.screenshot({
      path: "feedback/task_005_evidence/retry-abandoned-1280x720-zh-light.png",
    });
  });

  test("Responses adapter completes reasoning, tool output continuation and final text", async ({ page }) => {
    await page.goto("/");
    await createConversation(page, process.env.E2E_WORKSPACE_FRESH as string, /Responses 假模型/);
    await send(page, "Responses 工具闭环验证");
    await expect(page.getByTestId("final-answer")).toContainText("Responses 闭环完成。", {
      timeout: 45_000,
    });
    const thinkBlocks = page.getByTestId("think-block");
    await expect(thinkBlocks).toHaveCount(2);
    await thinkBlocks.last().getByRole("button").click();
    await expect(thinkBlocks.last()).toContainText("Responses visible summary");
    expect(await page.locator("body").innerText()).not.toContain("opaque-fake-ciphertext");
    await page.screenshot({
      path: "feedback/task_005_evidence/responses-expanded-1280x720-zh-light.png",
    });
  });

  test("busy composer queues three messages and survives reload/cancel", async ({ page }) => {
    await page.goto("/");
    await createConversation(page, process.env.E2E_WORKSPACE_FRESH as string, /慢速假模型/);
    await send(page, "第一个正在运行的任务");
    await expect(page.getByRole("button", { name: "取消运行" })).toBeVisible({ timeout: 15_000 });

    const textarea = page.getByTestId("conversation-task-input");
    for (const message of ["队列一", "队列二", "队列三"]) {
      await textarea.fill(message);
      await textarea.press("Enter");
      await expect(textarea).toHaveValue("", { timeout: 15_000 });
    }
    await expect(page.getByTestId("queue-dock")).toBeVisible();
    await expect(page.locator("[data-testid^='queue-item-']")).toHaveCount(3);
    await textarea.fill("插入当前轮的补充说明");
    await page.getByTestId("conversation-steer").click();
    await expect(page.getByTestId("steer-caption")).toBeVisible({ timeout: 15_000 });
    await textarea.fill("继续输入时的排队草稿");
    await expect(page.getByTestId("conversation-queue")).toBeVisible();
    await expect(page.getByTestId("conversation-steer")).toBeVisible();
    await expect(page.getByRole("button", { name: "取消运行" })).toBeVisible();
    await page.screenshot({
      path: "feedback/task_006_evidence/busy-queue-1280x720-zh.png",
      // Keep the evidence focused on queue/steer controls. The app header can
      // contain a temporary absolute workspace path, which must not be stored
      // in a committed acceptance artifact.
      clip: { x: 192, y: 356, width: 1000, height: 364 },
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.getByTestId("queue-dock")).toBeVisible();
    await page.screenshot({
      path: "feedback/task_006_evidence/busy-queue-390x844-zh.png",
      clip: { x: 0, y: 490, width: 390, height: 354 },
    });
    await page.reload();
    await expect(page.getByTestId("queue-dock")).toBeVisible();
    await expect(page.locator("[data-testid^='queue-item-']")).toHaveCount(3);
    await page.getByRole("button", { name: "取消运行" }).click();
    // After cancel the queue consumer should immediately claim the first
    // queued item and start the next turn; the other two remain in the dock.
    await expect(page.getByRole("button", { name: "取消运行" })).toBeVisible({ timeout: 45_000 });
    await expect(page.getByTestId("queue-dock")).toBeVisible();
    await expect
      .poll(async () => page.locator("[data-testid^='queue-item-']").count(), {
        timeout: 45_000,
      })
      .toBe(2);
  });

  test("memory center saves a fact, recalls it in a new conversation, and deletes it", async ({ page }) => {
    await page.goto("/");
    await createConversation(page, process.env.E2E_WORKSPACE as string);
    const sourceConversation = new URL(page.url()).searchParams.get("conversation");
    await page.getByTestId("conversation-task-input").fill("/remember 项目使用 FastAPI 和 React");
    await page.getByTestId("conversation-start").click();
    const rememberDialog = page.getByRole("dialog", { name: "保存为记忆" });
    await rememberDialog.getByLabel("标题").fill("项目栈");
    await rememberDialog.getByRole("button", { name: "记住" }).click();
    await expect(rememberDialog).not.toBeVisible();

    await createConversation(page, process.env.E2E_WORKSPACE as string);
    await send(page, "项目技术栈是什么？");
    // Usage is recorded when the request projection is built, before the
    // model turn necessarily releases its workspace lease.  Wait for the
    // terminal answer before navigating to the source and starting another
    // conversation in the same workspace.
    await expect(page.getByTestId("final-answer")).toContainText("记忆召回", {
      timeout: 45_000,
    });
    const usage = page.getByTestId("memory-usage-summary");
    await expect(usage).toBeVisible({ timeout: 45_000 });
    await expect(usage).toContainText("项目栈");
    await usage.getByRole("button", { name: /来源/ }).click();
    await expect.poll(() => new URL(page.url()).searchParams.get("conversation")).toBe(sourceConversation);

    await page.getByTestId("nav-memories").click();
    const row = page.locator("[data-testid^='memory-']").filter({ hasText: "项目栈" }).first();
    await row.getByRole("button", { name: "删除" }).click();
    const deleteDialog = page.getByRole("dialog", { name: "删除" });
    await deleteDialog.getByRole("button", { name: "删除" }).click();
    await expect(page.getByText("项目使用 FastAPI 和 React")).toHaveCount(0);

    await createConversation(page, process.env.E2E_WORKSPACE as string);
    await send(page, "项目技术栈是什么？");
    await expect(page.getByTestId("final-answer")).toContainText("未使用已删除的记忆", {
      timeout: 45_000,
    });
    await expect(page.getByTestId("memory-usage-summary")).toHaveCount(0);
  });

  test("composer /remember opens the save dialog without starting an agent turn", async ({ page }) => {
    await page.goto("/");
    await createConversation(page, process.env.E2E_WORKSPACE_FRESH as string);
    const textarea = page.getByTestId("conversation-task-input");
    await textarea.fill("/remember 项目使用 FastAPI");
    await page.getByTestId("conversation-start").click();
    const dialog = page.getByRole("dialog", { name: "保存为记忆" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByLabel("内容")).toHaveValue("项目使用 FastAPI");
    await dialog.getByRole("button", { name: "记住" }).click();
    await expect(dialog).not.toBeVisible();
    await expect(page.getByTestId("conversation-task-input")).toHaveValue("");
    await expect(page.getByTestId("final-answer")).toHaveCount(0);
  });

  test("a hard server restart recovers the active turn as interrupted without replay", async ({ page }) => {
    test.setTimeout(90_000);
    await page.goto("/");
    await createConversation(
      page,
      process.env.E2E_WORKSPACE_RESTART as string,
      /慢速假模型/
    );
    await send(page, "重启恢复验证");
    await expect(page.getByRole("button", { name: "取消运行" })).toBeVisible({ timeout: 45_000 });

    const oldPid = Number(process.env.E2E_APP_PID);
    const port = process.env.E2E_APP_PORT as string;
    const home = process.env.E2E_AGENT_HOME as string;
    stopProcessTree(oldPid);
    let restarted: ChildProcess | null = null;
    try {
      restarted = spawn(
        "uv",
        ["run", "coding-agent", "ui", "--port", port, "--no-browser"],
        {
          cwd: process.cwd(),
          env: { ...process.env, CODING_AGENT_HOME: home },
          stdio: ["ignore", "pipe", "pipe"],
          shell: false,
        }
      );
      await waitForHttp(`http://127.0.0.1:${port}/api/health`);
      await page.reload();
      await expect(page.getByRole("button", { name: "开始运行" })).toBeVisible({ timeout: 20_000 });
      await expect(page.getByText("本轮任务已停止")).toBeVisible();
      await expect(page.getByTestId("final-answer")).toHaveCount(0);
      await page.screenshot({ path: "feedback/task_004_evidence/restart-interrupted-1280x720-zh-light.png" });
    } finally {
      if (restarted?.pid) stopProcessTree(restarted.pid);
    }
  });
});
