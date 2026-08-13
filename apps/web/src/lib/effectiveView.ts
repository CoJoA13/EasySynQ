export type QueryStateClass = "material-view" | "detail" | "subview" | "ordinary" | "ignored";

export interface EffectiveView {
  title: string;
  chromeKey: string;
  recoveryKey: string;
  queryStateClass: QueryStateClass;
  focusOwner: "route-main" | "feature" | "none";
  announcement: string | null;
}

const TITLES = [
  ["/", "Dashboard"],
  ["/setup", "Setup"],
  ["/admin", "Administration"],
  ["/admin/users", "Administration"],
  ["/admin/roles", "Administration"],
  ["/admin/processes", "Administration"],
  ["/admin/config", "Administration"],
  ["/library", "Library"],
  ["/library/new", "New document"],
  ["/documents/:id", "Document"],
  ["/tasks", "Tasks"],
  ["/tasks/:id", "Task"],
  ["/settings/notifications", "Notification settings"],
  ["/notifications", "Notifications"],
  ["/search", "Search"],
  ["/compliance", "Compliance"],
  ["/reports/document-control", "Document register"],
  ["/capa", "CAPA"],
  ["/capa/complaints", "Complaints"],
  ["/capa/ncrs", "NCRs"],
  ["/audits", "Audits"],
  ["/audits/programme", "Audit programme"],
  ["/audits/:id", "Audit"],
  ["/imports", "Import"],
  ["/imports/:runId", "Import run"],
  ["/ingestion", "Import"],
  ["/ingestion/:runId", "Import run"],
  ["/drift", "Drift"],
  ["/drift/superseded-copies", "Superseded copies"],
  ["/objectives", "Objectives"],
  ["/objectives/:id", "Objective"],
  ["/management-reviews", "Management reviews"],
  ["/management-reviews/:id", "Management review"],
  ["/dcrs", "Document change requests"],
  ["/dcrs/:id/diff", "Document change request"],
  ["/improvement", "Improvement"],
  ["/risks", "Risks"],
  ["/context", "Context"],
  ["/interested-parties", "Interested parties"],
] as const;

const DETAIL_RULES = [
  ["/library", "detail", "Document details"],
  ["/dcrs", "dcr", "Change request details"],
  ["/capa", "capa", "CAPA details"],
  ["/improvement", "initiative", "Improvement details"],
  ["/context", "issue", "Context issue details"],
  ["/interested-parties", "party", "Interested party details"],
  ["/risks", "risk", "Risk details"],
] as const;

const DOCUMENT_TABS = new Set(["overview", "history", "approvals", "where-used", "acks"]);
const ORDINARY_KEYS = new Set([
  "q",
  "sort",
  "dir",
  "offset",
  "size",
  "state",
  "type",
  "owner",
  "clause",
  "eff",
  "ctype",
  "reason",
  "stage",
  "source",
  "status",
  "process",
  "rag",
  "band",
  "rtype",
  "classification",
  "category",
  "party_type",
  "influence",
  "queue",
  "conf",
]);

function matchesPath(pattern: string, pathname: string): boolean {
  const patternSegments = pattern.split("/").filter(Boolean);
  const pathSegments = pathname.split("/").filter(Boolean);

  return (
    patternSegments.length === pathSegments.length &&
    patternSegments.every(
      (segment, index) => segment.startsWith(":") || segment === pathSegments[index],
    )
  );
}

function labelFor(pathname: string): string | null {
  for (const [pattern, label] of TITLES) {
    if (matchesPath(pattern, pathname)) return label;
  }
  return null;
}

function keyPart(name: string, value: string): string {
  return `${name}=${encodeURIComponent(value)}`;
}

function knownView(pathname: string, label: string): EffectiveView {
  const key = `route:${pathname}`;
  return {
    title: `EasySynQ — ${label}`,
    chromeKey: key,
    recoveryKey: key,
    queryStateClass: "ignored",
    focusOwner: "route-main",
    announcement: label,
  };
}

function withQueryState(view: EffectiveView, queryStateClass: QueryStateClass): EffectiveView {
  return { ...view, queryStateClass };
}

function subview(pathname: string, base: EffectiveView, parts: readonly string[]): EffectiveView {
  return {
    ...base,
    recoveryKey: ["route", pathname, ...parts].join(":"),
    queryStateClass: "subview",
    focusOwner: "none",
    announcement: null,
  };
}

function hasOrdinaryState(searchParams: URLSearchParams): boolean {
  return Array.from(searchParams.keys()).some((key) => ORDINARY_KEYS.has(key));
}

function repeatedValue(searchParams: URLSearchParams, key: string): string | null {
  const values = searchParams.getAll(key);
  if (values.length === 0 || values.some((value) => value !== values[0])) return null;
  return values[0] ?? null;
}

export function classifyEffectiveView(
  pathname: string,
  searchParams: URLSearchParams,
): EffectiveView {
  const label = labelFor(pathname);
  if (label === null) {
    const key = `not-found:${pathname}`;
    return {
      title: "EasySynQ — Page not found",
      chromeKey: key,
      recoveryKey: key,
      queryStateClass: "ignored",
      focusOwner: "feature",
      announcement: null,
    };
  }

  const base = knownView(pathname, label);
  if (pathname === "/tasks" && repeatedValue(searchParams, "type") === "DOC_ACK") {
    return {
      title: "EasySynQ — Acknowledgements",
      chromeKey: "route:/tasks:acknowledgements",
      recoveryKey: "route:/tasks:acknowledgements",
      queryStateClass: "material-view",
      focusOwner: "route-main",
      announcement: "Acknowledgements",
    };
  }

  for (const [route, selector, detailLabel] of DETAIL_RULES) {
    const value = repeatedValue(searchParams, selector);
    if (pathname === route && value) {
      return {
        title: `EasySynQ — ${detailLabel}`,
        chromeKey: `route:${pathname}:${selector}`,
        recoveryKey: `route:${pathname}:${keyPart(selector, value)}`,
        queryStateClass: "detail",
        focusOwner: "feature",
        announcement: null,
      };
    }
  }

  if (matchesPath("/documents/:id", pathname)) {
    const tab = repeatedValue(searchParams, "tab");
    const mode = repeatedValue(searchParams, "mode");
    const from = repeatedValue(searchParams, "from");
    const to = repeatedValue(searchParams, "to");
    const parts = [
      ...(tab && tab !== "overview" && DOCUMENT_TABS.has(tab) ? [keyPart("tab", tab)] : []),
      ...(mode === "visual" ? [keyPart("mode", mode)] : []),
      ...(from ? [keyPart("from", from)] : []),
      ...(to ? [keyPart("to", to)] : []),
    ];
    if (parts.length > 0) return subview(pathname, base, parts);
  }

  if (matchesPath("/dcrs/:id/diff", pathname) && repeatedValue(searchParams, "mode") === "visual") {
    return subview(pathname, base, [keyPart("mode", "visual")]);
  }

  return withQueryState(base, hasOrdinaryState(searchParams) ? "ordinary" : "ignored");
}
