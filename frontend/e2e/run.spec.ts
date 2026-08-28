import { expect, test } from "@playwright/test";

const secret = "E2E-SENTINEL-9f3c1";

async function fillWorkspace(
  page: import("@playwright/test").Page,
  override?: string
): Promise<void> {
  const workspace = override ?? (process.env.E2E_WORKSPACE as string);
  const input = page.getByRole("textbox", { name: "工作区" });
  await expect(input).toBeVisible();
  await input.fill(workspace);
  await expect(page.getByText("工作区可用")).toBeVisible({ timeout: 15_000 });
}

async function typeCharacters(locator: import("@playwright/test").Locator, value: string): Promise<void> {
  // `fill()` is a single synthetic update. This deliberately exercises the
  // same per-keystroke React path that a user follows.
  await locator.pressSequentially(value);
}

test.describe("GUI closed loop with the Fake Model", () => {
  test("glob → grep → read → edit → verify → VERIFIED final answer", async ({ page }) => {
    await page.goto("/");
    // App loads with two seeded profiles; no onboarding is shown.
    await expect(page.getByTestId("main-page")).toBeVisible();

    await fillWorkspace(page);
    await page.getByLabel("任务描述").fill("修复 TODO 函数并用 py_compile 验证");
    await page.getByRole("button", { name: "开始运行" }).click();

    // The default inspector stays product-oriented while the transcript
    // streams shallow tool actions.
    await expect(page.getByTestId("run-inspector")).toContainText("运行中", { timeout: 15_000 });
    await expect(page.getByTestId("run-inspector")).toContainText("尚未验证");

    // Terminal state: VERIFIED badge + final answer + counters.
    await expect(page.getByTestId("run-inspector")).toContainText("验证通过", { timeout: 30_000 });
    await expect(page.getByTestId("final-answer")).toBeVisible();
    await expect(page.getByTestId("final-answer")).toContainText("已完成：greet 已实现并通过 py_compile 验证。");
    await expect(page.getByTestId("run-inspector")).toContainText("已完成");
    await page.screenshot({ path: "feedback/task_003_evidence/success-1280x720-zh-light.png" });

    // Changed files list shows the edited file.
    await expect(page.getByTestId("changed-files")).toContainText("hello.py");

    // One shallow action row is retained per tool in original order.
    await expect(page.locator("[data-tool]")).toHaveCount(5);
    const names = await page.locator('[data-tool]').evaluateAll((nodes) =>
      nodes.map((node) => node.getAttribute("data-tool") ?? "")
    );
    expect(names).toEqual(["glob", "grep", "read_file", "edit_file", "run_command"]);
    await expect(page.getByText("实时连接中断，正在重连…")).not.toBeVisible();

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

  test("invalid setup cannot send a run request from button or shortcut", async ({ page }) => {
    let startRequests = 0;
    page.on("request", (request) => {
      if (request.method() === "POST" && new URL(request.url()).pathname === "/api/runs") {
        startRequests += 1;
      }
    });
    await page.goto("/");
    await expect(page.getByTestId("main-page")).toBeVisible();
    await page.getByTestId("locale-toggle").click();
    await page.getByRole("option", { name: "English" }).click();
    await page.getByLabel("Task").fill("must not start");

    await expect(page.getByRole("button", { name: "Start run" })).toBeDisabled();
    await page.getByLabel("Task").press("Control+Enter");
    await page.waitForTimeout(300);
    expect(startRequests).toBe(0);
  });

  test("refresh after the first tool restores all five tools in order without duplicates", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("main-page")).toBeVisible();

    // The slow profile gives a stable mid-run window for the refresh.
    await page.getByRole("combobox", { name: "模型" }).click();
    await page.getByRole("option", { name: /慢速假模型/ }).click();

    await fillWorkspace(page, process.env.E2E_WORKSPACE_FRESH as string);
    await page.getByLabel("任务描述").fill("修复 TODO 函数并用 py_compile 验证");
    await page.getByRole("button", { name: "开始运行" }).click();
    // Bootstrap may initially show the previous terminal run from another
    // page; wait for the newly started run before inspecting its first group.
    await expect(page.getByTestId("run-inspector")).toContainText("运行中", { timeout: 15_000 });

    // Wait until the first shallow action is complete while the next model
    // request is still sleeping.
    await expect(page.locator('[data-tool="glob"]')).toBeVisible({ timeout: 20_000 });
    await page.reload();

    // The snapshot/SSE recovery must restore the full, ordered event stream.
    await expect(page.getByTestId("run-inspector")).toContainText("验证通过", { timeout: 60_000 });
    await expect(page.locator("[data-tool]")).toHaveCount(5);

    const names = await page.locator('[data-tool]').evaluateAll((nodes) =>
      nodes.map((node) => node.getAttribute("data-tool") ?? "")
    );
    expect(names).toEqual(["glob", "grep", "read_file", "edit_file", "run_command"]);
    expect(new Set(names).size).toBe(5);
    expect(await page.locator("body").innerText()).not.toContain(secret);
    await expect(page.getByText("实时连接中断，正在重连…")).not.toBeVisible();
  });

  test("cancel during a slow model run: cancel stays active, terminal INTERRUPTED, restart possible", async ({ page }) => {
    let cancelRequests = 0;
    page.on("request", (request) => {
      if (request.method() === "POST" && new URL(request.url()).pathname.endsWith("/cancel")) {
        cancelRequests += 1;
      }
    });
    await page.goto("/");
    await expect(page.getByTestId("main-page")).toBeVisible();

    // Select the slow profile explicitly.
    await page.getByRole("combobox", { name: "模型" }).click();
    await page.getByRole("option", { name: /慢速假模型/ }).click();

    await fillWorkspace(page);
    await page.getByLabel("任务描述").fill("慢速任务，验证取消");
    await page.getByRole("button", { name: "开始运行" }).click();

    // While the model request is in flight the primary slot has become Stop.
    await expect(page.getByTestId("run-inspector")).toContainText("运行中");
    await expect(page.getByRole("button", { name: "开始运行" })).toHaveCount(0);
    await page.screenshot({ path: "feedback/task_003_evidence/running-1280x720-zh-light.png" });

    // Two synchronous native clicks happen before React can paint the
    // disabled state. The mutation/ref guard must still send one request.
    await page.evaluate(() => {
      const button = document.querySelector<HTMLButtonElement>('button[aria-label="取消运行"]');
      button?.click();
      button?.click();
    });
    await expect(page.getByText("正在取消…")).toBeVisible();
    await expect(page.getByRole("button", { name: "取消运行" })).toBeDisabled();
    await expect.poll(() => cancelRequests).toBe(1);

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

  test("workspace validation does not follow task, theme or profile changes", async ({ page }) => {
    let validationRequests = 0;
    page.on("request", (request) => {
      if (request.method() === "POST" && new URL(request.url()).pathname === "/api/workspace/validate") {
        validationRequests += 1;
      }
    });
    await page.goto("/");
    const newTask = page.getByTestId("run-inspector").getByRole("button", { name: "新任务" });
    if (await newTask.isVisible()) await newTask.click();
    await page.screenshot({ path: "feedback/task_003_evidence/idle-1280x720-zh-light.png" });
    await fillWorkspace(page);
    expect(validationRequests).toBe(1);

    await typeCharacters(page.getByLabel("任务描述"), "x".repeat(50));
    await page.getByTestId("theme-toggle").click();
    await page.getByRole("option", { name: "深色" }).click();
    await page.screenshot({ path: "feedback/task_003_evidence/idle-1280x720-zh-dark.png" });
    await page.getByTestId("locale-toggle").click();
    await page.getByRole("option", { name: "English" }).click();
    await page.screenshot({ path: "feedback/task_003_evidence/idle-1280x720-en-dark.png" });
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => window.innerWidth)).toBe(390);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await expect.poll(async () => (await page.getByTestId("sidebar").boundingBox())?.width).toBeLessThanOrEqual(48);
    await page.screenshot({ path: "feedback/task_003_evidence/idle-390x844-en-dark.png" });
    await page.getByRole("combobox", { name: "Model" }).click();
    await page.getByRole("option", { name: /慢速假模型/ }).click();
    await page.waitForTimeout(700);
    expect(validationRequests).toBe(1);

    await page.getByRole("combobox", { name: "Model" }).click();
    await page.getByRole("option", { name: /本地假模型/ }).click();

    await page.setViewportSize({ width: 1280, height: 720 });
    await page.getByLabel("Task").fill("SSE_STRESS");
    await page.getByRole("button", { name: "Start run" }).click();
    await expect(page.getByTestId("final-answer")).toContainText("SSE stress run completed.", { timeout: 60_000 });
    const eventTotal = await page.evaluate(async () => {
      const response = await fetch("/api/bootstrap");
      const bootstrap = await response.json() as { run?: { events_total?: number } };
      return bootstrap.run?.events_total ?? 0;
    });
    expect(eventTotal).toBeGreaterThanOrEqual(50);
    expect(validationRequests).toBe(1);
  });

  test("workspace transport error is visible, retryable and captured at 1280×720", async ({ page }) => {
    let validationRequests = 0;
    await page.route("**/api/workspace/validate", async (route) => {
      validationRequests += 1;
      if (validationRequests === 1) {
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ error: { code: "transport_error", message: "offline" } }),
        });
        return;
      }
      await route.continue();
    });
    await page.goto("/");
    await expect(page.getByTestId("main-page")).toBeVisible();
    const workspace = page.getByRole("textbox", { name: "工作区" });
    await typeCharacters(workspace, process.env.E2E_WORKSPACE as string);
    await expect(page.getByTestId("workspace-retry")).toBeVisible({ timeout: 15_000 });
    await page.screenshot({ path: "feedback/task_003_evidence/error-recovery-1280x720-zh-light.png" });

    await page.getByTestId("workspace-retry").click();
    await expect(page.getByText("工作区可用")).toBeVisible({ timeout: 15_000 });
    await page.screenshot({ path: "feedback/task_003_evidence/error-recovered-1280x720-zh-light.png" });
    expect(validationRequests).toBe(2);
  });
});
