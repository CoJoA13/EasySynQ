import { execFile } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";
import type { BrowserContext, Page } from "@playwright/test";

function requiredEnvironment(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
}

const baseURL = requiredEnvironment("EASYSYNQ_LIVE_BASE_URL");
const liveOrigin = new URL(baseURL).origin;
const setupSecret = requiredEnvironment("EASYSYNQ_LIVE_SETUP_SECRET");
const username = requiredEnvironment("EASYSYNQ_LIVE_USERNAME");
const canonicalUsername = username.trim().toLowerCase();
const newPassword = requiredEnvironment("EASYSYNQ_LIVE_NEW_PASSWORD");
const composeProject = requiredEnvironment("EASYSYNQ_LIVE_COMPOSE_PROJECT");
if (!/^easysynq-first-admin-[a-z0-9]+$/.test(composeProject)) {
  throw new Error("EASYSYNQ_LIVE_COMPOSE_PROJECT is invalid");
}

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const composeEnvironment = join(repositoryRoot, ".env");

async function recreateKeycloakCandidate(): Promise<void> {
  const composeArguments = [
    "compose",
    "--env-file",
    composeEnvironment,
    "-p",
    composeProject,
    "-f",
    "infra/compose/compose.yml",
    "-f",
    "infra/compose/compose.s.yml",
    "-f",
    "infra/compose/compose.dev.yml",
    "-f",
    "infra/compose/compose.first-admin-live.yml",
    "up",
    "-d",
    "--no-deps",
    "--force-recreate",
    "keycloak",
  ];
  await new Promise<void>((resolveRecreation, rejectRecreation) => {
    execFile(
      "docker",
      composeArguments,
      {
        cwd: repositoryRoot,
        timeout: 60_000,
        maxBuffer: 1024 * 1024,
        windowsHide: true,
      },
      (error) => {
        if (error) {
          rejectRecreation(new Error("live acceptance could not recreate Keycloak"));
          return;
        }
        resolveRecreation();
      },
    );
  });
}

async function waitForOidcDiscovery(page: Page): Promise<void> {
  const discoveryURL = `${liveOrigin}/realms/easysynq/.well-known/openid-configuration`;
  await expect
    .poll(
      () =>
        page.evaluate(async (url) => {
          try {
            const response = await fetch(url, { cache: "no-store" });
            return response.ok;
          } catch {
            return false;
          }
        }, discoveryURL),
      {
        timeout: 90_000,
        intervals: [500, 1_000, 2_000],
        message: "OIDC discovery did not recover after Keycloak recreation",
      },
    )
    .toBe(true);
}

async function captureOidcTokenSubject(page: Page): Promise<string> {
  const response = await page.waitForResponse(
    (candidate) => {
      const url = new URL(candidate.url());
      return (
        candidate.request().method() === "POST" &&
        candidate.ok() &&
        url.origin === liveOrigin &&
        url.pathname === "/realms/easysynq/protocol/openid-connect/token"
      );
    },
    { timeout: 60_000 },
  );
  let tokenResponse: unknown;
  try {
    tokenResponse = await response.json();
  } catch {
    throw new Error("live token response was malformed");
  }
  if (
    typeof tokenResponse !== "object" ||
    tokenResponse === null ||
    !("id_token" in tokenResponse) ||
    typeof tokenResponse.id_token !== "string"
  ) {
    throw new Error("live token response did not contain an identity token");
  }

  const encodedPayload = tokenResponse.id_token.split(".")[1];
  if (!encodedPayload) throw new Error("live identity token was malformed");
  let payload: unknown;
  try {
    payload = JSON.parse(Buffer.from(encodedPayload, "base64url").toString("utf8"));
  } catch {
    throw new Error("live identity token was malformed");
  }
  if (
    typeof payload !== "object" ||
    payload === null ||
    !("sub" in payload) ||
    typeof payload.sub !== "string" ||
    !payload.sub
  ) {
    throw new Error("live identity token did not contain a subject");
  }
  return payload.sub;
}

function assertSameSubject(expected: string, candidate: string): void {
  if (candidate !== expected) {
    throw new Error("authenticated identity changed during live acceptance");
  }
}

async function installLiveOriginGuard(context: BrowserContext): Promise<void> {
  await context.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.protocol !== "http:" && url.protocol !== "https:") {
      await route.continue();
      return;
    }
    if (url.origin !== liveOrigin) {
      await route.abort("blockedbyclient");
      throw new Error("live acceptance blocked unexpected external request");
    }
    await route.continue();
  });
}

async function expectSensitiveValuesNotRetained(
  page: Page,
  sensitiveValues: readonly string[],
): Promise<void> {
  const retainedBrowserState = await page.evaluate(() => {
    const entries = (storage: Storage): string[] =>
      Array.from({ length: storage.length }, (_, index) => {
        const key = storage.key(index) ?? "";
        return `${key}=${storage.getItem(key) ?? ""}`;
      });
    return [window.location.href, ...entries(localStorage), ...entries(sessionStorage)].join("\n");
  });
  if (sensitiveValues.some((value) => retainedBrowserState.includes(value))) {
    throw new Error("a live credential reached browser storage or the URL");
  }
}

