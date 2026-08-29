import { expect, test, type Page } from "@playwright/test";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const fixtureRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(fixtureRoot, "../..");
const workspacePython = join(repoRoot, ".venv", "bin", "python");
const python = process.env.PYTHON ?? (existsSync(workspacePython) ? workspacePython : "python");

let workspace = "";
let sourcePath = "";
let reviewState = "";
let convergenceState = "";
let studioUrl = "";
let studioPort = 0;
let creationReadyMs = 0;

const canonicalViewports = [
  { option: "mobile", width: 390, height: 844 },
  { option: "tablet", width: 768, height: 1024 },
  { option: "desktop", width: 1440, height: 1000 },
] as const;

function runViewspec(args: string[], allowFailure = false, cwd = repoRoot): string {
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

function currentSourceSha256(): string {
  return createHash("sha256").update(readFileSync(sourcePath)).digest("hex");
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

type ReviewEvent = {
  event_id: string;
  body: string;
  target: {
    screen_id: string | null;
    source_ref: string;
    binding_id: string | null;
    action_id: string | null;
    intent_refs: string[];
    content_refs: string[];
  };
};

type ProposalSpec = {
  operation: Record<string, unknown>;
  requestFields?: Record<string, unknown>;
};

function writeConvergenceInputs(
  index: number,
  batch: { review_id: string; batch_id: string },
  event: ReviewEvent,
  proposal: ProposalSpec,
): { contextPath: string; patchPath: string } {
  const source = readFileSync(sourcePath, "utf8");
  const baseSourceSha256 = createHash("sha256").update(source).digest("hex");
  const evidenceRefs = [
    `review:${batch.review_id}:${batch.batch_id}`,
    `review_event:${event.event_id}`,
  ];
  const contextPath = join(workspace, `converge-context-${index}.json`);
  const patchPath = join(workspace, `converge-patch-${index}.json`);
  writeFileSync(
    contextPath,
    JSON.stringify(
      {
        origin: "review_batch",
        source_kind: "app_bundle",
        base_source_sha256: baseSourceSha256,
        contract_profile: "local_v1",
        evidence_refs: evidenceRefs,
        requests: [
          {
            request_id: event.event_id,
            kind: "change_request",
            instruction: event.body,
            screen_id: event.target.screen_id,
            source_ref: event.target.source_ref,
            binding_id: event.target.binding_id,
            action_id: event.target.action_id,
            intent_refs: event.target.intent_refs,
            content_refs: event.target.content_refs,
            ...proposal.requestFields,
          },
        ],
      },
      null,
      2,
    ),
  );
  writeFileSync(
    patchPath,
    JSON.stringify(
      {
        schema_version: 1,
        contract_profile: "local_v1",
        source_kind: "app_bundle",
        base_source_sha256: baseSourceSha256,
        operations: [proposal.operation],
        evidence_refs: evidenceRefs,
      },
      null,
      2,
    ),
  );
  return { contextPath, patchPath };
}

function submitConvergenceProposal(
  index: number,
  batch: { review_id: string; batch_id: string },
  event: ReviewEvent,
  proposal: ProposalSpec,
): void {
  const { contextPath, patchPath } = writeConvergenceInputs(index, batch, event, proposal);
  runViewspec(["converge-start", sourcePath, contextPath, "--state-dir", convergenceState, "--json"]);
  const submitted = JSON.parse(
    runViewspec(["converge-submit", sourcePath, patchPath, "--state-dir", convergenceState, "--json"]),
  );
  expect(submitted.convergence.status).toBe("awaiting_approval");
}

function acknowledgeReviewBatch(batchId: string, reply: string): void {
  runViewspec([
    "review-poll",
    sourcePath,
    "--state-dir",
    reviewState,
    "--ack",
    batchId,
    "--agent-reply",
    reply,
    "--timeout-ms",
    "1",
    "--json",
  ]);
}

function captureRuntimeFailures(page: Page): string[] {
  const failures: string[] = [];
  page.on("pageerror", (error) => failures.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") failures.push(`console: ${message.text()}`);
  });
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (url.hostname === "127.0.0.1" && Number(url.port) === studioPort && response.status() >= 400) {
      failures.push(`HTTP ${response.status()}: ${url.pathname}`);
    }
  });
  return failures;
}

