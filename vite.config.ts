import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  root: path.resolve(import.meta.dirname, "frontend"),
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "frontend/src"),
    },
  },
  build: {
    outDir: path.resolve(import.meta.dirname, "src/coding_agent/web/static"),
    emptyOutDir: true,
    sourcemap: false,
    target: "es2020",
  },
  server: {
    port: 5173,
    strictPort: false,
  },
});
