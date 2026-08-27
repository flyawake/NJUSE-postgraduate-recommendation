import { expect, test } from "@playwright/test";

const secret = "E2E-SENTINEL-9f3c1";

async function fillWorkspace(
  page: import("@playwright/test").Page,
  override?: string
): Promise<void> {
  const workspace = override ?? (process.env.E2E_WORKSPACE as string);
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

    // Running-time facts update from the event stream: step/attempt become
    // non-zero at the first step and verification has no conclusion yet.
    await expect(page.getByTestId("run-inspector")).toContainText("运行中", { timeout: 15_000 });
    await expect(page.getByTestId("count-逻辑步数")).not.toHaveText("0", { timeout: 15_000 });
    await expect(page.getByTestId("count-模型请求数")).not.toHaveText("0", { timeout: 15_000 });
    await expect(page.getByTestId("run-inspector")).toContainText("尚未验证");

    // Terminal state: VERIFIED badge + final answer + counters.
    await expect(page.getByTestId("run-inspector")).toContainText("验证通过", { timeout: 30_000 });
    await expect(page.getByTestId("final-answer")).toBeVisible();
    await expect(page.getByTestId("final-answer")).toContainText("已完成：greet 已实现并通过 py_compile 验证。");
    await expect(page.getByTestId("run-inspector")).toContainText("已完成");

    // Tool count includes the verify command (5 calls).
    await expect(page.getByTestId("count-工具调用数")).toHaveText("5");
    // Changed files list shows the edited file.
    await expect(page.getByTestId("changed-files")).toContainText("hello.py");

    // Each logical step is a separate group; no group may absorb tools from a
    // later step and render them before that step's label.
    const groupButtons = page.getByRole("button", { name: "已完成 1 项操作" });
    await expect(groupButtons).toHaveCount(5);
    await expect(page.locator('[data-tool="glob"]')).not.toBeVisible();
    for (let index = 0; index < 5; index += 1) {
      await groupButtons.nth(index).click();
    }
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
    await page.getByRole("combobox", { name: "模型 profile" }).click();
    await page.getByRole("option", { name: /慢速假模型/ }).click();

    await fillWorkspace(page, process.env.E2E_WORKSPACE_FRESH as string);
    await page.getByLabel("任务描述").fill("修复 TODO 函数并用 py_compile 验证");
    await page.getByRole("button", { name: "开始运行" }).click();
    // Bootstrap may initially show the previous terminal run from another
    // page; wait for the newly started run before inspecting its first group.
    await expect(page.getByTestId("run-inspector")).toContainText("运行中", { timeout: 15_000 });

    // Wait until the first tool has completed (its completed group collapses
    // into "已完成 1 项操作" while the next model request is still sleeping).
    await expect(page.getByRole("button", { name: "已完成 1 项操作" }).first()).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("count-逻辑步数")).toHaveText("2", { timeout: 10_000 });
    await expect(page.getByTestId("count-模型请求数")).toHaveText("2");
    await expect(page.getByTestId("count-工具调用数")).toHaveText("1");
    await expect(page.getByTestId("run-inspector")).toContainText("请求模型");
    await page.reload();

    // The snapshot/SSE recovery must restore the full, ordered event stream.
    await expect(page.getByTestId("run-inspector")).toContainText("验证通过", { timeout: 60_000 });
    const restoredGroups = page.getByRole("button", { name: "已完成 1 项操作" });
    await expect(restoredGroups).toHaveCount(5);
    for (let index = 0; index < 5; index += 1) {
      await restoredGroups.nth(index).click();
    }

    const names = await page.locator('[data-tool]').evaluateAll((nodes) =>
      nodes.map((node) => node.getAttribute("data-tool") ?? "")
    );
    expect(names).toEqual(["glob", "grep", "read_file", "edit_file", "run_command"]);
    expect(new Set(names).size).toBe(5);
    expect(await page.locator("body").innerText()).not.toContain(secret);
    await expect(page.getByText("实时连接中断，正在重连…")).not.toBeVisible();
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
