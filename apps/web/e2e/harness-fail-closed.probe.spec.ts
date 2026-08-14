import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { installRegisterApi } from "./support/api";

async function requestFailure(page: Page, url: string, errorText: string): Promise<void> {
  const failedRequest = page.waitForEvent("requestfailed", (request) => request.url() === url);
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

test("rejects an unmatched loopback API request through the installed interceptor", async ({
  page,
}) => {
  await installRegisterApi(page, { route: "tasks" });
  const url = "http://127.0.0.1:4174/api/v1/browser-harness-probe";

  await requestFailure(page, url, "net::ERR_FAILED");
});

test("rejects an external HTTPS request through the installed interceptor", async ({ page }) => {
  await installRegisterApi(page, { route: "tasks" });
  const url = "https://external.invalid/browser-harness-probe";

  await requestFailure(page, url, "net::ERR_BLOCKED_BY_CLIENT.Inspector");
});
