import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { matchPath, useLocation } from "react-router-dom";

const RouteErrorChromeActiveContext = createContext(false);
const RouteErrorChromeRegistrationContext = createContext<(() => () => void) | null>(null);

export function RouteChromeProvider({ children }: { children: ReactNode }) {
  const owners = useRef(new Set<symbol>());
  const [ownerCount, setOwnerCount] = useState(0);
  const acquireRouteErrorChrome = useCallback(() => {
    const owner = Symbol("route-error-chrome-owner");
    owners.current.add(owner);
    setOwnerCount(owners.current.size);

    return () => {
      if (!owners.current.delete(owner)) return;
      setOwnerCount(owners.current.size);
    };
  }, []);

  return createElement(
    RouteErrorChromeRegistrationContext.Provider,
    { value: acquireRouteErrorChrome },
    createElement(RouteErrorChromeActiveContext.Provider, { value: ownerCount > 0 }, children),
  );
}

export function useRouteErrorChromeOwnership(): void {
  const acquireRouteErrorChrome = useContext(RouteErrorChromeRegistrationContext);
  useEffect(() => acquireRouteErrorChrome?.(), [acquireRouteErrorChrome]);
}

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
  const routeErrorOwnsChrome = useContext(RouteErrorChromeActiveContext);
  const prevPathname = useRef<string | null>(null);
  const focusKnownRouteWhenAvailable = useRef(false);
  useEffect(() => {
    const label = labelFor(pathname);
    const pathnameChanged = prevPathname.current !== null && prevPathname.current !== pathname;

    if (routeErrorOwnsChrome) {
      if (pathnameChanged) focusKnownRouteWhenAvailable.current = true;
      prevPathname.current = pathname;
      document.title = "EasySynQ — Page unavailable";
      document.getElementById("route-error-heading")?.focus();
      return;
    }

    document.title = `EasySynQ — ${label ?? "Page not found"}`;
    // Focus the main region only on a genuine route CHANGE — not on the initial mount, and not on
    // React StrictMode's dev-only double-invoke of the mount effect (same pathname → no focus). An
    // unmatched route owns its focus in NotFoundPage, so this must not override the 404 heading.
    if (label !== null && (pathnameChanged || focusKnownRouteWhenAvailable.current)) {
      document.getElementById("main-content")?.focus();
    }
    focusKnownRouteWhenAvailable.current = false;
    prevPathname.current = pathname;
  }, [pathname, routeErrorOwnsChrome]);
}