async function measuredFrameViewport(page: Page, selector: string): Promise<{ width: number; height: number }> {
  return await page
    .frameLocator(selector)
    .locator("html")
    .evaluate((element) => ({
      width: element.ownerDocument.documentElement.clientWidth,
      height: element.ownerDocument.documentElement.clientHeight,
    }));
}

async function openDetails(page: Page): Promise<void> {
  const toggle = page.getByRole("button", { name: "Details", exact: true });
  if ((await toggle.getAttribute("aria-expanded")) !== "true") await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator("#studio-panel")).toHaveAttribute("aria-hidden", "false");
}

async function waitForRevision(page: Page, revision: number): Promise<number> {
  const startedAt = Date.now();
  await expect
    .poll(
      () => {
        const status = JSON.parse(
          runViewspec(["review-status", sourcePath, "--state-dir", reviewState, "--json"]),
        );
        if (status.review?.source_failure) {
          throw new Error(`Studio comparison rebuild failed: ${JSON.stringify(status.review.source_failure)}`);
        }
        return status.review?.revision;
      },
      { timeout: 30_000 },
    )
    .toBe(revision);
  await expect(page.locator(".brand small")).toContainText(`revision ${revision}`, { timeout: 30_000 });
  await expect(page.locator("#status")).toHaveText("Checked target pair ready");
  return Date.now() - startedAt;
}

async function assertTargetPairAtEveryViewport(
  page: Page,
  assertion: (frameSelector: "#artifact" | "#artifact-react") => Promise<void>,
): Promise<void> {
  for (const viewport of canonicalViewports) {
    await page.getByLabel("Canvas", { exact: true }).selectOption(viewport.option);
    await expect
      .poll(() => page.locator("#fit-shell").getAttribute("data-fit-scale"))
      .not.toBeNull();
    await expect
      .poll(() =>
        page.locator(".canvas").evaluate((element) => element.scrollWidth - element.clientWidth),
      )
      .toBeLessThanOrEqual(1);
    await expect(page.locator(".compare-stage")).toHaveCSS(
      "display",
      viewport.option === "mobile" ? "flex" : "grid",
    );
    await expect(page.locator("#coherence-card")).toHaveAttribute("data-status", /^(aligned|mismatch)$/);
    await expect(page.locator("#coherence-summary")).toContainText(
      `at ${viewport.option[0].toUpperCase()}${viewport.option.slice(1)}`,
    );
    await expect
      .poll(() => page.locator(".canvas").evaluate((element) => element.scrollTop))
      .toBe(0);
    for (const frameSelector of ["#artifact", "#artifact-react"] as const) {
      await expect.poll(() => measuredFrameViewport(page, frameSelector)).toEqual({
        width: viewport.width,
        height: viewport.height,
      });
      const overflow = await page
        .frameLocator(frameSelector)
        .locator("html")
        .evaluate((element) => element.scrollWidth - element.clientWidth);
      expect(overflow).toBeLessThanOrEqual(0);
      await assertion(frameSelector);
    }
  }
}

