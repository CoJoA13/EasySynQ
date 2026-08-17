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
  MAXIMUM_FIRST_ADMIN_RECEIPT,
  MAXIMUM_FIRST_ADMIN_SECRET,
  MAXIMUM_FIRST_ADMIN_USERNAME,
  REISSUED_FIRST_ADMIN_RECEIPT,
  REISSUED_FIRST_ADMIN_TEMPORARY_PASSWORD,
  REISSUE_FIRST_ADMIN_SECRET,
  REMINTED_FIRST_ADMIN_SECRET,
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

async function expectNoDocumentOverflow(page: Page): Promise<void> {
  expect(
    await page.evaluate(() => ({
      innerWidth: window.innerWidth,
      documentClientWidth: document.documentElement.clientWidth,
      documentScrollWidth: document.documentElement.scrollWidth,
      bodyLeft: document.body.getBoundingClientRect().left,
      bodyRight: document.body.getBoundingClientRect().right,
    })),
  ).toEqual({
    innerWidth: 320,
    documentClientWidth: 320,
    documentScrollWidth: 320,
    bodyLeft: 0,
    bodyRight: 320,
  });
}

async function expectExactActionTarget(locator: Locator): Promise<void> {
  const box = await locator.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.height).toBe(44);
  await expectInsideNarrowViewport(locator);
}

async function expectExactVisibilityTarget(locator: Locator): Promise<void> {
  const box = await locator.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.width).toBe(44);
  expect(box!.height).toBe(44);
  await expectInsideNarrowViewport(locator);
}

