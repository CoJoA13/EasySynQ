import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.EASYSYNQ_LIVE_BASE_URL;
if (!baseURL) {
  throw new Error("EASYSYNQ_LIVE_BASE_URL is required");
}

export default defineConfig({
  testDir: "./e2e-live",
  testMatch: "*.spec.ts",
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: "line",
  use: {
    baseURL,
    trace: "off",
    screenshot: "off",
    video: "off",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], browserName: "chromium" },
    },
  ],
});
