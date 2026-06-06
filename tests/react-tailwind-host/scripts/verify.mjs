import { spawn } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { existsSync, readFileSync, statSync } from "node:fs";
import { readdir, readFile, rm } from "node:fs/promises";
import { delimiter, dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const fixtureRoot = resolve(scriptDir, "..");
const repoRoot = resolve(fixtureRoot, "../..");
const fixtureRel = "tests/react-tailwind-host";
const generatedRel = `${fixtureRel}/src/generated`;
const markerPath = join(fixtureRoot, ".tmp", "host-proof-check.json");
const reportPath = join(fixtureRoot, ".tmp", "host-verify-report.json");
const npm = "npm";

function fail(code, message) {
  console.error(`${code}: ${message}`);
  process.exit(1);
}

function runCapture(command, args, cwd = repoRoot) {
  return new Promise((resolveRun) => {
    const child = spawn(command, args, { cwd, stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => (stdout += chunk));
    child.stderr.on("data", (chunk) => (stderr += chunk));
    child.on("error", (error) => fail("HOST_PROOF_CI_SOFT_GATE", error.message));
    child.on("exit", (status) => {
      if (status !== 0) fail("HOST_PROOF_CI_SOFT_GATE", stderr || `${command} exited ${status}`);
      resolveRun(stdout);
    });
  });
}

function runPhase(name, args, timeoutMs, env = {}) {
  return new Promise((resolveRun) => {
    const child = spawn(npm, args, {
      cwd: fixtureRoot,
      env: { ...process.env, ...env },
      shell: process.platform === "win32",
      stdio: "inherit",
    });
    const timer = setTimeout(() => {
      child.kill();
      fail("HOST_PROOF_TIMEOUT", `${name} exceeded ${timeoutMs}ms`);
    }, timeoutMs);
    child.on("error", (error) => {
      clearTimeout(timer);
      fail("HOST_PROOF_TIMEOUT", error.message);
    });
    child.on("exit", (status) => {
      clearTimeout(timer);
      if (status !== 0) process.exit(status ?? 1);
      resolveRun();
    });
  });
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

function runHostVerify(timeoutMs) {
  return new Promise((resolveRun) => {
    const child = spawn(
      pythonCommand(),
      [
        "-m",
        "viewspec.cli",
        "verify-host",
        join(fixtureRoot, "src", "generated"),
        "--target",
        "react-tailwind-tsx",
        "--install",
        "--json",
        "--report-out",
        reportPath,
      ],
      {
        cwd: repoRoot,
        env: pythonEnv(),
        stdio: ["ignore", "pipe", "pipe"],
      },
    );
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => {
      child.kill();
      fail("HOST_PROOF_TIMEOUT", `verify-host exceeded ${timeoutMs}ms`);
    }, timeoutMs);
    child.stdout.on("data", (chunk) => (stdout += chunk));
    child.stderr.on("data", (chunk) => (stderr += chunk));
    child.on("error", (error) => {
      clearTimeout(timer);
      fail("HOST_PROOF_CI_SOFT_GATE", error.message);
    });
    child.on("exit", (status) => {
      clearTimeout(timer);
      if (status !== 0) fail("HOST_PROOF_CI_SOFT_GATE", stderr || stdout || `verify-host exited ${status}`);
      resolveRun(stdout);
    });
  });
}

async function gitFiles() {
  const stdout = await runCapture("git", ["ls-files", "--", fixtureRel], repoRoot);
  return stdout.split(/\r?\n/).filter(Boolean).sort();
}

async function staticGuard() {
  if (!existsSync(join(fixtureRoot, "package-lock.json"))) fail("HOST_PROOF_LOCKFILE_REQUIRED", "package-lock.json is required");
  const files = await gitFiles();
  const trackedGenerated = files.filter((file) => file.startsWith(`${generatedRel}/`));
  if (trackedGenerated.length) fail("HOST_PROOF_GENERATED_ARTIFACT_TRACKED", trackedGenerated.join(", "));
  const nonLock = await physicalSourceFiles();
  if (nonLock.length > 12) fail("HOST_PROOF_FIXTURE_TOO_LARGE", `tracked non-lock fixture files: ${nonLock.length}`);
  const total = nonLock.reduce((size, file) => size + statSync(join(fixtureRoot, file)).size, 0);
  if (total > 40 * 1024) fail("HOST_PROOF_FIXTURE_TOO_LARGE", `tracked non-lock fixture source is ${total} bytes`);
  assertHostCss();
  assertImportPath();
  assertCiGate();
  assertDocs();
}

async function physicalSourceFiles(dir = fixtureRoot, prefix = "") {
  const skippedDirs = new Set([".tmp", "dist", "node_modules", "playwright-report", "test-results"]);
  const files = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const rel = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.isDirectory()) {
      if (skippedDirs.has(entry.name) || rel === "src/generated") continue;
      files.push(...(await physicalSourceFiles(join(dir, entry.name), rel)));
    } else if (entry.isFile() && entry.name !== "package-lock.json") {
      files.push(rel);
    }
  }
  return files.sort();
}

