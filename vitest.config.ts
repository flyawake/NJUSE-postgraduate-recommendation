import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    include: ["frontend/src/**/*.test.{ts,tsx}"],
    setupFiles: ["frontend/src/test/setup.ts"],
    globals: false,
    restoreMocks: true,
  },
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "frontend/src"),
    },
  },
});
