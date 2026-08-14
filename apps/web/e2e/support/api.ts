import type { Page, Route } from "@playwright/test";
import {
  auditListFixture,
  auditProgramsFixture,
  contextListFixture,
  contextRegisterStatusFixture,
  dcrListFixture,
  directoryFixture,
  initiativeFixtures,
  interestedPartyListFixture,
  interestedPartyRegisterStatusFixture,
  mgmtReviewListFixture,
  notificationFixtures,
  objectiveFixtures,
  processesFixture,
  riskListFixture,
  riskRegisterStatusFixture,
  taskFixture,
} from "../../src/test/msw/handlers";
import type { RegisterCase } from "./registers";

export interface RegisterScenario {
  route: RegisterCase["key"];
}

const HARNESS_ORIGIN = "http://127.0.0.1:4174";
const currentDirectoryUser = directoryFixture[0];
const primaryTask = taskFixture[0];
const primaryProcess = processesFixture[0];

if (!currentDirectoryUser || !primaryTask || !primaryProcess) {
  throw new Error("browser fixtures require a synthetic user, task, and process");
}
const primaryProcessId = primaryProcess.id;

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
const objectiveByRag = { green: 0, amber: 0, red: 0, unmeasured: 0 };
for (const objective of objectiveFixtures) objectiveByRag[objective.rag] += 1;
const objectiveScorecard = {
  total: objectiveFixtures.length,
  on_target: objectiveByRag.green,
  by_rag: objectiveByRag,
  objectives: objectiveFixtures,
};

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
      scenario.route === "audits" &&
      method === "GET" &&
      url.pathname === "/api/v1/audits" &&
      url.search === ""
    ) {
      await fulfillJson(route, auditListFixture);
      return;
    }

    if (
      scenario.route === "audits" &&
      method === "GET" &&
      url.pathname === "/api/v1/audit-programs" &&
      url.search === ""
    ) {
      await fulfillJson(route, auditProgramsFixture);
      return;
    }

    if (
      (scenario.route === "audits" ||
        scenario.route === "dcrs" ||
        scenario.route === "improvement") &&
      method === "GET" &&
      url.pathname === "/api/v1/directory/users" &&
      url.search === ""
    ) {
      await fulfillJson(route, directoryFixture);
      return;
    }

    if (
      scenario.route === "objectives" &&
      method === "GET" &&
      url.pathname === "/api/v1/objectives/scorecard" &&
      url.search === ""
    ) {
      await fulfillJson(route, objectiveScorecard);
      return;
    }

    if (
      scenario.route === "management-reviews" &&
      method === "GET" &&
      url.pathname === "/api/v1/management-reviews" &&
      url.search === ""
    ) {
      await fulfillJson(route, mgmtReviewListFixture);
      return;
    }

    if (
      scenario.route === "dcrs" &&
      method === "GET" &&
      url.pathname === "/api/v1/dcrs" &&
      url.search === ""
    ) {
      await fulfillJson(route, dcrListFixture);
      return;
    }

    if (
      scenario.route === "improvement" &&
      method === "GET" &&
      url.pathname === "/api/v1/improvement-initiatives" &&
      url.search === ""
    ) {
      await fulfillJson(route, { data: initiativeFixtures });
      return;
    }

    if (
      scenario.route === "risks" &&
      method === "GET" &&
      url.pathname === "/api/v1/risks" &&
      url.search === ""
    ) {
      await fulfillJson(route, riskListFixture);
      return;
    }

    if (
      scenario.route === "risks" &&
      method === "GET" &&
      url.pathname === "/api/v1/risks/register" &&
      url.search === ""
    ) {
      await fulfillJson(route, riskRegisterStatusFixture);
      return;
    }

    if (
      (scenario.route === "improvement" || scenario.route === "risks") &&
      method === "GET" &&
      url.pathname === "/api/v1/processes" &&
      url.search === ""
    ) {
      await fulfillJson(route, processesFixture);
      return;
    }

    if (
      scenario.route === "risks" &&
      method === "GET" &&
      url.pathname === "/api/v1/me/permissions" &&
      hasOnlySearchParams(url, { scope_level: "PROCESS", scope_id: primaryProcessId })
    ) {
      await fulfillJson(route, {
        scope: { level: "PROCESS", selector: { process_ids: [primaryProcessId] } },
        permissions: [],
      });
      return;
    }

    if (
      scenario.route === "context" &&
      method === "GET" &&
      url.pathname === "/api/v1/context" &&
      url.search === ""
    ) {
      await fulfillJson(route, contextListFixture);
      return;
    }

    if (
      scenario.route === "context" &&
      method === "GET" &&
      url.pathname === "/api/v1/context/register" &&
      url.search === ""
    ) {
      await fulfillJson(route, contextRegisterStatusFixture);
      return;
    }

    if (
      scenario.route === "interested-parties" &&
      method === "GET" &&
      url.pathname === "/api/v1/interested-parties" &&
      url.search === ""
    ) {
      await fulfillJson(route, interestedPartyListFixture);
      return;
    }

    if (
      scenario.route === "interested-parties" &&
      method === "GET" &&
      url.pathname === "/api/v1/interested-parties/register" &&
      url.search === ""
    ) {
      await fulfillJson(route, interestedPartyRegisterStatusFixture);
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
