import { useEffect, useRef } from "react";
import { matchPath, useLocation } from "react-router-dom";

const TITLES: readonly (readonly [string, string])[] = [
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
];

function labelFor(pathname: string): string | null {
  for (const [pattern, label] of TITLES) {
    if (matchPath({ path: pattern, end: true }, pathname)) return label;
  }
  return null;
}

export function useRouteChrome(): void {
  const { pathname } = useLocation();
  const prevPathname = useRef<string | null>(null);
  useEffect(() => {
    const label = labelFor(pathname);
    document.title = `EasySynQ — ${label ?? "Page not found"}`;
    // Focus the main region only on a genuine route CHANGE — not on the initial mount, and not on
    // React StrictMode's dev-only double-invoke of the mount effect (same pathname → no focus). An
    // unmatched route owns its focus in NotFoundPage, so this must not override the 404 heading.
    if (label !== null && prevPathname.current !== null && prevPathname.current !== pathname) {
      document.getElementById("main-content")?.focus();
    }
    prevPathname.current = pathname;
  }, [pathname]);
}
