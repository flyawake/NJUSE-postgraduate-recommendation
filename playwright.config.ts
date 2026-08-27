import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./frontend/e2e",
  globalSetup: "./frontend/e2e/global-setup.ts",
  globalTeardown: "./frontend/e2e/global-teardown.ts",
  timeout: 90_000,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL,
    viewport: { width: 1280, height: 720 },
    locale: "zh-CN",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
