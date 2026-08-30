import { expect, test } from "@playwright/test";
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const fixtureRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(fixtureRoot, "../..");
const workspacePython = join(repoRoot, ".venv", "bin", "python");
const python = process.env.PYTHON ?? (existsSync(workspacePython) ? workspacePython : "python");

function runViewspec(args: string[], cwd: string, allowFailure = false): string {
  const result = spawnSync(python, ["-m", "viewspec", ...args], {
    cwd,
    encoding: "utf8",
    env: { ...process.env, PYTHONUNBUFFERED: "1" },
    maxBuffer: 4 * 1024 * 1024,
    timeout: 60_000,
  });
  if (result.error) throw result.error;
  if (!allowFailure && result.status !== 0) {
    throw new Error(`viewspec ${args[0]} failed (${result.status}): ${result.stderr || result.stdout}`);
  }
  return result.stdout;
}

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

test("one empty-workspace command stays in one tab through failure, check, and checked product", async ({
  page,
  context,
}) => {
  const workspace = mkdtempSync(join(tmpdir(), "viewspec-studio-creation-room-"));
  const stateRoot = join(workspace, "studio-state");
  const candidatePath = join(workspace, ".viewspec", "studio-candidate.intent.json");
  const sourcePath = join(workspace, "viewspec.intent.json");
  const port = await availablePort();
  const brief = "Build a calm field dispatch dashboard for overdue jobs and crew availability.";
  const externalRequests: string[] = [];
  const runtimeFailures: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.hostname !== "127.0.0.1") externalRequests.push(request.url());
  });
  page.on("console", (message) => {
    if (message.type() === "error") runtimeFailures.push(message.text());
  });
  page.on("pageerror", (error) => runtimeFailures.push(error.message));
  try {
    const started = JSON.parse(
      runViewspec(
        [
          "studio",
          "--brief",
          brief,
          "--kind",
          "view",
          "--no-open",
          "--port",
          String(port),
          "--state-dir",
          stateRoot,
          "--json",
        ],
        workspace,
      ),
    );
    expect(started.studio.status).toBe("creating");
    expect(started.creation.headline).toBe("Waiting for agent");
    expect(started.metadata.network_calls).toBe("loopback_only");
    expect(started.metadata.reference_uploaded).toBe(false);

    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto(started.creation.url, { waitUntil: "domcontentloaded" });
    await expect(page).toHaveTitle("ViewSpec Studio · First creation");
    await expect(page.getByRole("heading", { name: "Waiting for agent" })).toBeVisible();
    await expect(page.locator("#brief")).toHaveText(brief);
    await expect(page.getByText("nothing uploaded")).toBeVisible();
    expect(context.pages()).toHaveLength(1);

    writeFileSync(candidatePath, "{}\n");
    await expect(page.getByRole("heading", { name: "Candidate needs one fix" })).toBeVisible({ timeout: 15_000 });
    await expect(page.locator("#error-code")).toHaveText("STUDIO_CREATION_CANDIDATE_INVALID");
    expect(existsSync(sourcePath)).toBe(false);

    runViewspec(["init-intent", "--out", candidatePath, "--force"], workspace);
    const candidate = JSON.parse(readFileSync(candidatePath, "utf8"));
    candidate.substrate.nodes.starter_dashboard.attrs.title = "Field Dispatch";
    candidate.substrate.nodes.revenue.attrs = { label: "Open jobs", value: "18" };
    candidate.substrate.nodes.weekly_trend.attrs = { label: "Response time", value: "12 min" };
    writeFileSync(candidatePath, `${JSON.stringify(candidate, null, 2)}\n`);

    await expect(page.getByRole("heading", { name: "Checking candidate" })).toBeVisible({ timeout: 15_000 });
    await expect(page).toHaveTitle("ViewSpec Studio", { timeout: 60_000 });
    await expect(page.getByRole("button", { name: "Comment", exact: true })).toBeVisible();
    expect(context.pages()).toHaveLength(1);
    expect(existsSync(sourcePath)).toBe(true);
    await page.setViewportSize({ width: 390, height: 844 });
    const outerOverflow = await page.locator("html").evaluate((element) => element.scrollWidth - element.clientWidth);
    expect(outerOverflow).toBeLessThanOrEqual(0);

    const receipt = JSON.parse(readFileSync(join(started.paths.proof, "room-transition.json"), "utf8"));
    expect(receipt.status).toBe("checked");
    expect(receipt.candidate_validation).toBe("passed");
    expect(receipt.artifact_check).toBe("passed");
    expect(receipt.candidate_to_checked_ms).toBeLessThan(60_000);
    expect(receipt.network_calls).toBe("none");
    expect(externalRequests).toEqual([]);
    expect(runtimeFailures).toEqual([]);
  } finally {
    if (existsSync(sourcePath)) {
      runViewspec(["review-end", sourcePath, "--state-dir", stateRoot, "--json"], workspace, true);
    }
    rmSync(workspace, { recursive: true, force: true });
  }
});