async function assertStableInteractionContract(page: Page, queueHeading: string): Promise<void> {
  await page.getByLabel("Canvas", { exact: true }).selectOption("desktop");
  await page.frameLocator("#artifact-react").getByRole("button", { name: "Incident", exact: true }).click();
  await expect(page.frameLocator("#artifact-react").getByRole("heading", { name: "Job detail" })).toBeVisible();
  await expect(page.frameLocator("#artifact").getByRole("heading", { name: "Job detail" })).toBeVisible();
  await openDetails(page);
  await page.getByRole("combobox", { name: "Preview surface" }).selectOption("html-tailwind-app");
  await expect(page.locator(".target-frame[data-studio-surface='html-tailwind-app']")).toHaveAttribute(
    "data-studio-surface-active",
    "true",
  );
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await page.frameLocator("#artifact").getByRole("button", { name: "Queue", exact: true }).click();
  await expect(page.frameLocator("#artifact").getByRole("heading", { name: queueHeading })).toBeVisible();
  await expect(page.frameLocator("#artifact-react").getByRole("heading", { name: queueHeading })).toBeVisible();
  await openDetails(page);
  await page.locator("#replay-checkpoint").selectOption("triage_replay:1");
  await expect(page.locator("#replay-proof")).toContainText("Proved result");
  await page.getByRole("button", { name: "Show", exact: true }).click();
  await expect(page.locator("#status")).toHaveText("Replay checkpoint · both targets applied");
  await expect(page.locator("#replay-proof")).toContainText("Checkpoint active");
}

test.beforeAll(async () => {
  const creationStartedAt = Date.now();
  workspace = mkdtempSync(join(tmpdir(), "viewspec-studio-browser-"));
  sourcePath = join(workspace, "viewspec.app.json");
  reviewState = join(workspace, "review-state");
  convergenceState = join(workspace, "convergence-state");
  studioPort = await availablePort();

  const briefPath = join(workspace, "product-brief.md");
  const referencePath = join(workspace, "reference.png");
  const referenceBytes = Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
    "base64",
  );
  writeFileSync(
    briefPath,
    "Build a fast field-dispatch dashboard. Prioritize overdue jobs, response time, and crew availability.\n",
  );
  writeFileSync(referencePath, referenceBytes);

  const prepared = JSON.parse(
    runViewspec(
      [
        "studio-create",
        "--brief-file",
        "product-brief.md",
        "--reference",
        "reference.png",
        "--kind",
        "app",
        "--json",
      ],
      false,
      workspace,
    ),
  );
  expect(prepared.creation.source_path).toBe("viewspec.app.json");
  expect(prepared.metadata.network_calls).toBe("none");
  expect(prepared.metadata.reference_uploaded).toBe(false);
  expect(prepared.creation.reference.sha256).toBe(createHash("sha256").update(referenceBytes).digest("hex"));
  expect(existsSync(sourcePath)).toBe(false);

  const candidatePath = join(workspace, prepared.creation.candidate_path);
  runViewspec(["init-app", "--out", candidatePath, "--template", "react-app"], false, workspace);
  const candidate = JSON.parse(readFileSync(candidatePath, "utf8"));
  candidate.app.id = "field_dispatch";
  candidate.app.title = "Field Dispatch";
  const queue = candidate.screens.find((screen: { id: string }) => screen.id === "queue");
  const detail = candidate.screens.find((screen: { id: string }) => screen.id === "detail");
  queue.title = "Active jobs";
  queue.intent_bundle.substrate.nodes.incident_queue.attrs.title = "Active jobs";
  detail.title = "Job detail";
  detail.intent_bundle.substrate.nodes.incident_detail.attrs.title = "Job detail";
  writeFileSync(candidatePath, `${JSON.stringify(candidate, null, 2)}\n`);

  const accepted = JSON.parse(runViewspec(["studio-accept", "--json"], false, workspace));
  expect(accepted.creation.status).toBe("source_ready");
  expect(accepted.creation.candidate_validation).toBe("passed");
  expect(accepted.creation.artifact_check).toBe("passed");
  expect(accepted.creation.network_calls).toBe("none");
  expect(accepted.creation.reference_fidelity).toBe("not_proven");
  expect(accepted.creation.reference_sha256).toBe(prepared.creation.reference.sha256);
  expect(existsSync(sourcePath)).toBe(true);

  const started = JSON.parse(
    runViewspec([
      "studio",
      sourcePath,
      "--no-open",
      "--port",
      String(studioPort),
      "--state-dir",
      reviewState,
      "--convergence-state-dir",
      convergenceState,
      "--compare",
      "--install",
      "--json",
    ]),
  );
  expect(started.studio.ready_ms).toBeLessThan(60_000);
  expect(started.studio.primary_loop).toEqual(["preview", "comment", "approve"]);
  expect(started.studio.comparison).toEqual({
    status: "ready",
    targets: ["html-tailwind-app", "react-tailwind-app"],
    synchronized: ["viewport", "route", "semantic_identity"],
    visual_parity: "not_proven",
    dependency_install: "explicit_opt_in",
    inspection: {
      coherence_status: "browser_observed",
      coherence_contract: "semantic_geometry_v1",
      state_status: "ready",
      replay_count: 1,
      resource_status: "ready",
      resource_assertion_count: 9,
      production_data: "not_claimed",
    },
  });
  creationReadyMs = Date.now() - creationStartedAt;
  studioUrl = started.review.url;
});

