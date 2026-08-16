import { expect, test } from "@playwright/test";
import type { Locator, Page } from "@playwright/test";
import {
  installFirstAdministratorApi,
  installRegisterApi,
  LONG_FIRST_ADMIN_TEMPORARY_PASSWORD,
  MAXIMUM_FIRST_ADMIN_DISPLAY_NAME,
  MAXIMUM_FIRST_ADMIN_EMAIL,
  MAXIMUM_FIRST_ADMIN_FIRST_NAME,
  MAXIMUM_FIRST_ADMIN_LAST_NAME,
  MAXIMUM_FIRST_ADMIN_SECRET,
  MAXIMUM_FIRST_ADMIN_USERNAME,
} from "./support/api";

async function expectInsideNarrowViewport(locator: Locator): Promise<void> {
  const box = await locator.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.x).toBeGreaterThanOrEqual(-1);
  expect(box!.x + box!.width).toBeLessThanOrEqual(321);
}

async function expectLoginCalls(page: Page, expected: number): Promise<void> {
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (window as Window & { __EASYSYNQ_E2E_LOGIN_CALLS__?: number })
            .__EASYSYNQ_E2E_LOGIN_CALLS__,
      ),
    )
    .toBe(expected);
}

test("mounts the real routed shell with deterministic authenticated data", async ({ page }) => {
  await installRegisterApi(page, { route: "tasks" });
  await page.goto("/tasks");

  await expect(page).toHaveTitle(/Tasks/);
  await expect(page.getByRole("heading", { name: "Review and approve" })).toBeVisible();
  await expect(page.getByRole("table", { name: "My tasks" })).toHaveCount(1);
  await expect(page.getByRole("link", { name: /SOP-PUR-014/ })).toHaveCount(1);
});

