import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  testMatch: "studio-production-canary.spec.ts",
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  workers: 1,
  reporter: "line",
  outputDir: process.env.VIEWSPEC_CANARY_BROWSER_OUTPUT_DIR || "test-results/studio-production-canary",
  timeout: 5 * 60 * 1000,
  expect: { timeout: 20_000 },
  use: {
    ignoreHTTPSErrors: false,
    screenshot: "only-on-failure",
    trace: "off",
    video: "off",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "firefox", use: { ...devices["Desktop Firefox"] } },
    { name: "webkit", use: { ...devices["Desktop Safari"] } },
  ],
});
