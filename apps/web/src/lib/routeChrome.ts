import { useEffect, useRef } from "react";
import { useLocation } from "react-router-dom";

// path-prefix → tab-title. Longest prefix wins (sorted below), so a child path like
// /settings/notifications resolves before a shorter sibling. Unmapped → the bare app name.
// "/" is the dashboard: it sorts LAST (shortest) and its `prefix + "/"` guard is "//" (never a real
// path), so it matches the root route exactly and can never shadow a deeper route.
const TITLES: readonly (readonly [string, string])[] = [
  ["/", "Dashboard"],
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
  const prevPathname = useRef<string | null>(null);
  useEffect(() => {
    const label = labelFor(pathname);
    document.title = label ? `EasySynQ — ${label}` : "EasySynQ";
    // Focus the main region only on a genuine route CHANGE — not on the initial mount, and not on
    // React StrictMode's dev-only double-invoke of the mount effect (same pathname → no focus).
    if (prevPathname.current !== null && prevPathname.current !== pathname) {
      document.getElementById("main-content")?.focus();
    }
    prevPathname.current = pathname;
  }, [pathname]);
}