test("mounts the Records register and populated detail through the real routed shell", async ({
  page,
}) => {
  await installRegisterApi(page, { route: "records" });
  await page.goto("/records");

  const recordLink = page.getByRole("link", { name: "Open record REC-000041" });
  await expect(page).toHaveTitle(/Records/);
  await expect(recordLink).toHaveCount(1);
  await recordLink.click();

  await expect(page).toHaveURL(/\/records\/re000001-0001-0001-0001-000000000001$/);
  await expect(
    page.getByRole("heading", { name: "Preventive-maintenance schedule" }),
  ).toBeVisible();
  await expect(page.getByRole("region", { name: "Provenance" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Lifecycle" })).toBeVisible();
});

test("first administrator setup is resilient at 320px in forced colors", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await page.emulateMedia({ forcedColors: "active" });
  const requestCount = await installFirstAdministratorApi(page);
  const externalRequests: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).origin !== "http://127.0.0.1:4174") {
      externalRequests.push(request.url());
    }
  });

  await page.goto("/setup?e2e-auth=tokenless");

  expect(await page.evaluate(() => matchMedia("(forced-colors: active)").matches)).toBe(true);

  const secret = page.getByLabel(/^Setup secret/);
  const username = page.getByLabel(/^Username/);
  const displayName = page.getByLabel(/^Display name/);
  const email = page.getByLabel("Email", { exact: true });
  const firstName = page.getByLabel("First name", { exact: true });
  const lastName = page.getByLabel("Last name", { exact: true });
  const createAdministrator = page.getByRole("button", { name: "Create administrator" });
  await expect(
    page.getByRole("heading", { name: "Create the first administrator" }),
  ).toBeVisible();
  await expect(page).toHaveURL("http://127.0.0.1:4174/setup?e2e-auth=tokenless");
  await expectLoginCalls(page, 0);
  expect(externalRequests).toEqual([]);

  await secret.fill(MAXIMUM_FIRST_ADMIN_SECRET);
  await username.fill(MAXIMUM_FIRST_ADMIN_USERNAME);
  await displayName.fill(MAXIMUM_FIRST_ADMIN_DISPLAY_NAME);
  await email.fill(MAXIMUM_FIRST_ADMIN_EMAIL);
  await firstName.fill(MAXIMUM_FIRST_ADMIN_FIRST_NAME);
  await lastName.fill(MAXIMUM_FIRST_ADMIN_LAST_NAME);

  await username.focus();
  await page.keyboard.press("Tab");
  await expect(displayName).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(email).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(firstName).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(lastName).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(createAdministrator).toBeFocused();
  expect(
    await createAdministrator.evaluate((button) => {
      const styles = getComputedStyle(button);
      return {
        matchesFocusVisible: button.matches(":focus-visible"),
        outlineStyle: styles.outlineStyle,
        outlineWidth: styles.outlineWidth,
        outlineOffset: styles.outlineOffset,
        borderStyle: styles.borderStyle,
        borderWidth: styles.borderWidth,
      };
    }),
  ).toEqual({
    matchesFocusVisible: true,
    outlineStyle: "solid",
    outlineWidth: "2px",
    outlineOffset: "2px",
    borderStyle: "solid",
    borderWidth: "1px",
  });

  for (const control of [
    secret,
    username,
    displayName,
    email,
    firstName,
    lastName,
    createAdministrator,
  ]) {
    await expectInsideNarrowViewport(control);
  }
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);

  await page.keyboard.press("Enter");

  const passwordHeading = page.getByRole("heading", {
    name: "Temporary password — shown once",
  });
  const password = page.getByText(LONG_FIRST_ADMIN_TEMPORARY_PASSWORD, { exact: true });
  const copy = page.getByRole("button", { name: "Copy temporary password" });
  const acknowledge = page.getByRole("button", {
    name: "I’ve saved it — Continue to sign in",
  });
  await expect(passwordHeading).toBeVisible();
  await expect(passwordHeading).toBeFocused();
  await expect(password).toBeVisible();
  await expectLoginCalls(page, 0);
  const passwordShrinkBoundaries = await password.evaluate((element) => {
    const alert = element.closest('[role="alert"]');
    if (!(alert instanceof HTMLElement)) return [];

    const minWidths: string[] = [];
    let current: HTMLElement | null = element as HTMLElement;
    while (current !== null && current !== alert) {
      const parent: HTMLElement | null = current.parentElement;
      if (parent === null) return [];
      const parentDisplay = getComputedStyle(parent).display;
      if (parentDisplay === "flex" || parentDisplay === "inline-flex") {
        minWidths.push(getComputedStyle(current).minWidth);
      }
      current = parent;
    }
    return minWidths;
  });
  expect(passwordShrinkBoundaries).not.toHaveLength(0);
  expect(passwordShrinkBoundaries.every((minWidth) => minWidth === "0px")).toBe(true);
  for (const control of [password, copy, acknowledge]) {
    await expectInsideNarrowViewport(control);
  }
  for (const action of [copy, acknowledge]) {
    const box = await action.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.height).toBeGreaterThanOrEqual(44);
  }
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);

  await acknowledge.focus();
  await expect(acknowledge).toBeFocused();
  await page.keyboard.press("Enter");

  const retry = page.getByRole("button", { name: "Retry acknowledgment" });
  await expect(password).toBeVisible();
  await expect(page.getByRole("alert", { name: "Password receipt was not saved" })).toBeVisible();
  await expect(retry).toBeVisible();
  await expect(page).toHaveURL("http://127.0.0.1:4174/setup?e2e-auth=tokenless");
  await expectLoginCalls(page, 0);
  expect(externalRequests).toEqual([]);

  await retry.focus();
  await page.keyboard.press("Enter");

  await expect(page.getByRole("heading", { name: "Sign in to continue setup" })).toBeVisible();
  await expect(page.getByLabel("Legal name", { exact: true })).toHaveCount(0);
  await expectLoginCalls(page, 1);
  await expect(
    page.getByRole("heading", { name: "Create the first administrator" }),
  ).toHaveCount(0);
  await expect(page).toHaveURL("http://127.0.0.1:4174/setup?e2e-auth=tokenless");
  expect(requestCount("POST", "/api/v1/setup/administrator")).toBe(1);
  expect(requestCount("POST", "/api/v1/setup/administrator/acknowledge")).toBe(2);
  expect(requestCount("GET", "/api/v1/setup/state")).toBe(2);
  expect(requestCount("GET", "/api/v1/setup")).toBe(0);
  expect(externalRequests).toEqual([]);

  await page.goto("/setup?e2e-auth=authenticated");

  await expect(page.getByLabel("Legal name", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Sign in to continue setup" })).toHaveCount(0);
  await expectLoginCalls(page, 0);
  await expect(page).toHaveURL("http://127.0.0.1:4174/setup?e2e-auth=authenticated");
  expect(requestCount("GET", "/api/v1/setup/state")).toBe(3);
  expect(requestCount("GET", "/api/v1/setup")).toBe(1);
  expect(externalRequests).toEqual([]);
});