test("first administrator completes the required Keycloak password update", async ({
  browser,
  page,
}) => {
  test.setTimeout(300_000);
  await installLiveOriginGuard(page.context());
  await page.goto("/setup");

  await expect(page.getByRole("heading", { name: "Create the first administrator" })).toBeVisible();
  await expect(page.locator('input[name="username"]')).toHaveCount(0);

  await page.getByLabel(/^Setup secret/).fill(setupSecret);
  await page.getByLabel(/^Username/).fill(username);
  await page.getByLabel(/^Display name/).fill("Live First Administrator");
  const provisionResponsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      response.request().method() === "POST" &&
      url.origin === liveOrigin &&
      url.pathname === "/api/v1/setup/administrator"
    );
  });
  await page.getByRole("button", { name: "Create administrator" }).click();
  const provisionResponse = await provisionResponsePromise;
  expect(provisionResponse.status()).toBe(201);
  const provisioned = (await provisionResponse.json()) as {
    administrator: { username: string };
    credential_receipt: string;
  };
  expect(provisioned.administrator.username).toBe(canonicalUsername);
  const credentialReceipt = provisioned.credential_receipt;
  if (!/^[A-Za-z0-9_-]{43}$/.test(credentialReceipt)) {
    throw new Error("the provision response credential receipt was absent or malformed");
  }

  const passwordHeading = page.getByRole("heading", {
    name: "Temporary password — shown once",
  });
  await expect(passwordHeading).toBeVisible();
  const temporaryPassword = (await page.locator("code").textContent())?.trim();
  if (!temporaryPassword) throw new Error("the show-once credential was absent");
  const sensitiveValues = [setupSecret, credentialReceipt, temporaryPassword, newPassword];

  await expectSensitiveValuesNotRetained(page, sensitiveValues);
  const acknowledgmentRequestPromise = page.waitForRequest((request) => {
    const url = new URL(request.url());
    return (
      request.method() === "POST" &&
      url.origin === liveOrigin &&
      url.pathname === "/api/v1/setup/administrator/acknowledge"
    );
  });
  await page.getByRole("button", { name: "I’ve saved it — Continue to sign in" }).click();
  const acknowledgmentRequest = await acknowledgmentRequestPromise;
  const acknowledgmentBody = acknowledgmentRequest.postDataJSON() as Record<string, unknown>;
  const expectedAcknowledgment = {
    secret: setupSecret,
    credential_receipt: credentialReceipt,
  };
  if (
    Object.keys(acknowledgmentBody).length !== 2 ||
    acknowledgmentBody.secret !== expectedAcknowledgment.secret ||
    acknowledgmentBody.credential_receipt !== expectedAcknowledgment.credential_receipt
  ) {
    throw new Error("the live acknowledgment did not carry the exact active credential proof");
  }

  const keycloakUsername = page.locator('input[name="username"]');
  await expect(keycloakUsername).toBeVisible();
  await keycloakUsername.fill(canonicalUsername);
  await page.locator('input[name="password"]').fill(temporaryPassword);
  await page.getByRole("button", { name: "Sign In", exact: true }).click();

  const replacement = page.locator('input[name="password-new"]');
  await expect(replacement).toBeVisible();
  await replacement.fill(newPassword);
  await page.locator('input[name="password-confirm"]').fill(newPassword);
  const subjectBeforeRecreationPromise = captureOidcTokenSubject(page);
  await page.getByRole("button", { name: "Submit", exact: true }).click();
  const subjectBeforeRecreation = await subjectBeforeRecreationPromise;

  await page.waitForURL((url) => url.origin === liveOrigin && url.pathname === "/setup");
  await expect(page.getByLabel("Legal name", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Create the first administrator" })).toHaveCount(
    0,
  );
  await expectSensitiveValuesNotRetained(page, sensitiveValues);

  await recreateKeycloakCandidate();
  await waitForOidcDiscovery(page);
  const subjectAfterRecreationPromise = captureOidcTokenSubject(page);
  await page.reload();
  const subjectAfterRecreation = await subjectAfterRecreationPromise;
  assertSameSubject(subjectBeforeRecreation, subjectAfterRecreation);
  await page.waitForURL((url) => url.origin === liveOrigin && url.pathname === "/setup");
  await expect(page.getByLabel("Legal name", { exact: true })).toBeVisible();
  await expect(page.locator('input[name="username"]')).toHaveCount(0);
  await expectSensitiveValuesNotRetained(page, sensitiveValues);

  const freshCredentialContext = await browser.newContext();
  try {
    await installLiveOriginGuard(freshCredentialContext);
    const freshCredentialPage = await freshCredentialContext.newPage();
    await freshCredentialPage.goto(`${baseURL}/setup`);
    const freshUsername = freshCredentialPage.locator('input[name="username"]');
    await expect(freshUsername).toBeVisible();
    await freshUsername.fill(canonicalUsername);
    await freshCredentialPage.locator('input[name="password"]').fill(newPassword);
    const subjectFromFreshLoginPromise = captureOidcTokenSubject(freshCredentialPage);
    await freshCredentialPage.getByRole("button", { name: "Sign In", exact: true }).click();
    const subjectFromFreshLogin = await subjectFromFreshLoginPromise;
    assertSameSubject(subjectBeforeRecreation, subjectFromFreshLogin);
    await freshCredentialPage.waitForURL(
      (url) => url.origin === liveOrigin && url.pathname === "/setup",
    );
    await expect(freshCredentialPage.getByLabel("Legal name", { exact: true })).toBeVisible();
    await expectSensitiveValuesNotRetained(freshCredentialPage, sensitiveValues);
  } finally {
    await freshCredentialContext.close();
  }

  const rejectedCredentialContext = await browser.newContext();
  try {
    await installLiveOriginGuard(rejectedCredentialContext);
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
    await expectSensitiveValuesNotRetained(rejectedCredentialPage, sensitiveValues);
  } finally {
    await rejectedCredentialContext.close();
  }
});
