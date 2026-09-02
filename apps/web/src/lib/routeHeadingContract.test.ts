import { describe, expect, it } from "vitest";

// A source-text contract over the route table, in the idiom of ./registerHeaderAdoption.test.ts and
// ./shellLabelContract.test.ts.
//
// It exists because the defect it guards is invisible to every behavioural suite. A page that
// titles itself `<Title order={2}>` renders the same accessible NAME as one that titles itself
// `order={1}`, so all 125 `getByRole("heading", { name })` queries in the repository pass either
// way — and the two axe rules that would object cannot reach it: `page-has-heading-one` carries the
// selector `html:not(html *)`, so it is reported INAPPLICABLE for every container-scoped run, which
// is every `axe(container)` call in the suite. That is how eleven registers and fifteen other
// routes came to present a document with no `h1` at all, for the whole life of the programme,
// with nothing red.
//
// The runtime assertion is `test/headingOutline.ts`; it proves the rendered outline of the pages it
// is pointed at. This contract is the complement: it proves the COHORT — that no routed page was
// missed and that a twelfth register, or a revert, cannot quietly drop back to an `h2` page title.
const pages = import.meta.glob("../{features,admin,app}/**/*.tsx", {
  eager: true,
  query: "?raw",
  import: "default",
}) as Record<string, string>;

// A separate glob: a sibling-directory pattern ("./*.tsx") resolves to nothing here, so the shared
// header is named explicitly the way ./registerHeaderAdoption.test.ts names its eleven adopters.
const headerSource = import.meta.glob("./RegisterPageHeader.tsx", {
  eager: true,
  query: "?raw",
  import: "default",
}) as Record<string, string>;

const appSource = import.meta.glob("../App.tsx", {
  eager: true,
  query: "?raw",
  import: "default",
}) as Record<string, string>;

function sourceEndingWith(suffix: string): string {
  const hit = Object.entries(pages).find(([key]) => key.endsWith(suffix))?.[1];
  if (typeof hit !== "string") throw new Error(`Missing source: ${suffix}`);
  return hit;
}

/** Strip comments so prose mentioning `<Title>` is never read as a rendered heading. */
function code(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\/|\/\/[^\n]*/g, "");
}

function titleOrders(source: string): number[] {
  return [...code(source).matchAll(/<Title\b[^>]*?>/gs)].map((m) => {
    const order = /order=\{(\d)\}/.exec(m[0]);
    // Mantine's Title defaults to order 1, so a bare <Title> IS an h1.
    return order ? Number(order[1]) : 1;
  });
}

// Every route that renders its own page body, paired with the file that owns its title. Layout and
// redirect elements are listed separately below rather than omitted, so this cohort can be checked
// against App.tsx's route table instead of drifting from it.
const ROUTED_LEAF_PAGES = [
  "features/home/HomePage.tsx",
  "features/library/LibraryPage.tsx",
  "features/authoring/NewDocumentWizard.tsx",
  "features/document/DocumentDetailPage.tsx",
  "features/review/TasksInbox.tsx",
  "features/review/ReviewApprovePage.tsx",
  "features/notifications/NotificationsPage.tsx",
  "features/notifications/NotificationSettingsPage.tsx",
  "features/search/SearchResultsPage.tsx",
  "features/compliance/CompliancePage.tsx",
  "features/reports/ReportsRegisterPage.tsx",
  "features/capa/CapaBoardPage.tsx",
  "features/capa/ComplaintsPage.tsx",
  "features/capa/NcrsPage.tsx",
  "features/audits/AuditsListPage.tsx",
  "features/audits/ProgramPage.tsx",
  "features/audits/AuditDetailPage.tsx",
  "features/ingestion/IngestionRunsPage.tsx",
  "features/ingestion/IngestionRunPage.tsx",
  "features/drift/DriftStatusPage.tsx",
  "features/drift/SupersededCopiesPage.tsx",
  "features/objectives/ObjectivesRegisterPage.tsx",
  "features/objectives/ObjectiveDetailPage.tsx",
  "features/management-review/ManagementReviewsRegisterPage.tsx",
  "features/management-review/ManagementReviewDetailPage.tsx",
  "features/dcr/DcrsRegisterPage.tsx",
  "features/dcr/DcrDiffPage.tsx",
  "features/improvement/ImprovementRegisterPage.tsx",
  "features/risk/RisksRegisterPage.tsx",
  "features/context/ContextRegisterPage.tsx",
  "features/interested-parties/InterestedPartiesRegisterPage.tsx",
  "features/records/RecordsPage.tsx",
  "features/records/RecordDetailPage.tsx",
] as const;

