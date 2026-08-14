import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: "harness-fail-closed.probe.spec.ts",
  outputDir: "../test-results/probe",
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: "json",
  use: {
    baseURL: "http://127.0.0.1:4174",
    screenshot: "off",
    trace: "off",
    video: "off",
  },
  projects: [
    {
      name: "chromium-probe",
      use: { ...devices["Desktop Chrome"], browserName: "chromium" },
    },
  ],
});
