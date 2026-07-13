import { defineConfig, devices } from "@playwright/test";

type VerificationViewport = {
  name: string;
  width: number;
  height: number;
};

function verificationViewports(): VerificationViewport[] | null {
  const raw = process.env.VIEWSPEC_HOST_VERIFY_PLAN_JSON;
  if (!raw) return null;
  const parsed = JSON.parse(raw) as { viewports?: VerificationViewport[] };
  if (!Array.isArray(parsed.viewports) || parsed.viewports.length < 1) {
    throw new Error("HOST_VERIFY_PROOF_REPORT_INVALID: verification plan has no viewports");
  }
  return parsed.viewports;
}

const plannedViewports = verificationViewports();
const projects = plannedViewports
  ? plannedViewports.map((viewport) => ({
      name: viewport.name,
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: viewport.width, height: viewport.height },
      },
    }))
  : [
      {
        name: "chromium",
        use: {
          ...devices["Desktop Chrome"],
          viewport: { width: 1280, height: 900 },
        },
      },
    ];

export default defineConfig({
  testDir: "./tests",
  timeout: 30_000,
  expect: {
    timeout: 5_000,
  },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.VIEWSPEC_HOST_VERIFY_BASE_URL ?? "http://127.0.0.1:4177",
    trace: "retain-on-failure",
  },
  projects,
});
