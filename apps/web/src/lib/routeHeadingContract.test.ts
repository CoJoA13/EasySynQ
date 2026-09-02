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

const artifactHeaderSource = import.meta.glob("../features/document/ArtifactHeader.tsx", {
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

/**
 * Every opening `<name …>` tag, brace-aware, with its offset.
 *
 * A naive `/<Title\b[^>]*?>/` terminates at the first `>` in the source — which inside
 * `onClick={() => undefined}` is the arrow, not the end of the tag. Such a tag then appears to
 * carry no `order` and is silently classified as an h1. Balancing braces is what closes that.
 */
function openingTags(source: string, name: string): { at: number; tag: string }[] {
  const found: { at: number; tag: string }[] = [];
  const needle = `<${name}`;
  for (let i = source.indexOf(needle); i !== -1; i = source.indexOf(needle, i + 1)) {
    const next = source[i + needle.length];
    if (next !== undefined && !/[\s/>]/.test(next)) continue; // <Titlebar>, not <Title>
    let depth = 0;
    let j = i + needle.length;
    for (; j < source.length; j += 1) {
      const c = source[j];
      if (c === "{") depth += 1;
      else if (c === "}") depth -= 1;
      else if (c === ">" && depth === 0) break;
    }
    found.push({ at: i, tag: source.slice(i, j + 1) });
  }
  return found;
}

/**
 * The heading levels a file contributes, in source order.
 *
 * Counts BOTH Mantine `<Title>` and a raw `<h1>`–`<h6>` element. Only counting `<Title>` was a real
 * hole: a raw `<h2>` added to `AppShell` renders above every page's h1 on every route, and passed
 * this contract, all 217 shell tests and the whole browser suite.
 *
 * Three `<Title>` shapes cannot be classified from source and are REJECTED rather than assumed:
 * a non-literal `order={SOME_CONST}`, and `component="h2"`, which is polymorphic and genuinely
 * overrides `order` (measured: `<Title order={1} component="h2">` renders level 2). Falling through
 * to "h1" for either is how a gate quietly stops gating. A BARE `<Title>` is not ambiguous — it is
 * an h1, which is Mantine's documented default and was confirmed by rendering one.
 */
function headingLevels(source: string): number[] {
  const src = code(source);
  const entries: { at: number; level: number }[] = [];

  for (const { at, tag } of openingTags(src, "Title")) {
    if (/\bcomponent=/.test(tag)) {
      throw new Error(
        `<Title component=…> cannot be levelled from source (it overrides order): ${tag.trim()}`,
      );
    }
    const order = /\border=\{([^}]*)\}/.exec(tag);
    if (order && !/^\s*[1-6]\s*$/.test(order[1]!)) {
      throw new Error(`<Title order={…}> is not a literal level: ${tag.trim()}`);
    }
    entries.push({ at, level: order ? Number(order[1]!.trim()) : 1 });
  }

  for (let level = 1; level <= 6; level += 1) {
    for (const { at } of openingTags(src, `h${level}`)) entries.push({ at, level });
  }

  return entries.sort((a, b) => a.at - b.at).map((e) => e.level);
}

/** Kept as the old name so the intent reads the same at each call site. */
const titleOrders = headingLevels;

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
    // Scan the whole <Routes> block for capitalised JSX tags rather than matching `element={<X`.
    // That pattern missed App.tsx's OWN established idiom —
    // `element={operational ? <Page /> : <Navigate to="/setup" replace />}` — which four top-level
    // routes already use, and it missed a prettier-wrapped `element={` on its own line. The effect
    // was worse than a miss: `AdminShell`, `AppShell` and `SetupWizard` never appeared in the
    // mounted set at all, so their entries in `accountedFor` were inert padding concealing the gap.
    const app = code(Object.values(appSource)[0]!);
    const routes = app.slice(app.indexOf("<Routes>"), app.indexOf("</Routes>"));
    expect(routes.length, "could not locate the <Routes> block in App.tsx").toBeGreaterThan(0);
    const mounted = new Set(
      [...routes.matchAll(/<([A-Z]\w+)/g)]
        .map((m) => m[1]!)
        // react-router's own elements are the scaffold, not something mounted.
        .filter((name) => name !== "Route" && name !== "Routes"),
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
    // Pin the SIZE too. Without it, an idiom this scan stops recognising shows up as silence
    // rather than as a failure — the set simply shrinks and every name in it is still accounted for.
    expect(mounted.size, `mounted: ${[...mounted].sort().join(", ")}`).toBe(accountedFor.size);
  });

  it.each(ROUTED_LEAF_PAGES)("%s makes its page title an h1", (path) => {
    const source = sourceEndingWith(path);
    const orders = titleOrders(source);
    const delegates =
      /<RegisterPageHeader\b/.test(code(source)) ||
      /<ArtifactHeader[^>]*order=\{1\}/s.test(code(source));
    // Either the page titles itself at order 1, or it hands the title to a component that does.
    // NOTE the delegating branch asserts nothing about an adopter's OWN <Title>s. What stops one of
    // the eleven reverting a single branch to a hand-rolled `<Title order={2}>` is the neighbouring
    // ./registerHeaderAdoption.test.ts, which forbids `<Title` in all eleven. A twelfth register
    // must be added to BOTH cohorts, or it inherits this gap.
    expect(
      orders.includes(1) || delegates,
      `${path} renders heading levels [${orders.join(", ")}] and delegates=${delegates}; ` +
        `a routed page must contribute an h1`,
    ).toBe(true);
  });

  it.each(HEADINGLESS_LAYOUTS)("%s contributes no heading of its own", (path) => {
    expect(titleOrders(sourceEndingWith(path))).toEqual([]);
  });

  // The shallowest heading each admin tab body may contribute. `not.toContain(1)` alone permitted an
  // h1 -> h5 jump straight under AdminShell's h1, which passed this contract and all 57 admin tests.
  // Two of them legitimately start at 3 rather than 2: their bodies render inside a Mantine Drawer,
  // whose `title` prop emits its own h2 (ModalBaseTitle is hard-coded component:"h2"), so h3 is the
  // correct next step down. RolesAdmin renders no heading at all — its Accordion controls are
  // UnstyledButtons, not headings.
  const ADMIN_SHALLOWEST: Record<(typeof ADMIN_TAB_BODIES)[number], number | null> = {
    "admin/UsersAdmin.tsx": 3,
    "admin/RolesAdmin.tsx": null,
    "admin/ProcessesAdmin.tsx": 3,
    "admin/ConfigAdmin.tsx": 2,
  };

  it.each(ADMIN_TAB_BODIES)("%s adds no second h1 and no skip beneath AdminShell", (path) => {
    const levels = titleOrders(sourceEndingWith(path));
    expect(levels).not.toContain(1);
    const shallowest = ADMIN_SHALLOWEST[path];
    expect(levels.length > 0 ? Math.min(...levels) : null).toBe(shallowest);
  });

  // ArtifactHeader is the ONE component in the SPA that still takes a heading level as a prop,
  // which makes it the higher-risk of the two shared headers and it had no guard at all. Flipping
  // its default from 3 to 1 gives the library drawer a second h1, nested under LibraryPage's h1 AND
  // under Mantine's own drawer h2 — and the entire suite, 2328 tests across 280 files, stayed green.
  it("keeps ArtifactHeader's drawer default a subheading, not a second h1", () => {
    const header = Object.values(artifactHeaderSource)[0]!;
    expect(code(header)).toContain("order = 3");
    expect(code(header)).toContain("order?: 1 | 3");
    expect(code(header)).toContain("<Title order={order}");
  });

  it("keeps the shared register header at order 1 for every adopter at once", () => {
    const header = Object.values(headerSource)[0]!;
    // The level is deliberately NOT a prop — `size` is. If `order` ever becomes caller-controlled
    // again, the eleven registers can diverge silently, which is how they diverged the first time.
    expect(code(header)).toContain("<Title order={1}");
    expect(code(header)).not.toMatch(/order\?:/);
  });
});
