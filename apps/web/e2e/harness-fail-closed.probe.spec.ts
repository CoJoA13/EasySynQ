import { expect, test as base } from "@playwright/test";
import type { Page } from "@playwright/test";
import { installRegisterApi } from "./support/api";

async function observeRequestFailure(page: Page, url: string, errorText: string): Promise<void> {
  const failedRequest = page.waitForEvent("requestfailed", async (request) => {
    if (request.url() !== url) return false;
    if (process.env.EASYSYNQ_PROBE_DELAY_REQUESTFAILED === "1") {
      // Force observation behind the real interceptor fatal, and prove that ordering occurred.
      await new Promise((resolve) => setTimeout(resolve, 500));
      expect(test.info().status).toBe("failed");
    }
    return true;
  });
  const fetchResult = page.evaluate(async (target) => {
    try {
      await fetch(target);
      return "resolved";
    } catch {
      return "rejected";
    }
  }, url);

  const [request, result] = await Promise.all([failedRequest, fetchResult]);
  expect(result).toBe("rejected");
  expect(request.failure()).toEqual({ errorText });
  await test.info().attach("abort-success", {
    body: JSON.stringify({ url, errorText }),
    contentType: "application/json",
  });
}

// The interceptor's intentional fatal interrupts the test body before requestfailed may settle.
// A dependent fixture finishes the same observation before Playwright tears down its page. This
// preserves the original fatal and abort evidence without swallowing errors or delaying the route.
const test = base.extend<{ requestFailure: (url: string, errorText: string) => Promise<void> }>({
  requestFailure: async ({ page }, use) => {
    let observation: Promise<void> | undefined;
    await use((url, errorText) => {
      observation = observeRequestFailure(page, url, errorText);
      return observation;
    });
    await observation;
  },
});

test("rejects an unmatched loopback API request through the installed interceptor", async ({
  page,
  requestFailure,
}) => {
  await installRegisterApi(page, { route: "tasks" });
  const url = "http://127.0.0.1:4174/api/v1/browser-harness-probe";

  await requestFailure(url, "net::ERR_FAILED");
});

test("rejects an external HTTPS request through the installed interceptor", async ({
  page,
  requestFailure,
}) => {
  await installRegisterApi(page, { route: "tasks" });
  const url = "https://external.invalid/browser-harness-probe";

  await requestFailure(url, "net::ERR_BLOCKED_BY_CLIENT.Inspector");
});
