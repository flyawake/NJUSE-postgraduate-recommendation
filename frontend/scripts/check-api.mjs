// Verify that regenerating API types produces no diff (acceptance A17).
// Exits non-zero when the committed schema.json/schema.d.ts are stale.
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { regenerate } from "./gen-api.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const committed = path.join(root, "frontend", "src", "api");
const temp = mkdtempSync(path.join(os.tmpdir(), "coding-agent-api-check-"));

try {
  regenerate(temp);
  let failed = false;
  for (const name of ["schema.json", "schema.d.ts"]) {
    const a = readFileSync(path.join(temp, name), "utf-8");
    const b = readFileSync(path.join(committed, name), "utf-8");
    if (a !== b) {
      failed = true;
      console.error(`DIFF: ${name} is stale. Run "npm run gen:api" and commit the result.`);
    }
  }
  if (failed) {
    process.exit(1);
  }
  console.log("API types are up to date (no diff).");
} finally {
  rmSync(temp, { recursive: true, force: true });
}