// These render around a leaf page and must contribute NO heading, or the page below them would be
// a second h1. CapaLayout, AuditsLayout and DriftLayout are tab strips: their children are sibling
// routes, not nested documents, which is why those children each own an h1 of their own.
const HEADINGLESS_LAYOUTS = [
  "app/shell/AppShell.tsx",
  "features/capa/CapaLayout.tsx",
  "features/audits/AuditsLayout.tsx",
  "features/drift/DriftLayout.tsx",
] as const;

// /admin is the one genuine nesting in the route table: AdminShell renders the h1 and its four tab
// bodies render INSIDE it, so they must NOT introduce one of their own.
const ADMIN_TAB_BODIES = [
  "admin/UsersAdmin.tsx",
  "admin/RolesAdmin.tsx",
  "admin/ProcessesAdmin.tsx",
  "admin/ConfigAdmin.tsx",
] as const;

describe("route heading contract", () => {
  it("covers every element the route table mounts", () => {
    const mounted = new Set(
      [...code(Object.values(appSource)[0]!).matchAll(/element=\{<([A-Z]\w+)/g)].map((m) => m[1]!),
    );
    // Everything App.tsx mounts is either a leaf page, a headingless layout, an admin tab body, or
    // one of the four elements below that own no page title. If this fails, a route was added and
    // this contract did not notice — which is the exact way the defect returns.
    const accountedFor = new Set([
      ...[...ROUTED_LEAF_PAGES, ...HEADINGLESS_LAYOUTS, ...ADMIN_TAB_BODIES].map((p) =>
        p.slice(p.lastIndexOf("/") + 1).replace(".tsx", ""),
      ),
      "Navigate", // redirect, renders nothing
      "AdminShell", // the /admin h1 itself
      "SetupWizard", // pre-operational, outside the shell
      "LegacyImportRedirect", // redirect, renders nothing
    ]);
    expect([...mounted].filter((name) => !accountedFor.has(name))).toEqual([]);
  });

  it.each(ROUTED_LEAF_PAGES)("%s makes its page title an h1", (path) => {
    const source = sourceEndingWith(path);
    const orders = titleOrders(source);
    const delegates =
      /<RegisterPageHeader\b/.test(code(source)) ||
      /<ArtifactHeader[^>]*order=\{1\}/s.test(code(source));
    // Either the page titles itself at order 1, or it hands the title to a component that does.
    expect(
      orders.includes(1) || delegates,
      `${path} renders heading levels [${orders.join(", ")}] and delegates=${delegates}; ` +
        `a routed page must contribute an h1`,
    ).toBe(true);
  });

  it.each(HEADINGLESS_LAYOUTS)("%s contributes no heading of its own", (path) => {
    expect(titleOrders(sourceEndingWith(path))).toEqual([]);
  });

  it.each(ADMIN_TAB_BODIES)("%s adds no second h1 beneath AdminShell", (path) => {
    expect(titleOrders(sourceEndingWith(path))).not.toContain(1);
  });

  it("keeps the shared register header at order 1 for every adopter at once", () => {
    const header = Object.values(headerSource)[0]!;
    // The level is deliberately NOT a prop — `size` is. If `order` ever becomes caller-controlled
    // again, the eleven registers can diverge silently, which is how they diverged the first time.
    expect(code(header)).toContain("<Title order={1}");
    expect(code(header)).not.toMatch(/order\?:/);
  });
});
