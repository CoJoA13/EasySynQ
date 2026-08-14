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
