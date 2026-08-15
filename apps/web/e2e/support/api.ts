import type { Page, Route } from "@playwright/test";
import {
  auditListFixture,
  auditProgramsFixture,
  contextListFixture,
  contextRegisterStatusFixture,
  dcrListFixture,
  directoryFixture,
  docFixture,
  initiativeFixtures,
  interestedPartyListFixture,
  interestedPartyRegisterStatusFixture,
  mgmtReviewListFixture,
  notificationFixtures,
  objectiveFixtures,
  processesFixture,
  recordDetailFixture,
  recordsFixture,
  riskListFixture,
  riskRegisterStatusFixture,
  taskFixture,
} from "../../src/test/msw/handlers";
import type { RegisterCase } from "./registers";

export interface RegisterScenario {
  route: RegisterCase["key"];
  override?: RegisterRequestOverride;
  maxContent?: boolean;
}

export type RegisterRequestOutcome = "http-503" | "network-error" | "loaded";

export interface RegisterRequestOverride {
  readonly method: string;
  readonly pathname: string;
  readonly outcomes: readonly RegisterRequestOutcome[];
}

export type RequestCount = (method: string, pathname: string) => number;

const HARNESS_ORIGIN = "http://127.0.0.1:4174";
const currentDirectoryUser = directoryFixture[0];
const primaryTask = taskFixture[0];
const primaryProcess = processesFixture[0];
const primaryRecord = recordsFixture.data[0];

if (!currentDirectoryUser || !primaryTask || !primaryProcess || !primaryRecord) {
  throw new Error("browser fixtures require a synthetic user, task, process, and record");
}
const primaryProcessId = primaryProcess.id;
const primaryRecordId = primaryRecord.id;

export const MAXIMUM_RECORD_SEARCH = "Q".repeat(200);
export const MAXIMUM_RECORD_TITLE = "Preventive-maintenance-schedule".repeat(8);
export const MAXIMUM_EVIDENCE_FILENAME = `${"evidence".repeat(31)}.pdf`;

const maximumRecordsFixture = {
  ...recordsFixture,
  data: recordsFixture.data.map((record, index) =>
    index === 0
      ? {
          ...record,
          title: MAXIMUM_RECORD_TITLE,
          captured_by_display_name: "Maximum-length-captured-by-name".repeat(7),
        }
      : record,
  ),
};

const maximumRecordDetailFixture = {
  ...recordDetailFixture,
  title: maximumRecordsFixture.data[0]!.title,
  captured_by_display_name: maximumRecordsFixture.data[0]!.captured_by_display_name,
  source_document_title: "Maximum-length-source-document-title".repeat(7),
  evidence_blobs: recordDetailFixture.evidence_blobs.map((blob, index) =>
    index === 0 ? { ...blob, filename: MAXIMUM_EVIDENCE_FILENAME } : blob,
  ),
};

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

function requestKey(method: string, pathname: string): string {
  return `${method.toUpperCase()} ${pathname}`;
}

export async function installRegisterApi(
  page: Page,
  scenario: RegisterScenario,
): Promise<RequestCount> {
  const requestCounts = new Map<string, number>();
  const overrideKey = scenario.override
    ? requestKey(scenario.override.method, scenario.override.pathname)
    : null;
  const overrideOutcomes = [...(scenario.override?.outcomes ?? [])];

  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method().toUpperCase();

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

    const key = requestKey(method, url.pathname);
    requestCounts.set(key, (requestCounts.get(key) ?? 0) + 1);

    if (key === overrideKey) {
      const outcome = overrideOutcomes.shift();
      if (outcome === "http-503") {
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({
            code: "service_unavailable",
            title: "Service unavailable",
            detail: "The synthetic register request is temporarily unavailable.",
          }),
        });
        return;
      }
      if (outcome === "network-error") {
        await route.abort("failed");
        return;
      }
      // `loaded` (or an exhausted queue) delegates to the normal exact route response below.
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
        scenario.route === "improvement" ||
        scenario.route === "records") &&
      method === "GET" &&
      url.pathname === "/api/v1/directory/users" &&
      url.search === ""
    ) {
      await fulfillJson(route, directoryFixture);
      return;
    }

    if (
      scenario.route === "records" &&
      method === "GET" &&
      url.pathname === "/api/v1/documents" &&
      hasOnlySearchParams(url, { limit: "20", offset: "0" })
    ) {
      await fulfillJson(route, {
        data: docFixture,
        page: {
          limit: 20,
          offset: 0,
          returned: docFixture.length,
          has_more: false,
        },
      });
      return;
    }

    if (
      scenario.route === "records" &&
      method === "GET" &&
      url.pathname === "/api/v1/records" &&
      (hasOnlySearchParams(url, { limit: "50" }) ||
        (scenario.maxContent &&
          hasOnlySearchParams(url, { limit: "50", q: MAXIMUM_RECORD_SEARCH })) ||
        hasOnlySearchParams(url, {
          limit: "50",
          q: "REC-000041",
          record_type: "EVIDENCE",
        }))
    ) {
      await fulfillJson(route, {
        ...(scenario.maxContent ? maximumRecordsFixture : recordsFixture),
        page: { ...recordsFixture.page, limit: 50 },
      });
      return;
    }

    if (
      scenario.route === "records" &&
      method === "GET" &&
      url.pathname === `/api/v1/records/${primaryRecordId}` &&
      url.search === ""
    ) {
      await fulfillJson(
        route,
        scenario.maxContent ? maximumRecordDetailFixture : recordDetailFixture,
      );
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

  return (method, pathname) => requestCounts.get(requestKey(method, pathname)) ?? 0;
}
