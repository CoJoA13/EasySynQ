import type { Page, Route } from "@playwright/test";
import { directoryFixture, notificationFixtures, taskFixture } from "../../src/test/msw/handlers";

export interface RegisterScenario {
  route: "tasks";
}

const HARNESS_ORIGIN = "http://127.0.0.1:4174";
const currentDirectoryUser = directoryFixture[0];
const primaryTask = taskFixture[0];

if (!currentDirectoryUser || !primaryTask) {
  throw new Error("browser fixtures require a synthetic user and task");
}

const currentUser = {
  ...currentDirectoryUser,
  keycloak_subject: currentDirectoryUser.id,
  email: "mara@example.com",
  status: "ACTIVE",
  org_timezone: "UTC",
};

const emptyNotifications = notificationFixtures.slice(0, 0);
const tasks = [
  primaryTask,
  {
    ...primaryTask,
    id: "task2222-2222-2222-2222-222222222222",
    instance_id: "wf222222-2222-2222-2222-222222222222",
    subject_id: "22222222-2222-2222-2222-222222222222",
    subject_identifier: "SOP-PRD-007",
    subject_title: "Production Control",
  },
];

function hasOnlySearchParams(url: URL, expected: Record<string, string>): boolean {
  const entries = Object.entries(expected);
  return (
    url.searchParams.size === entries.length &&
    entries.every(([key, value]) => url.searchParams.get(key) === value)
  );
}

async function fulfillJson(route: Route, body: unknown): Promise<void> {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

export async function installRegisterApi(page: Page, scenario: RegisterScenario): Promise<void> {
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();

    if (url.protocol !== "http:" && url.protocol !== "https:") {
      await route.continue();
      return;
    }

    if (url.origin !== HARNESS_ORIGIN) {
      await route.abort("blockedbyclient");
      throw new Error(`Unexpected external request: ${method} ${request.url()}`);
    }

    if (!url.pathname.startsWith("/api/")) {
      await route.continue();
      return;
    }

    if (method === "GET" && url.pathname === "/api/v1/setup/state" && url.search === "") {
      await fulfillJson(route, { setup_state: "OPERATIONAL" });
      return;
    }

    if (method === "GET" && url.pathname === "/api/v1/me" && url.search === "") {
      await fulfillJson(route, currentUser);
      return;
    }

    if (method === "GET" && url.pathname === "/api/v1/me/permissions" && url.search === "") {
      await fulfillJson(route, {
        scope: { level: "SYSTEM", selector: null },
        permissions: [],
      });
      return;
    }

    if (
      method === "GET" &&
      url.pathname === "/api/v1/notifications" &&
      hasOnlySearchParams(url, { unread_only: "true", limit: "100" })
    ) {
      await fulfillJson(route, emptyNotifications);
      return;
    }

    if (method === "GET" && url.pathname === "/api/v1/notifications/stream" && url.search === "") {
      await route.fulfill({ status: 200, contentType: "text/event-stream", body: "" });
      return;
    }

    if (
      scenario.route === "tasks" &&
      method === "GET" &&
      url.pathname === "/api/v1/tasks" &&
      hasOnlySearchParams(url, { assignee: "me", state: "PENDING" })
    ) {
      await fulfillJson(route, tasks);
      return;
    }

    await route.abort("failed");
    throw new Error(`Unexpected API request: ${method} ${request.url()}`);
  });
}
