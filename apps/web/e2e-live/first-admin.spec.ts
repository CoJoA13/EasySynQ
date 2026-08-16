import { expect, test } from "@playwright/test";

function requiredEnvironment(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
}

const baseURL = requiredEnvironment("EASYSYNQ_LIVE_BASE_URL");
const setupSecret = requiredEnvironment("EASYSYNQ_LIVE_SETUP_SECRET");
const username = requiredEnvironment("EASYSYNQ_LIVE_USERNAME");
const canonicalUsername = username.trim().toLowerCase();
const newPassword = requiredEnvironment("EASYSYNQ_LIVE_NEW_PASSWORD");

test("first administrator completes the required Keycloak password update", async ({
  browser,
  page,
}) => {
  await page.goto("/setup");

  await expect(page.getByRole("heading", { name: "Create the first administrator" })).toBeVisible();
  await expect(page.locator('input[name="username"]')).toHaveCount(0);

  await page.getByLabel(/^Setup secret/).fill(setupSecret);
  await page.getByLabel(/^Username/).fill(username);
  await page.getByLabel(/^Display name/).fill("Live First Administrator");
  const provisionResponsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith("/api/v1/setup/administrator"),
  );
  await page.getByRole("button", { name: "Create administrator" }).click();
  const provisionResponse = await provisionResponsePromise;
  expect(provisionResponse.status()).toBe(201);
  const provisioned = (await provisionResponse.json()) as {
    administrator: { username: string };
  };
  expect(provisioned.administrator.username).toBe(canonicalUsername);

  const passwordHeading = page.getByRole("heading", {
    name: "Temporary password — shown once",
  });
  await expect(passwordHeading).toBeVisible();
  const temporaryPassword = (await page.locator("code").textContent())?.trim();
  if (!temporaryPassword) throw new Error("the show-once credential was absent");

  await page.getByRole("button", { name: "I’ve saved it — Continue to sign in" }).click();

  const keycloakUsername = page.locator('input[name="username"]');
  await expect(keycloakUsername).toBeVisible();
  await keycloakUsername.fill(canonicalUsername);
  await page.locator('input[name="password"]').fill(temporaryPassword);
  await page.getByRole("button", { name: "Sign In", exact: true }).click();

  const replacement = page.locator('input[name="password-new"]');
  await expect(replacement).toBeVisible();
  await replacement.fill(newPassword);
  await page.locator('input[name="password-confirm"]').fill(newPassword);
  await page.getByRole("button", { name: "Submit", exact: true }).click();

  await page.waitForURL((url) => url.origin === baseURL && url.pathname === "/setup");
  await expect(page.getByLabel("Legal name", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Create the first administrator" })).toHaveCount(
    0,
  );

  const rejectedCredentialContext = await browser.newContext();
  try {
    const rejectedCredentialPage = await rejectedCredentialContext.newPage();
    await rejectedCredentialPage.goto(`${baseURL}/setup`);
    const cleanUsername = rejectedCredentialPage.locator('input[name="username"]');
    await expect(cleanUsername).toBeVisible();
    await cleanUsername.fill(canonicalUsername);
    await rejectedCredentialPage.locator('input[name="password"]').fill(temporaryPassword);
    await rejectedCredentialPage.getByRole("button", { name: "Sign In", exact: true }).click();

    await expect(rejectedCredentialPage.locator('input[name="password"]')).toBeVisible();
    await expect(
      rejectedCredentialPage.getByText("Invalid username or password.", { exact: true }),
    ).toBeVisible();
  } finally {
    await rejectedCredentialContext.close();
  }
});
