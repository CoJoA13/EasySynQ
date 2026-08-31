import type { Page, Route } from "@playwright/test";
import {
  auditListFixture,
  auditProgramsFixture,
  capaListFixture,
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

/**
 * The route a scenario drives.
 *
 * `/capa` is deliberately NOT a `RegisterCase`. That interface is table-shaped (`floor`, `headers`,
 * `finalHeader`) and the shared specs consume those — `register-geometry.spec.ts` asserts a case's
 * `containerScrollWidth` and `tableWidth` against its `floor`. `CapaBoardPage` renders a kanban
 * `ScrollArea` AND a `<Table>` that has no `Table.ScrollContainer` (the file is absent from the nine
 * pinned by `responsiveRegisterContract.test.ts`), so those assertions would measure something that
 * is not there. `/capa` gets its own spec instead and needs the harness only to answer its reads.
 */
export type ScenarioRoute = RegisterCase["key"] | "capa";

export interface RegisterScenario {
  route: ScenarioRoute;
  override?: RegisterRequestOverride;
  maxContent?: boolean;
  /**
   * SYSTEM-scope permission keys granted to the harness caller. Defaults to `[]` — an ungranted
   * reader — which is what every scenario used before this existed and what they still get by
   * omitting it. Pass keys to make a permission-gated write affordance render, so a spec can
   * measure the surface an operator with rights actually sees rather than only the denied one.
   */
  permissions?: readonly string[];
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
export const MAXIMUM_FIRST_ADMIN_SECRET = "s".repeat(512);
export const MAXIMUM_FIRST_ADMIN_USERNAME = "u".repeat(255);
export const MAXIMUM_FIRST_ADMIN_DISPLAY_NAME = "D".repeat(255);
export const MAXIMUM_FIRST_ADMIN_EMAIL = `${"e".repeat(308)}@example.com`;
export const MAXIMUM_FIRST_ADMIN_FIRST_NAME = "F".repeat(255);
export const MAXIMUM_FIRST_ADMIN_LAST_NAME = "L".repeat(255);
export const LONG_FIRST_ADMIN_TEMPORARY_PASSWORD = "P".repeat(512);
export const MAXIMUM_FIRST_ADMIN_RECEIPT = "R".repeat(43);
export const REMINTED_FIRST_ADMIN_SECRET = "C".repeat(512);
export const REISSUE_FIRST_ADMIN_SECRET = "N".repeat(512);
export const REISSUED_FIRST_ADMIN_RECEIPT = "Z".repeat(43);
export const REISSUED_FIRST_ADMIN_TEMPORARY_PASSWORD = "Q".repeat(512);

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

function hasExactJsonBody(actual: unknown, expected: Record<string, unknown>): boolean {
  if (actual === null || typeof actual !== "object" || Array.isArray(actual)) return false;
  const record = actual as Record<string, unknown>;
  const expectedEntries = Object.entries(expected);
  return (
    Object.keys(record).length === expectedEntries.length &&
    expectedEntries.every(([key, value]) => record[key] === value)
  );
}

export async function installFirstAdministratorApi(page: Page): Promise<RequestCount> {
  const requestCounts = new Map<string, number>();
  let provisionAttempts = 0;
  let acknowledgmentAttempts = 0;
  let acknowledged = false;

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

    if (method === "GET" && url.pathname === "/api/v1/setup/state" && url.search === "") {
      await fulfillJson(route, { setup_state: acknowledged ? "IN_SETUP" : "UNINITIALIZED" });
      return;
    }

    if (
      method === "POST" &&
      url.pathname === "/api/v1/setup/administrator" &&
      url.search === "" &&
      request.headers().authorization === undefined
    ) {
      const profile = {
        username: MAXIMUM_FIRST_ADMIN_USERNAME,
        display_name: MAXIMUM_FIRST_ADMIN_DISPLAY_NAME,
        email: MAXIMUM_FIRST_ADMIN_EMAIL,
        first_name: MAXIMUM_FIRST_ADMIN_FIRST_NAME,
        last_name: MAXIMUM_FIRST_ADMIN_LAST_NAME,
      };
      const body = request.postDataJSON();
      if (
        provisionAttempts === 0 &&
        hasExactJsonBody(body, { secret: MAXIMUM_FIRST_ADMIN_SECRET, ...profile })
      ) {
        provisionAttempts += 1;
        await route.fulfill({
          status: 201,
          contentType: "application/json",
          body: JSON.stringify({
            administrator: {
              id: "ad000001-0001-0001-0001-000000000001",
              username: MAXIMUM_FIRST_ADMIN_USERNAME,
              display_name: MAXIMUM_FIRST_ADMIN_DISPLAY_NAME,
              email: MAXIMUM_FIRST_ADMIN_EMAIL,
              status: "INVITED",
            },
            temporary_password: LONG_FIRST_ADMIN_TEMPORARY_PASSWORD,
            credential_receipt: MAXIMUM_FIRST_ADMIN_RECEIPT,
            password_delivery: "shown_once",
          }),
        });
        return;
      }
      if (
        provisionAttempts === 1 &&
        hasExactJsonBody(body, { secret: REMINTED_FIRST_ADMIN_SECRET, ...profile })
      ) {
        provisionAttempts += 1;
        await route.fulfill({
          status: 403,
          contentType: "application/problem+json",
          body: JSON.stringify({
            code: "bootstrap_invalid",
            title: "Synthetic invalid bootstrap proof",
          }),
        });
        return;
      }
      if (
        provisionAttempts === 2 &&
        hasExactJsonBody(body, { secret: REISSUE_FIRST_ADMIN_SECRET, ...profile })
      ) {
        provisionAttempts += 1;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            administrator: {
              id: "ad000001-0001-0001-0001-000000000001",
              username: MAXIMUM_FIRST_ADMIN_USERNAME,
              display_name: MAXIMUM_FIRST_ADMIN_DISPLAY_NAME,
              email: MAXIMUM_FIRST_ADMIN_EMAIL,
              status: "INVITED",
            },
            temporary_password: REISSUED_FIRST_ADMIN_TEMPORARY_PASSWORD,
            credential_receipt: REISSUED_FIRST_ADMIN_RECEIPT,
            password_delivery: "shown_once",
          }),
        });
        return;
      }
    }

    if (
      method === "POST" &&
      url.pathname === "/api/v1/setup/administrator/acknowledge" &&
      url.search === "" &&
      request.headers().authorization === undefined
    ) {
      const body = request.postDataJSON();
      if (
        acknowledgmentAttempts === 0 &&
        hasExactJsonBody(body, {
          secret: MAXIMUM_FIRST_ADMIN_SECRET,
          credential_receipt: MAXIMUM_FIRST_ADMIN_RECEIPT,
        })
      ) {
        acknowledgmentAttempts += 1;
        await route.fulfill({
          status: 503,
          contentType: "application/problem+json",
          body: JSON.stringify({
            code: "keycloak_unavailable",
            title: "Synthetic acknowledgment outage",
          }),
        });
        return;
      }
      if (
        acknowledgmentAttempts === 1 &&
        hasExactJsonBody(body, {
          secret: MAXIMUM_FIRST_ADMIN_SECRET,
          credential_receipt: MAXIMUM_FIRST_ADMIN_RECEIPT,
        })
      ) {
        acknowledgmentAttempts += 1;
        await route.fulfill({
          status: 403,
          contentType: "application/problem+json",
          body: JSON.stringify({
            code: "bootstrap_invalid",
            title: "Synthetic invalid bootstrap proof",
          }),
        });
        return;
      }
      if (
        acknowledgmentAttempts === 2 &&
        hasExactJsonBody(body, {
          secret: REMINTED_FIRST_ADMIN_SECRET,
          credential_receipt: MAXIMUM_FIRST_ADMIN_RECEIPT,
        })
      ) {
        acknowledgmentAttempts += 1;
        await route.fulfill({
          status: 409,
          contentType: "application/problem+json",
          body: JSON.stringify({
            code: "bootstrap_credential_superseded",
            title: "Synthetic credential supersession",
          }),
        });
        return;
      }
      if (
        acknowledgmentAttempts === 3 &&
        hasExactJsonBody(body, {
          secret: REISSUE_FIRST_ADMIN_SECRET,
          credential_receipt: REISSUED_FIRST_ADMIN_RECEIPT,
        })
      ) {
        acknowledgmentAttempts += 1;
        acknowledged = true;
        await fulfillJson(route, {
          setup_state: "IN_SETUP",
          admin_user_id: "ad000001-0001-0001-0001-000000000001",
        });
        return;
      }
    }

    if (
      acknowledged &&
      method === "GET" &&
      url.pathname === "/api/v1/setup" &&
      url.search === "" &&
      request.headers().authorization === "Bearer browser-test-token"
    ) {
      await fulfillJson(route, {
        setup_state: "IN_SETUP",
        gates: { "G-A": true, "G-E": false, "G-B": false, "G-C": false, "G-D": false },
        org_profile: { legal_name: null, short_code: null, timezone: null },
        backup: {
          configured: false,
          destination: null,
          last_restore_test_at: null,
          last_restore_test_result: null,
        },
        auth: { configured: false, method: null, last_test_at: null },
        tamper_evident: false,
      });
      return;
    }

    await route.abort("failed");
    throw new Error(`Unexpected API request: ${method} ${request.url()}`);
  });

  return (method, pathname) => requestCounts.get(requestKey(method, pathname)) ?? 0;
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
        // The SPA reads `{ key, effect }` entries and keeps only ALLOW (app/shell/usePermissions.ts),
        // so a bare key list would be silently dropped and every affordance would stay hidden.
        permissions: (scenario.permissions ?? []).map((key) => ({
          key,
          effect: "ALLOW",
          source: "harness",
        })),
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

    // SHELL, not per-scenario. The LeftRail's /tasks entry carries an open-task count (S-ui-2), and
    // the rail is mounted by AppShell on EVERY route — so this request now arrives in every
    // scenario, not just scenario.route === "tasks". Leaving it gated sent all other scenarios into
    // the fail-closed tail below. Non-tasks scenarios get an empty list, which resolveTaskCount
    // reads as a TRUE ZERO and renders with no badge, so the narrow-viewport geometry assertions
    // are unaffected; the tasks scenario keeps its populated fixture.
    if (
      method === "GET" &&
      url.pathname === "/api/v1/tasks" &&
      hasOnlySearchParams(url, { assignee: "me", state: "PENDING" })
    ) {
      await fulfillJson(route, scenario.route === "tasks" ? tasks : []);
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

    // Shaped from the real serializer, not the page: `api/capa.py::list_capas_endpoint` returns
    // `{ data: [...], truncated: bool }`, and `useCapas` reads BOTH (`query.data.data` and
    // `query.data.truncated`). `capaListFixture` is the same `satisfies Capa[]` rows the vitest
    // suite uses, so the row shape stays pinned to `_capa` in one place; `truncated` is added here
    // because that fixture is typed `{ data: Capa[] }` and omits it.
    if (
      scenario.route === "capa" &&
      method === "GET" &&
      url.pathname === "/api/v1/capas" &&
      url.search === ""
    ) {
      await fulfillJson(route, { ...capaListFixture, truncated: false });
      return;
    }

    // `capa` joins these because `CapaBoardPage` mounts `<CapaDrawer>` unconditionally — with a null
    // capaId it renders nothing, but its `useUserDirectory()` still runs (CapaDrawer.tsx:22).
    if (
      (scenario.route === "audits" ||
        scenario.route === "dcrs" ||
        scenario.route === "improvement" ||
        scenario.route === "records" ||
        scenario.route === "capa") &&
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

    // `capa` joins these because `CapaBoardPage` calls `useProcesses()` UNCONDITIONALLY — it probes
    // capa.create at the caller's first readable process so the Raise affordance can reach a bound
    // Process Owner, who never holds that key at SYSTEM.
    if (
      (scenario.route === "improvement" ||
        scenario.route === "risks" ||
        scenario.route === "capa") &&
      method === "GET" &&
      url.pathname === "/api/v1/processes" &&
      url.search === ""
    ) {
      await fulfillJson(route, processesFixture);
      return;
    }

    // The refined question `usePermissions({ level: "PROCESS", id })` asks. Answered `[]` — the
    // ungranted reader this harness authenticates everywhere — so a scenario that wants the Raise
    // affordance grants `capa.create` through `scenario.permissions` at SYSTEM instead.
    if (
      (scenario.route === "risks" || scenario.route === "capa") &&
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

    await route.abort("failed");
    throw new Error(`Unexpected API request: ${method} ${request.url()}`);
  });

  return (method, pathname) => requestCounts.get(requestKey(method, pathname)) ?? 0;
}
