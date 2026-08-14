import { expect, test } from "@playwright/test";
import type { Page, Request } from "@playwright/test";
import { installRegisterApi } from "./support/api";

async function requestFailure(page: Page, url: string): Promise<Request> {
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
  return request;
}

test("rejects an unmatched loopback API request through the installed interceptor", async ({
  page,
}) => {
  await installRegisterApi(page, { route: "tasks" });
  const url = "http://127.0.0.1:4174/api/v1/browser-harness-probe";

  const request = await requestFailure(page, url);

  expect(request.failure()).toEqual({ errorText: "net::ERR_FAILED" });
  expect(test.info().errors.map((error) => error.message)).toContain(
    `Error: Unexpected API request: GET ${url}`,
  );
  test.fail(true, "the interceptor's fatal unmatched-API throw is expected by this self-test");
});

test("rejects an external HTTPS request through the installed interceptor", async ({ page }) => {
  await installRegisterApi(page, { route: "tasks" });
  const url = "https://external.invalid/browser-harness-probe";

  const request = await requestFailure(page, url);

  expect(request.failure()?.errorText).toMatch(/^net::ERR_BLOCKED_BY_CLIENT/);
  expect(test.info().errors.map((error) => error.message)).toContain(
    `Error: Unexpected external request: GET ${url}`,
  );
  test.fail(true, "the interceptor's fatal external-request throw is expected by this self-test");
});
