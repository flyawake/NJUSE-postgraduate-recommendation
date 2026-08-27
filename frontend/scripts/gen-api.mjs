// Regenerate frontend API types from the backend OpenAPI schema.
// Outputs (committed, so consumers of the repo need no Python to typecheck):
//   frontend/src/api/schema.json   schema snapshot
//   frontend/src/api/schema.d.ts   generated TypeScript types
import { spawnSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

export function regenerate(targetDir) {
  const outDir = targetDir ?? path.join(root, "frontend", "src", "api");
  mkdirSync(outDir, { recursive: true });
  const dump = spawnSync("uv", ["run", "python", "-m", "coding_agent.web.openapi_json"], {
    cwd: root,
    encoding: "utf-8",
    env: { ...process.env, PYTHONIOENCODING: "utf-8" },
  });
  if (dump.status !== 0) {
    throw new Error(`openapi dump failed: ${dump.stderr || dump.stdout}`);
  }
  const schemaPath = path.join(outDir, "schema.json");
  writeFileSync(schemaPath, dump.stdout, "utf-8");

  const gen = spawnSync(
    process.execPath,
    [
      path.join(root, "node_modules", "openapi-typescript", "bin", "cli.js"),
      schemaPath,
      "-o",
      path.join(outDir, "schema.d.ts"),
    ],
    { cwd: root, encoding: "utf-8" }
  );
  if (gen.status !== 0) {
    throw new Error(`openapi-typescript failed: ${gen.stderr || gen.stdout}`);
  }
  return outDir;
}

if (process.argv[1] && import.meta.url.endsWith(path.basename(process.argv[1]))) {
  const dir = regenerate();
  console.log(`API types regenerated into ${dir}`);
}