test.afterAll(() => {
  if (sourcePath && reviewState) {
    runViewspec(["review-end", sourcePath, "--state-dir", reviewState, "--json"], true);
  }
  if (workspace) rmSync(workspace, { recursive: true, force: true });
});

test("Studio carries a brief through three synchronized static/React semantic changes", async ({ page }, testInfo) => {
  const journeyStartedAt = Date.now();
  const changeEvidence: Array<Record<string, unknown>> = [];
  const initialSourceSha256 = currentSourceSha256();
  const failures = captureRuntimeFailures(page);
  await page.setViewportSize({ width: 1280, height: 720 });
  const response = await page.goto(studioUrl, { waitUntil: "domcontentloaded" });
  expect(response?.status()).toBe(200);
  await expect(page).toHaveTitle("ViewSpec Studio");
  await expect(page.locator("#status")).toHaveText("Checked target pair ready");
  await expect(page.locator(".compare-stage[data-studio-comparison=true]")).toBeVisible();
  await expect(page.getByRole("button", { name: "Details", exact: true })).toHaveAttribute("aria-expanded", "false");
  await expect(page.locator("#studio-panel")).toHaveAttribute("aria-hidden", "true");
  await expect(page.getByText("Point. Ask. Approve.", { exact: true })).toBeHidden();
  await openDetails(page);
  await expect(page.getByText("Point. Ask. Approve.", { exact: true })).toBeVisible();
  await expect(page.locator("#inspection")).toBeVisible();
  await expect(page.locator("#inspection-summary")).toHaveText("1 checked replay · 9 checked fixture fields");
  await page.keyboard.press("Escape");
  await expect(page.locator("#studio-panel")).toHaveAttribute("aria-hidden", "true");
  await expect(page.getByRole("button", { name: "Details", exact: true })).toBeFocused();

  for (const viewport of canonicalViewports) {
    await page.getByLabel("Canvas", { exact: true }).selectOption(viewport.option);
    await expect.poll(() => measuredFrameViewport(page, "#artifact")).toEqual({ width: viewport.width, height: viewport.height });
    await expect.poll(() => measuredFrameViewport(page, "#artifact-react")).toEqual({ width: viewport.width, height: viewport.height });
  }

  await expect(page.locator("#coherence-card")).toHaveAttribute("data-status", "aligned");
  await expect(page.locator("#coherence-summary")).toHaveText("Static + React align at Desktop");
  await expect
    .poll(async () => Number(await page.locator("#fit-shell").getAttribute("data-fit-scale")))
    .toBeGreaterThanOrEqual(0.84);
  await expect
    .poll(() => page.locator(".canvas").evaluate((element) => element.scrollWidth - element.clientWidth))
    .toBeLessThanOrEqual(1);
  await page.waitForTimeout(800);
  expect(await page.locator(".canvas").evaluate((element) => element.scrollTop)).toBe(0);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await expect
    .poll(async () => Number(await page.locator("#fit-shell").getAttribute("data-fit-scale")))
    .toBeGreaterThanOrEqual(0.95);
  const semanticTarget = page.frameLocator("#artifact-react").locator("[data-binding-id='inc_1042_status']");
  await expect(semanticTarget).toBeVisible();
  const expectedIrId = await semanticTarget.getAttribute("data-ir-id");
  expect(expectedIrId).toBeTruthy();
  await semanticTarget.evaluate((element) => {
    (element as HTMLElement).style.transform = "translateX(160px)";
  });
  await openDetails(page);
  await page.getByRole("button", { name: "Recheck targets", exact: true }).click();
  await expect(page.locator("#coherence-card")).toHaveAttribute("data-status", "mismatch");
  await expect(page.locator("#coherence-summary")).toHaveText("Targets differ at Desktop");
  const mismatchDetail = await page.locator("#coherence-detail").innerText();
  expect(mismatchDetail).toContain("in React");
  await page.getByRole("button", { name: "Review this", exact: true }).click();
  await expect(page.locator("#selection-title")).toHaveText("inc_1042 · Status");
  await expect(page.locator("#selection-detail")).toContainText("Matched across Static + React");
  await expect(
    page
      .frameLocator("#artifact")
      .locator("[data-resource-view-id='queue_incidents'][data-binding-id='inc_1042_status']"),
  ).toHaveAttribute("data-viewspec-review-selected", "true");
  await expect(semanticTarget).toHaveAttribute("data-viewspec-review-selected", "true");
  await expect(page.locator("#resource-card")).toContainText("incidents → inc_1042 → status");
  await expect(page.locator("#resource-card")).toContainText("investigating");
  await expect(page.getByRole("heading", { name: "Ask for one change" })).toBeVisible();
  await expect(page.getByLabel("What should be different?", { exact: true })).toHaveValue(
    `Make this consistent across Static and React. ${mismatchDetail}`,
  );
  await page.getByLabel("What should be different?", { exact: true }).fill("Make this result unmistakable.");
  await page.getByRole("button", { name: "Send to agent", exact: true }).click();
  const firstCommentAt = Date.now();
  await expect(page.locator("#queued")).toHaveText("1");

  const polled = JSON.parse(
    runViewspec(["review-poll", sourcePath, "--state-dir", reviewState, "--timeout-ms", "1", "--json"]),
  );
  const event = polled.batch.events[0];
  expect(event.body).toBe("Make this result unmistakable.");
  expect(event.context.viewport).toEqual({ name: "desktop", width: 1440, height: 1000 });
  expect(event.context.route).toBe("/");
  expect(event.context.screen_id).toBe("queue");
  expect(event.context.evidence_refs).toEqual(["studio-inspection/resources/incidents/inc_1042/status"]);
  expect(event.target.target_resolution).toBe("exact");
  expect(event.target.ir_id).toBe(expectedIrId);

  submitConvergenceProposal(1, polled.batch, event, {
    operation: {
      op: "set_binding_presentation",
      screen_id: "queue",
      binding_id: "inc_1042_status",
      old_value: "value",
      value: "badge",
    },
  });
  acknowledgeReviewBatch(polled.batch.batch_id, "A checked badge proposal is ready for approval.");
  const decision = page.locator("#convergence");
  await expect(decision).toBeVisible();
  await expect(decision).toContainText("Before");
  await expect(decision).toContainText("After");
  const firstProposalMs = Date.now() - firstCommentAt;
  await page.getByRole("button", { name: "Approve change", exact: true }).click();
  await expect(decision).toBeHidden();
  await expect.poll(() => {
    const source = JSON.parse(readFileSync(sourcePath, "utf8"));
    const queueScreen = source.screens.find((screen: { id: string }) => screen.id === "queue");
    return queueScreen.intent_bundle.view_spec.bindings.find(
      (binding: { id: string }) => binding.id === "inc_1042_status",
    )?.present_as;
  }).toBe("badge");
  const firstApprovalMs = await waitForRevision(page, 2);
  await expect(page.locator("#coherence-card")).toHaveAttribute("data-status", "aligned");
  await expect(page.locator("#coherence-summary")).toContainText("Static + React align");
  await assertStableInteractionContract(page, "Active jobs");
  await assertTargetPairAtEveryViewport(page, async (frameSelector) => {
    const badge = page
      .frameLocator(frameSelector)
      .locator("[data-resource-view-id='queue_incidents'][data-binding-id='inc_1042_status']");
    await expect(badge).toBeVisible();
    const borderRadius = await badge.evaluate((element) => getComputedStyle(element).borderRadius);
    expect(borderRadius).not.toBe("0px");
  });
  changeEvidence.push({
    index: 1,
    operation: "set_binding_presentation",
    target: "queue/inc_1042_status",
    before: "value",
    after: "badge",
    source_sha256: currentSourceSha256(),
    proposal_ms: firstProposalMs,
    approval_to_revision_ms: firstApprovalMs,
    revision: 2,
    viewports: [390, 768, 1440],
    targets: ["html-tailwind-app", "react-tailwind-app"],
    coherence_detector: mismatchDetail,
    coherence_recovered: true,
  });

  await page.getByRole("button", { name: "Comment", exact: true }).click();
  const secondTarget = page
    .frameLocator("#artifact-react")
    .locator("[data-resource-view-id='queue_incidents'][data-binding-id='inc_1043_severity']");
  await expect(secondTarget).toBeVisible();
  await secondTarget.click();
  await expect(page.getByRole("heading", { name: "Ask for one change" })).toBeVisible();
  await page.getByLabel("What should be different?", { exact: true }).fill("Raise this incident severity to high.");
  await page.getByRole("button", { name: "Send to agent", exact: true }).click();
  const secondCommentAt = Date.now();
  const secondPolled = JSON.parse(
    runViewspec(["review-poll", sourcePath, "--state-dir", reviewState, "--timeout-ms", "1", "--json"]),
  );
  const secondEvent = secondPolled.batch.events[0] as ReviewEvent;
  expect(secondEvent.body).toBe("Raise this incident severity to high.");
  expect(secondEvent.target.binding_id).toBe("inc_1043_severity");
  submitConvergenceProposal(2, secondPolled.batch, secondEvent, {
    requestFields: { resource_id: "incidents", record_id: "inc_1043", field: "severity" },
    operation: {
      op: "replace_fixture_scalar",
      resource_id: "incidents",
      record_id: "inc_1043",
      field: "severity",
      old_value: "medium",
      value: "high",
    },
  });
  acknowledgeReviewBatch(secondPolled.batch.batch_id, "A checked severity proposal is ready for approval.");
  await expect(decision).toBeVisible();
  await expect(decision).toContainText("medium");
  await expect(decision).toContainText("high");
  const secondProposalMs = Date.now() - secondCommentAt;
  await page.getByRole("button", { name: "Approve change", exact: true }).click();
  await expect(decision).toBeHidden();
  const secondApprovalMs = await waitForRevision(page, 3);
  expect(
    JSON.parse(readFileSync(sourcePath, "utf8")).resources.find(
      (resource: { id: string }) => resource.id === "incidents",
    ).records.find((record: { id: string }) => record.id === "inc_1043").severity,
  ).toBe("high");
  await assertStableInteractionContract(page, "Active jobs");
  await assertTargetPairAtEveryViewport(page, async (frameSelector) => {
    await expect(
      page
        .frameLocator(frameSelector)
        .locator("[data-resource-view-id='queue_incidents'][data-binding-id='inc_1043_severity']"),
    ).toHaveText("high");
  });
  changeEvidence.push({
    index: 2,
    operation: "replace_fixture_scalar",
    target: "incidents/inc_1043/severity",
    before: "medium",
    after: "high",
    source_sha256: currentSourceSha256(),
    proposal_ms: secondProposalMs,
    approval_to_revision_ms: secondApprovalMs,
    revision: 3,
    viewports: [390, 768, 1440],
    targets: ["html-tailwind-app", "react-tailwind-app"],
  });

  await page.getByRole("button", { name: "Comment", exact: true }).click();
  const thirdTarget = page.frameLocator("#artifact-react").getByRole("heading", { name: "Active jobs" });
  await expect(thirdTarget).toBeVisible();
  await thirdTarget.click();
  await expect(page.getByRole("heading", { name: "Ask for one change" })).toBeVisible();
  await page.getByLabel("What should be different?", { exact: true }).fill("Rename this queue heading to Priority jobs.");
  await page.getByRole("button", { name: "Send to agent", exact: true }).click();
  const thirdCommentAt = Date.now();
  const thirdPolled = JSON.parse(
    runViewspec(["review-poll", sourcePath, "--state-dir", reviewState, "--timeout-ms", "1", "--json"]),
  );
  const thirdEvent = thirdPolled.batch.events[0] as ReviewEvent;
  expect(thirdEvent.body).toBe("Rename this queue heading to Priority jobs.");
  expect(thirdEvent.target.content_refs).toContain("node:incident_queue#attr:title");
  submitConvergenceProposal(3, thirdPolled.batch, thirdEvent, {
    operation: {
      op: "replace_semantic_attr",
      screen_id: "queue",
      node_id: "incident_queue",
      attr: "title",
      old_value: "Active jobs",
      value: "Priority jobs",
    },
  });
  acknowledgeReviewBatch(thirdPolled.batch.batch_id, "A checked heading proposal is ready for approval.");
  await expect(decision).toBeVisible();
  await expect(decision).toContainText("Active jobs");
  await expect(decision).toContainText("Priority jobs");
  const thirdProposalMs = Date.now() - thirdCommentAt;
  await page.getByRole("button", { name: "Approve change", exact: true }).click();
  await expect(decision).toBeHidden();
  const thirdApprovalMs = await waitForRevision(page, 4);
  await assertStableInteractionContract(page, "Priority jobs");
  await assertTargetPairAtEveryViewport(page, async (frameSelector) => {
    await expect(page.frameLocator(frameSelector).getByRole("heading", { name: "Priority jobs" })).toBeVisible();
  });
  changeEvidence.push({
    index: 3,
    operation: "replace_semantic_attr",
    target: "queue/incident_queue/title",
    before: "Active jobs",
    after: "Priority jobs",
    source_sha256: currentSourceSha256(),
    proposal_ms: thirdProposalMs,
    approval_to_revision_ms: thirdApprovalMs,
    revision: 4,
    viewports: [390, 768, 1440],
    targets: ["html-tailwind-app", "react-tailwind-app"],
  });

  expect(creationReadyMs).toBeLessThan(60_000);
  const evidence = {
    schema_version: 1,
    status: "passed",
    journey: "brief-to-three-approved-semantic-changes",
    initial_source_sha256: initialSourceSha256,
    final_source_sha256: currentSourceSha256(),
    creation_ready_ms: creationReadyMs,
    studio_journey_ms: Date.now() - journeyStartedAt,
    change_count: changeEvidence.length,
    changes: changeEvidence,
    generated_output_edits: 0,
    static_react_target_pass_rate: 1,
    responsive_viewports: [390, 768, 1440],
    human_desirability: "not_measured",
    private_review: "separately_proven",
    runtime_failures: failures,
  };
  const evidencePath = testInfo.outputPath("studio-product-journey-evidence.json");
  writeFileSync(evidencePath, `${JSON.stringify(evidence, null, 2)}\n`);
  await testInfo.attach("studio-product-journey-evidence", {
    path: evidencePath,
    contentType: "application/json",
  });
  expect(failures).toEqual([]);
});
