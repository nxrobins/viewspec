import { expect, test } from "@playwright/test";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createInterface } from "node:readline";

const fixtureRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(fixtureRoot, "../..");
const workspacePython = join(repoRoot, ".venv", "bin", "python");
const python = process.env.PYTHON ?? (existsSync(workspacePython) ? workspacePython : "python");

let workspace = "";
let studioUrl = "";
let eventsPath = "";
let serverProcess: ChildProcessWithoutNullStreams | null = null;

async function availablePort(): Promise<number> {
  return await new Promise((resolvePort, rejectPort) => {
    const server = createServer();
    server.once("error", rejectPort);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (address === null || typeof address === "string") {
        server.close();
        rejectPort(new Error("Could not allocate a loopback port."));
        return;
      }
      server.close((error) => (error ? rejectPort(error) : resolvePort(address.port)));
    });
  });
}

function readEvents(): string[] {
  if (!existsSync(eventsPath)) return [];
  return readFileSync(eventsPath, "utf8")
    .trim()
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line).kind);
}

test.beforeAll(async () => {
  workspace = mkdtempSync(join(tmpdir(), "viewspec-studio-share-browser-"));
  const port = await availablePort();
  serverProcess = spawn(
    python,
    [join(repoRoot, "tests", "studio_share_browser_server.py"), "--root", workspace, "--port", String(port)],
    {
      cwd: repoRoot,
      env: { ...process.env, PYTHONUNBUFFERED: "1" },
      stdio: ["pipe", "pipe", "pipe"],
    },
  );
  const ready = await new Promise<{ url: string; events: string }>((resolveReady, rejectReady) => {
    const lines = createInterface({ input: serverProcess!.stdout });
    const timer = setTimeout(() => rejectReady(new Error("Attested Studio Share server did not become ready.")), 60_000);
    lines.once("line", (line) => {
      clearTimeout(timer);
      try {
        resolveReady(JSON.parse(line));
      } catch (error) {
        rejectReady(error);
      }
    });
    serverProcess!.once("exit", (code) => rejectReady(new Error(`Attested Studio Share server exited ${code}.`)));
  });
  studioUrl = ready.url;
  eventsPath = ready.events;
});

test.afterAll(async () => {
  if (serverProcess !== null && serverProcess.exitCode === null) {
    const exited = new Promise<void>((resolveExit) => serverProcess!.once("exit", () => resolveExit()));
    serverProcess.kill("SIGTERM");
    await Promise.race([exited, new Promise<void>((resolveWait) => setTimeout(resolveWait, 5_000))]);
  }
  rmSync(workspace, { recursive: true, force: true });
});

test("attested Share prepares before and publishes only after exact confirmation", async ({ page }) => {
  const runtimeFailures: string[] = [];
  page.on("pageerror", (error) => runtimeFailures.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") runtimeFailures.push(message.text());
  });

  await page.goto(studioUrl);
  await expect(page.locator("#status")).toHaveText("Checked target pair ready", { timeout: 20_000 });
  const share = page.getByRole("button", { name: "Share", exact: true });
  await expect(share).toBeVisible();
  expect(await page.content()).not.toContain("VIEWSPEC_STUDIO_API_KEY");
  expect(readEvents()).toEqual([]);

  await share.click();
  await expect(page.locator("#share-panel")).toBeVisible();
  await expect(page.locator("#share-summary")).toContainText("Nothing has been uploaded");
  await expect(page.locator("#share-leaving")).toContainText("exact semantic source");
  await expect(page.locator("#share-staying")).toContainText("environment variables");
  await expect(page.locator("#share-create")).toBeDisabled();
  expect(readEvents()).toEqual(["prepare"]);

  await page.locator("#share-confirm").check();
  await expect(page.locator("#share-create")).toBeEnabled();
  await page.locator("#share-expiry").selectOption("3600");
  await page.locator("#share-create").click();
  await expect(page.locator("#share-result")).toBeVisible();
  await expect(page.locator("#share-reviewer-link")).toHaveValue(/#cap=vsc_/);
  await expect(page.locator("#share-owner")).toHaveAttribute("href", /#cap=vsc_/);
  expect(readEvents()).toEqual(["prepare", "publish"]);

  await page.reload();
  await expect(page.locator("#status")).toHaveText("Checked target pair ready", { timeout: 20_000 });
  expect(await page.content()).not.toContain("#cap=vsc_");
  expect(runtimeFailures).toEqual([]);
});
