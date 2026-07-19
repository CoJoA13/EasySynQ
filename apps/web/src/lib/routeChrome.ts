import { useEffect, useRef } from "react";
import { useLocation } from "react-router-dom";

// path-prefix → tab-title. Longest prefix wins (sorted below), so a child path like
// /settings/notifications resolves before a shorter sibling. Unmapped → the bare app name.
const TITLES: readonly (readonly [string, string])[] = [
  ["/admin", "Administration"],
  ["/setup", "Setup"],
  ["/library", "Library"],
  ["/documents", "Document"],
  ["/tasks", "Tasks"],
  ["/settings/notifications", "Notification settings"],
  ["/notifications", "Notifications"],
  ["/search", "Search"],
  ["/compliance", "Compliance"],
  ["/capa", "CAPA"],
  ["/audits", "Audits"],
  ["/ingestion", "Ingestion"],
  ["/drift", "Drift"],
  ["/objectives", "Objectives"],
  ["/management-reviews", "Management reviews"],
  ["/dcrs", "Document change requests"],
  ["/improvement", "Improvement"],
  ["/risks", "Risks"],
  ["/context", "Context"],
  ["/interested-parties", "Interested parties"],
];

const SORTED = [...TITLES].sort((a, b) => b[0].length - a[0].length);

function labelFor(pathname: string): string {
  for (const [prefix, label] of SORTED) {
    if (pathname === prefix || pathname.startsWith(prefix + "/")) return label;
  }
  return "";
}

export function useRouteChrome(): void {
  const { pathname } = useLocation();
  const firstRun = useRef(true);
  useEffect(() => {
    const label = labelFor(pathname);
    document.title = label ? `EasySynQ — ${label}` : "EasySynQ";
    if (firstRun.current) {
      firstRun.current = false;
      return; // don't steal focus from the skip-link on the first load
    }
    document.getElementById("main-content")?.focus();
  }, [pathname]);
}
