import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { mkdir, readdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, delimiter, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const fixtureRoot = resolve(scriptDir, "..");
const repoRoot = resolve(fixtureRoot, "../..");
const generatedDir = join(fixtureRoot, "src", "generated");
const tmpDir = join(fixtureRoot, ".tmp");
const intentPath = join(tmpDir, "tailwind_admin_workspace.intent.json");
const markerPath = join(tmpDir, "host-proof-check.json");

function fail(code, message) {
  console.error(`${code}: ${message}`);
  process.exit(1);
}

function pythonCommand() {
  if (process.env.PYTHON) return process.env.PYTHON;
  const local = join(repoRoot, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
  return existsSync(local) ? local : "python";
}

function pythonEnv() {
  const src = join(repoRoot, "src");
  return { ...process.env, PYTHONPATH: process.env.PYTHONPATH ? `${src}${delimiter}${process.env.PYTHONPATH}` : src };
}

function run(command, args, code) {
  return new Promise((resolveRun) => {
    const child = spawn(command, args, { cwd: repoRoot, env: pythonEnv(), stdio: "inherit" });
    child.on("error", (error) => fail(code, error.message));
    child.on("exit", (status) => {
      if (status !== 0) fail(code, `${command} ${args.join(" ")} exited ${status}`);
      resolveRun();
    });
  });
}

async function sha256(path) {
  return createHash("sha256").update(await readFile(path)).digest("hex");
}

async function assertGeneratedIdentity() {
  const files = (await readdir(generatedDir)).sort();
  const expected = ["ViewSpecView.tsx", "diagnostics.json", "provenance_manifest.json"];
  if (JSON.stringify(files) !== JSON.stringify(expected)) {
    fail("HOST_PROOF_ARTIFACT_HASH_MISMATCH", `generated output must contain only ${expected.join(", ")}`);
  }
  const manifest = JSON.parse(await readFile(join(generatedDir, "provenance_manifest.json"), "utf8"));
  const artifactHash = await sha256(join(generatedDir, "ViewSpecView.tsx"));
  if (manifest.artifact_hash !== artifactHash) {
    fail("HOST_PROOF_ARTIFACT_HASH_MISMATCH", "manifest artifact_hash does not match ViewSpecView.tsx");
  }
  await writeFile(
    markerPath,
    JSON.stringify({ artifactHash, checkOk: true, runToken: process.env.HOST_PROOF_RUN_TOKEN ?? "" }, null, 2),
  );
}

try {
  await rm(generatedDir, { recursive: true, force: true });
  await rm(tmpDir, { recursive: true, force: true });
  await mkdir(generatedDir, { recursive: true });
  await mkdir(tmpDir, { recursive: true });
  if ((await readdir(generatedDir)).length !== 0) {
    fail("HOST_PROOF_STALE_ARTIFACT", "src/generated was not empty after cleanup");
  }
} catch (error) {
  fail("HOST_PROOF_STALE_ARTIFACT", error instanceof Error ? error.message : String(error));
}

const python = pythonCommand();
const writeIntent = [
  "import json, sys",
  "from pathlib import Path",
  "from viewspec.compiler_benchmarks import benchmark_fixtures",
  "fixture = next(item for item in benchmark_fixtures() if item.id == 'tailwind_admin_workspace')",
  "Path(sys.argv[1]).write_text(json.dumps(fixture.bundle.to_json(), ensure_ascii=True, sort_keys=True), encoding='utf-8')",
].join("; ");

await run(python, ["-c", writeIntent, intentPath], "HOST_PROOF_CHECK_NOT_RUN");
await run(python, ["-m", "viewspec.cli", "compile", intentPath, "--target", "react-tailwind-tsx", "--out", generatedDir], "HOST_PROOF_CHECK_NOT_RUN");
await run(python, ["-m", "viewspec.cli", "check", generatedDir, "--json"], "HOST_PROOF_CHECK_NOT_RUN");
await assertGeneratedIdentity();
