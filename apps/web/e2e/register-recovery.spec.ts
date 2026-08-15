import { expect, test } from "@playwright/test";
import { installRegisterApi } from "./support/api";

test("recovers the DCR register after one HTTP 503", async ({ page }) => {
  const scenario = {
    route: "dcrs",
    override: {
      method: "GET",
      pathname: "/api/v1/dcrs",
      outcomes: ["http-503", "loaded"],
    },
  } as const;
  const requestCount = await installRegisterApi(page, scenario);

  await page.goto("/dcrs");

  const error = page.getByText("Couldn't load change requests");
  const retry = page.getByRole("button", { name: "Try again" });
  await expect(error).toBeVisible();
  await expect(page.getByRole("table")).toHaveCount(0);
  await expect(retry).toHaveCount(1);
  await retry.click();
  await expect(page.getByRole("table")).toHaveCount(1);
  await expect(error).toHaveCount(0);
  await expect(retry).toHaveCount(0);
  expect(requestCount("GET", "/api/v1/dcrs")).toBe(2);
});

test("recovers the Context register after one network error", async ({ page }) => {
  const scenario = {
    route: "context",
    override: {
      method: "GET",
      pathname: "/api/v1/context",
      outcomes: ["network-error", "loaded"],
    },
  } as const;
  const requestCount = await installRegisterApi(page, scenario);

  await page.goto("/context");

  await expect(page.getByText("Couldn't load the context register")).toBeVisible();
  await expect(page.getByRole("table")).toHaveCount(0);
  await expect(page.getByPlaceholder("Search issues…")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Try again" })).toHaveCount(1);
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("table")).toHaveCount(1);
  await expect(page.getByPlaceholder("Search issues…")).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Try again" })).toHaveCount(0);
  expect(requestCount("GET", "/api/v1/context")).toBe(2);
});

test("recovers Records after one HTTP 503 while its URL and controls persist", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  const scenario = {
    route: "records",
    override: {
      method: "GET",
      pathname: "/api/v1/records",
      outcomes: ["http-503", "loaded"],
    },
  } as const;
  const requestCount = await installRegisterApi(page, scenario);
  const recordsUrl = "/records?q=REC-000041&record_type=EVIDENCE";

  await page.goto(recordsUrl);

  const error = page.getByText("Couldn't load records");
  const retry = page.getByRole("button", { name: "Try again" });
  const search = page.getByRole("searchbox", { name: "Search records", exact: true });
  const recordType = page.getByRole("textbox", { name: "Record type", exact: true });
  await expect(error).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`${recordsUrl.replace("?", "\\?")}$`));
  await expect(search).toHaveValue("REC-000041");
  await expect(recordType).toHaveValue("EVIDENCE");
  await expect(page.getByRole("table")).toHaveCount(0);
  const retryBox = await retry.boundingBox();
  expect(retryBox).not.toBeNull();
  expect(retryBox!.height).toBeGreaterThanOrEqual(44);

  await retry.click();

  await expect(page.getByRole("link", { name: "Open record REC-000041" })).toBeVisible();
  await expect(error).toHaveCount(0);
  await expect(retry).toHaveCount(0);
  await expect(page).toHaveURL(new RegExp(`${recordsUrl.replace("?", "\\?")}$`));
  await expect(search).toHaveValue("REC-000041");
  await expect(recordType).toHaveValue("EVIDENCE");
  expect(requestCount("GET", "/api/v1/records")).toBe(2);
});