function assertHostCss() {
  const expected = [
    '@import "tailwindcss";',
    '@source "./generated/*.tsx";',
    "html,",
    "body,",
    "#root {",
    "  min-height: 100%;",
    "}",
    "body {",
    "  margin: 0;",
    "}",
  ];
  const actual = readFileSync(join(fixtureRoot, "src", "index.css"), "utf8").split(/\r?\n/).filter((line) => line.trim());
  if (actual.length > 20 || JSON.stringify(actual) !== JSON.stringify(expected)) {
    fail("HOST_PROOF_FORBIDDEN_HOST_CSS", "host CSS must be only Tailwind import/source plus root sizing/reset");
  }
}

function assertImportPath() {
  const app = readFileSync(join(fixtureRoot, "src", "App.tsx"), "utf8");
  const imports = [...app.matchAll(/from\s+["']([^"']*ViewSpecView)["']/g)].map((match) => match[1]);
  if (imports.length !== 1 || imports[0] !== "./generated/ViewSpecView") {
    fail("HOST_PROOF_IMPORT_PATH_INVALID", "host app must import exactly ./generated/ViewSpecView");
  }
}

function assertCiGate() {
  const ci = readFileSync(join(repoRoot, ".github", "workflows", "ci.yml"), "utf8");
  const job = /^  react_tailwind_host:[\s\S]*?(?=^  [A-Za-z0-9_-]+:|\s*$)/m.exec(ci)?.[0] ?? "";
  if (!job || ci.includes("continue-on-error") || /^\s+if:/m.test(job)) {
    fail("HOST_PROOF_CI_SOFT_GATE", "react_tailwind_host CI job must be mandatory and fail-closed");
  }
}

function assertDocs() {
  const docs = ["docs/free-sdk-reliability.md", "docs/known-limits-react-tailwind-tsx.md"];
  const forbidden = /\b(pixel-perfect|visual equivalence|full browser compatibility)\b/i;
  const allowedNegative = /\b(not|does not|rather than|is not|not required)\b/i;
  for (const doc of docs) {
    const lines = readFileSync(join(repoRoot, doc), "utf8").split(/\r?\n/);
    lines.forEach((line, index) => {
      if (forbidden.test(line) && !allowedNegative.test(line)) {
        fail("HOST_VERIFY_DOCS_OVERCLAIM", `${doc}:${index + 1}: ${line}`);
      }
    });
  }
}

async function assertCheckedArtifact(runToken) {
  const marker = JSON.parse(await readFile(markerPath, "utf8").catch(() => fail("HOST_PROOF_CHECK_NOT_RUN", "missing check marker")));
  if (marker.runToken !== runToken || marker.checkOk !== true) fail("HOST_PROOF_CHECK_NOT_RUN", "check marker does not match this run");
  const files = (await readdir(join(fixtureRoot, "src", "generated"))).sort();
  const expected = ["ViewSpecView.tsx", "diagnostics.json", "provenance_manifest.json"];
  if (JSON.stringify(files) !== JSON.stringify(expected)) fail("HOST_PROOF_ARTIFACT_HASH_MISMATCH", "unexpected generated files");
  const tsx = await readFile(join(fixtureRoot, "src", "generated", "ViewSpecView.tsx"));
  const manifest = JSON.parse(await readFile(join(fixtureRoot, "src", "generated", "provenance_manifest.json"), "utf8"));
  const hash = createHash("sha256").update(tsx).digest("hex");
  if (hash !== manifest.artifact_hash || hash !== marker.artifactHash) {
    fail("HOST_PROOF_ARTIFACT_HASH_MISMATCH", "checked artifact hash chain is broken");
  }
  return marker;
}

await rm(markerPath, { force: true });
await rm(reportPath, { force: true });
await staticGuard();
const runToken = randomUUID();
await runPhase("prepare", ["run", "prepare:artifact"], 30_000, { HOST_PROOF_RUN_TOKEN: runToken });
const marker = await assertCheckedArtifact(runToken);
await runHostVerify(180_000);
const report = JSON.parse(await readFile(reportPath, "utf8").catch(() => fail("HOST_PROOF_CI_SOFT_GATE", "missing verify-host report")));
if (report.ok !== true) fail("HOST_PROOF_CI_SOFT_GATE", JSON.stringify(report.errors ?? []));
if (report.artifact_hash !== marker.artifactHash) fail("HOST_PROOF_ARTIFACT_HASH_MISMATCH", "verify-host report does not match checked artifact");
if (!report.host_template_lock_hash || report.host_template_lock_hash.length !== 64) {
  fail("HOST_PROOF_ARTIFACT_HASH_MISMATCH", "verify-host report missing host template lock hash");
}
if ((report.assertions?.dom_count ?? 0) < 1 || (report.assertions?.style_assertion_count ?? 0) < 4) {
  fail("HOST_PROOF_STYLE_ASSERTION_TOO_WEAK", "verify-host report has weak DOM/style assertions");
}
