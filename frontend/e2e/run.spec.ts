import { expect, test } from "@playwright/test";

const secret = "fake-key-value-123456";

async function fillWorkspace(page: import("@playwright/test").Page): Promise<void> {
  const workspace = process.env.E2E_WORKSPACE as string;
  const input = page.getByLabel("工作区");
  await expect(input).toBeVisible();
  await input.fill(workspace);
  await expect(page.getByText("工作区可用")).toBeVisible({ timeout: 15_000 });
}

test.describe("GUI closed loop with the Fake Model", () => {
  test("glob → grep → read → edit → verify → VERIFIED final answer", async ({ page }) => {
    await page.goto("/");
    // App loads with two seeded profiles; no onboarding is shown.
    await expect(page.getByTestId("main-page")).toBeVisible();

    await fillWorkspace(page);
    await page.getByLabel("任务描述").fill("修复 TODO 函数并用 py_compile 验证");
    await page.getByRole("button", { name: "开始运行" }).click();

    // The activity feed streams real tool events from AgentLoop.
    await expect(page.locator('[data-tool="glob"]')).toBeVisible({ timeout: 20_000 });
    await expect(page.locator('[data-tool="grep"]')).toBeVisible();
    await expect(page.locator('[data-tool="read_file"]')).toBeVisible();
    await expect(page.locator('[data-tool="edit_file"]')).toBeVisible();
    await expect(page.locator('[data-tool="run_command"]')).toBeVisible();

    // Terminal state: VERIFIED badge + final answer + counters.
    await expect(page.getByTestId("run-inspector")).toContainText("验证通过", { timeout: 30_000 });
    await expect(page.getByTestId("final-answer")).toBeVisible();
    await expect(page.getByTestId("final-answer")).toContainText("已完成：greet 已实现并通过 py_compile 验证。");
    await expect(page.getByTestId("run-inspector")).toContainText("已完成");

    // Tool count includes the verify command (5 calls).
    await expect(page.getByTestId("count-工具调用数")).toHaveText("5");
    // Changed files list shows the edited file.
    await expect(page.getByTestId("changed-files")).toContainText("hello.py");

    // Completed tools collapse into a group summary ("已完成 5 项操作") and
    // the individual cards hide behind the summary row.
    await expect(page.getByText("已完成 5 项操作")).toBeVisible();
    await expect(page.locator('[data-tool="glob"]')).not.toBeVisible();

    // 1280x720: no horizontal scrolling.
    const noHorizontalScroll = await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth
    );
    expect(noHorizontalScroll).toBe(true);

    // Secrets never reach the DOM.
    expect(await page.locator("body").innerText()).not.toContain(secret);
    const html = await page.content();
    expect(html).not.toContain(secret);
  });

  test("settings page shows credential state without echoing the secret", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("open-settings").click();
    await expect(page.getByRole("heading", { name: "模型设置" })).toBeVisible();
    const body = await page.locator("body").innerText();
    expect(body).toContain("本地假模型");
    expect(body).not.toContain(secret);
  });

  test("cancel during a slow model run: cancel stays active, terminal INTERRUPTED, restart possible", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("main-page")).toBeVisible();

    // Select the slow profile explicitly.
    await page.getByRole("combobox", { name: "模型 profile" }).click();
    await page.getByRole("option", { name: /慢速假模型/ }).click();

    await fillWorkspace(page);
    await page.getByLabel("任务描述").fill("慢速任务，验证取消");
    await page.getByRole("button", { name: "开始运行" }).click();

    // While the model request is in flight: cannot start a second run.
    await expect(page.getByTestId("run-inspector")).toContainText("运行中");
    await expect(page.getByRole("button", { name: "开始运行" })).toBeDisabled();

    await page.getByRole("button", { name: "取消运行" }).click();
    await expect(page.getByText("正在取消…")).toBeVisible();
    await expect(page.getByRole("button", { name: "取消运行" })).toBeDisabled();

    // Refresh mid-cancel: the page recovers from the snapshot/SSE and the
    // run becomes terminal INTERRUPTED.
    await page.reload();
    await expect(page.getByTestId("run-inspector")).toContainText("已取消", { timeout: 60_000 });

    // A new run can start again after the terminal state.
    await page.getByTestId("run-inspector").getByRole("button", { name: "新任务" }).click();
    await fillWorkspace(page);
    await page.getByLabel("任务描述").fill("新任务开始");
    await expect(page.getByRole("button", { name: "开始运行" })).toBeEnabled();
  });
});