async function expectForcedColorsFocus(locator: Locator): Promise<void> {
  await expect(locator).toBeFocused();
  expect(
    await locator.evaluate((element) => {
      const styles = getComputedStyle(element);
      return {
        matchesFocusVisible: element.matches(":focus-visible"),
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
}

async function beforeUnloadIsPrevented(page: Page): Promise<boolean> {
  return page.evaluate(() => {
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    return event.defaultPrevented;
  });
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
  const requestBodies: Array<{ method: string; pathname: string; body: unknown }> = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.origin !== "http://127.0.0.1:4174") {
      externalRequests.push(request.url());
    } else if (url.pathname.startsWith("/api/") && request.method() === "POST") {
      requestBodies.push({
        method: request.method(),
        pathname: url.pathname,
        body: request.postDataJSON(),
      });
    }
  });

  await page.goto("/setup?e2e-auth=tokenless");

  expect(await page.evaluate(() => matchMedia("(forced-colors: active)").matches)).toBe(true);

  const secret = page.getByLabel(/^Setup secret/);
  const secretVisibility = page.getByRole("button", { name: "Show or hide setup secret" });
  const username = page.getByLabel(/^Username/);
  const displayName = page.getByLabel(/^Display name/);
  const email = page.getByLabel("Email", { exact: true });
  const firstName = page.getByLabel("First name", { exact: true });
  const lastName = page.getByLabel("Last name", { exact: true });
  const createAdministrator = page.getByRole("button", { name: "Create administrator" });
  await expect(page.getByRole("heading", { name: "Create the first administrator" })).toBeVisible();
  await expect(page).toHaveURL("http://127.0.0.1:4174/setup?e2e-auth=tokenless");
  await expectLoginCalls(page, 0);
  expect(externalRequests).toEqual([]);

  await secret.fill(MAXIMUM_FIRST_ADMIN_SECRET);
  await username.fill(MAXIMUM_FIRST_ADMIN_USERNAME);
  await displayName.fill(MAXIMUM_FIRST_ADMIN_DISPLAY_NAME);
  await email.fill(MAXIMUM_FIRST_ADMIN_EMAIL);
  await firstName.fill(MAXIMUM_FIRST_ADMIN_FIRST_NAME);
  await lastName.fill(MAXIMUM_FIRST_ADMIN_LAST_NAME);

  await secret.focus();
  await page.keyboard.press("Tab");
  await expectForcedColorsFocus(secretVisibility);
  await expectExactVisibilityTarget(secretVisibility);
  await page.keyboard.press("Space");
  await expect(secret).toHaveAttribute("type", "text");
  await page.keyboard.press("Space");
  await expect(secret).toHaveAttribute("type", "password");
  await page.keyboard.press("Tab");
  await expect(username).toBeFocused();
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
  await expectForcedColorsFocus(createAdministrator);

  for (const control of [
    secret,
    secretVisibility,
    username,
    displayName,
    email,
    firstName,
    lastName,
    createAdministrator,
  ]) {
    await expectInsideNarrowViewport(control);
  }
  await expectExactActionTarget(createAdministrator);
  await expectNoDocumentOverflow(page);

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
  await expect(copy).toBeEnabled();
  await expectLoginCalls(page, 0);
  expect(await beforeUnloadIsPrevented(page)).toBe(true);
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
  for (const action of [copy, acknowledge]) await expectExactActionTarget(action);
  await expectNoDocumentOverflow(page);

  await acknowledge.focus();
  await expect(acknowledge).toBeFocused();
  await page.keyboard.press("Enter");

  const retry = page.getByRole("button", { name: "Retry acknowledgment" });
  await expect(password).toBeVisible();
  await expect(page.getByRole("alert", { name: "Password receipt was not saved" })).toBeVisible();
  await expect(retry).toBeVisible();
  await expectExactActionTarget(retry);
  expect(requestBodies[1]).toEqual({
    method: "POST",
    pathname: "/api/v1/setup/administrator/acknowledge",
    body: {
      secret: MAXIMUM_FIRST_ADMIN_SECRET,
      credential_receipt: MAXIMUM_FIRST_ADMIN_RECEIPT,
    },
  });
  await expect(page).toHaveURL("http://127.0.0.1:4174/setup?e2e-auth=tokenless");
  await expectLoginCalls(page, 0);
  expect(externalRequests).toEqual([]);
  expect(await beforeUnloadIsPrevented(page)).toBe(true);

  await retry.focus();
  await expectForcedColorsFocus(retry);
  await page.keyboard.press("Enter");

  const invalidAlert = page.getByRole("alert", { name: "Current setup secret required" });
  const replacementSecret = page.getByRole("textbox", { name: "Current setup secret" });
  const replacementSecretVisibility = page.getByRole("button", {
    name: "Show or hide current setup secret for acknowledgment",
  });
  const retryWithCurrentSecret = page.getByRole("button", {
    name: "Retry with current setup secret",
  });
  await expect(invalidAlert).toBeVisible();
  await expect(replacementSecret).toBeFocused();
  await expect(copy).toBeEnabled();
  await expectLoginCalls(page, 0);
  await replacementSecret.fill(REMINTED_FIRST_ADMIN_SECRET);
  await page.keyboard.press("Tab");
  await expectForcedColorsFocus(replacementSecretVisibility);
  await expectExactVisibilityTarget(replacementSecretVisibility);
  await page.keyboard.press("Space");
  await expect(replacementSecret).toHaveAttribute("type", "text");
  await page.keyboard.press("Space");
  await expect(replacementSecret).toHaveAttribute("type", "password");
  await retryWithCurrentSecret.focus();
  await expectForcedColorsFocus(retryWithCurrentSecret);
  await expectExactActionTarget(retryWithCurrentSecret);
  await expectNoDocumentOverflow(page);
  await page.keyboard.press("Enter");

  const supersededAlert = page.getByRole("alert", {
    name: "Temporary password no longer current",
  });
  const issueNewPassword = page.getByRole("button", {
    name: "Issue a new temporary password",
  });
  await expect(supersededAlert).toBeVisible();
  await expect(password).toBeVisible();
  await expect(copy).toBeDisabled();
  await expect(acknowledge).toBeDisabled();
  await expectForcedColorsFocus(issueNewPassword);
  await expectExactActionTarget(issueNewPassword);
  await expectNoDocumentOverflow(page);
  await expectLoginCalls(page, 0);
  expect(await beforeUnloadIsPrevented(page)).toBe(true);
  await page.keyboard.press("Enter");

  const reissueInvalidAlert = page.getByRole("alert", {
    name: "Current setup secret required for reissue",
  });
  const reissueSecret = page.getByRole("textbox", { name: "Current setup secret" });
  const reissueSecretVisibility = page.getByRole("button", {
    name: "Show or hide current setup secret for password reissue",
  });
  const retryReissue = page.getByRole("button", {
    name: "Retry issuing with current setup secret",
  });
  await expect(reissueInvalidAlert).toBeVisible();
  await expect(reissueSecret).toBeFocused();
  await expect(copy).toBeDisabled();
  await reissueSecret.fill(REISSUE_FIRST_ADMIN_SECRET);
  await page.keyboard.press("Tab");
  await expectForcedColorsFocus(reissueSecretVisibility);
  await expectExactVisibilityTarget(reissueSecretVisibility);
  await page.keyboard.press("Space");
  await expect(reissueSecret).toHaveAttribute("type", "text");
  await page.keyboard.press("Space");
  await expect(reissueSecret).toHaveAttribute("type", "password");
  await retryReissue.focus();
  await expectForcedColorsFocus(retryReissue);
  await expectExactActionTarget(retryReissue);
  await expectNoDocumentOverflow(page);
  await page.keyboard.press("Enter");

  const reissuedPassword = page.getByText(REISSUED_FIRST_ADMIN_TEMPORARY_PASSWORD, {
    exact: true,
  });
  await expect(reissuedPassword).toBeVisible();
  await expect(password).toHaveCount(0);
  const reissuedCopy = page.getByRole("button", { name: "Copy temporary password" });
  const acknowledgeReissued = page.getByRole("button", {
    name: "I’ve saved it — Continue to sign in",
  });
  await expect(reissuedCopy).toBeEnabled();
  await expectLoginCalls(page, 0);
  await acknowledgeReissued.focus();
  await expectForcedColorsFocus(acknowledgeReissued);
  await expectExactActionTarget(acknowledgeReissued);
  await expectNoDocumentOverflow(page);
  await page.keyboard.press("Enter");

  await expect(page.getByRole("heading", { name: "Sign in to continue setup" })).toBeVisible();
  await expect(page.getByLabel("Legal name", { exact: true })).toHaveCount(0);
  await expectLoginCalls(page, 1);
  await expect(page.getByRole("heading", { name: "Create the first administrator" })).toHaveCount(
    0,
  );
  await expect(page).toHaveURL("http://127.0.0.1:4174/setup?e2e-auth=tokenless");
  expect(requestCount("POST", "/api/v1/setup/administrator")).toBe(3);
  expect(requestCount("POST", "/api/v1/setup/administrator/acknowledge")).toBe(4);
  expect(requestCount("POST", "/api/v1/setup/bootstrap")).toBe(0);
  expect(requestCount("GET", "/api/v1/setup/state")).toBe(2);
  expect(requestCount("GET", "/api/v1/setup")).toBe(0);
  expect(externalRequests).toEqual([]);
  expect(requestBodies).toEqual([
    {
      method: "POST",
      pathname: "/api/v1/setup/administrator",
      body: {
        secret: MAXIMUM_FIRST_ADMIN_SECRET,
        username: MAXIMUM_FIRST_ADMIN_USERNAME,
        display_name: MAXIMUM_FIRST_ADMIN_DISPLAY_NAME,
        email: MAXIMUM_FIRST_ADMIN_EMAIL,
        first_name: MAXIMUM_FIRST_ADMIN_FIRST_NAME,
        last_name: MAXIMUM_FIRST_ADMIN_LAST_NAME,
      },
    },
    {
      method: "POST",
      pathname: "/api/v1/setup/administrator/acknowledge",
      body: {
        secret: MAXIMUM_FIRST_ADMIN_SECRET,
        credential_receipt: MAXIMUM_FIRST_ADMIN_RECEIPT,
      },
    },
    {
      method: "POST",
      pathname: "/api/v1/setup/administrator/acknowledge",
      body: {
        secret: MAXIMUM_FIRST_ADMIN_SECRET,
        credential_receipt: MAXIMUM_FIRST_ADMIN_RECEIPT,
      },
    },
    {
      method: "POST",
      pathname: "/api/v1/setup/administrator/acknowledge",
      body: {
        secret: REMINTED_FIRST_ADMIN_SECRET,
        credential_receipt: MAXIMUM_FIRST_ADMIN_RECEIPT,
      },
    },
    {
      method: "POST",
      pathname: "/api/v1/setup/administrator",
      body: {
        secret: REMINTED_FIRST_ADMIN_SECRET,
        username: MAXIMUM_FIRST_ADMIN_USERNAME,
        display_name: MAXIMUM_FIRST_ADMIN_DISPLAY_NAME,
        email: MAXIMUM_FIRST_ADMIN_EMAIL,
        first_name: MAXIMUM_FIRST_ADMIN_FIRST_NAME,
        last_name: MAXIMUM_FIRST_ADMIN_LAST_NAME,
      },
    },
    {
      method: "POST",
      pathname: "/api/v1/setup/administrator",
      body: {
        secret: REISSUE_FIRST_ADMIN_SECRET,
        username: MAXIMUM_FIRST_ADMIN_USERNAME,
        display_name: MAXIMUM_FIRST_ADMIN_DISPLAY_NAME,
        email: MAXIMUM_FIRST_ADMIN_EMAIL,
        first_name: MAXIMUM_FIRST_ADMIN_FIRST_NAME,
        last_name: MAXIMUM_FIRST_ADMIN_LAST_NAME,
      },
    },
    {
      method: "POST",
      pathname: "/api/v1/setup/administrator/acknowledge",
      body: {
        secret: REISSUE_FIRST_ADMIN_SECRET,
        credential_receipt: REISSUED_FIRST_ADMIN_RECEIPT,
      },
    },
  ]);
  expect(await beforeUnloadIsPrevented(page)).toBe(false);

  await page.goto("/setup?e2e-auth=authenticated");

  await expect(page.getByLabel("Legal name", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Sign in to continue setup" })).toHaveCount(0);
  await expectLoginCalls(page, 0);
  await expect(page).toHaveURL("http://127.0.0.1:4174/setup?e2e-auth=authenticated");
  expect(requestCount("GET", "/api/v1/setup/state")).toBe(3);
  expect(requestCount("GET", "/api/v1/setup")).toBe(1);
  expect(externalRequests).toEqual([]);
});
