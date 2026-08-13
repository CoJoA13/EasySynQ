import { VisuallyHidden } from "@mantine/core";
import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useLocation } from "react-router-dom";
import { classifyEffectiveView, type QueryStateClass } from "./effectiveView";

const RouteErrorChromeActiveContext = createContext(false);
const RouteErrorChromeRegistrationContext = createContext<(() => () => void) | null>(null);
const RouteAnnouncementValueContext = createContext<string | null>(null);
const RouteAnnouncementPublisherContext = createContext<((message: string | null) => void) | null>(
  null,
);

export function RouteChromeProvider({ children }: { children: ReactNode }) {
  const owners = useRef(new Set<symbol>());
  const [ownerCount, setOwnerCount] = useState(0);
  const [announcement, setAnnouncement] = useState<string | null>(null);
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
    createElement(
      RouteErrorChromeActiveContext.Provider,
      { value: ownerCount > 0 },
      createElement(
        RouteAnnouncementPublisherContext.Provider,
        { value: setAnnouncement },
        createElement(RouteAnnouncementValueContext.Provider, { value: announcement }, children),
      ),
    ),
  );
}

export function RouteAnnouncement() {
  const message = useContext(RouteAnnouncementValueContext);
  return createElement(
    VisuallyHidden,
    {
      role: "status",
      "aria-live": "polite",
      "aria-atomic": "true",
      "aria-label": "Page navigation",
    },
    message ?? "",
  );
}

export function useRouteErrorChromeOwnership(): void {
  const acquireRouteErrorChrome = useContext(RouteErrorChromeRegistrationContext);
  useEffect(() => acquireRouteErrorChrome?.(), [acquireRouteErrorChrome]);
}

interface PreviousRouteView {
  pathname: string;
  chromeKey: string;
  recoveryKey: string;
  queryStateClass: QueryStateClass;
}

function isSamePathMaterialTransition(
  previousView: PreviousRouteView | null,
  pathname: string,
  chromeKey: string,
  queryStateClass: QueryStateClass,
): boolean {
  return (
    previousView !== null &&
    previousView.pathname === pathname &&
    previousView.chromeKey !== chromeKey &&
    (previousView.queryStateClass === "material-view" || queryStateClass === "material-view")
  );
}

export function useRouteChrome(): void {
  const { pathname, search, hash } = useLocation();
  const routeErrorOwnsChrome = useContext(RouteErrorChromeActiveContext);
  const publishAnnouncement = useContext(RouteAnnouncementPublisherContext);
  const view = useMemo(
    () => classifyEffectiveView(pathname, new URLSearchParams(search)),
    [pathname, search],
  );
  const previous = useRef<PreviousRouteView | null>(null);
  const previousRouteErrorOwnership = useRef(false);
  const pendingRouteMain = useRef<{ announcement: string | null } | null>(null);

  useEffect(() => {
    const previousView = previous.current;
    const pathnameChanged = previousView !== null && previousView.pathname !== pathname;
    const chromeChanged = previousView !== null && previousView.chromeKey !== view.chromeKey;
    const recoveryChanged = previousView !== null && previousView.recoveryKey !== view.recoveryKey;
    const effectiveTransition = pathnameChanged || chromeChanged || recoveryChanged;
    const materialTransition = isSamePathMaterialTransition(
      previousView,
      pathname,
      view.chromeKey,
      view.queryStateClass,
    );

    if (routeErrorOwnsChrome) {
      if (pathnameChanged) publishAnnouncement?.(null);
      if (effectiveTransition) {
        pendingRouteMain.current =
          (pathnameChanged && view.focusOwner === "route-main") || materialTransition
            ? { announcement: materialTransition ? view.announcement : null }
            : null;
      }
      previous.current = {
        pathname,
        chromeKey: view.chromeKey,
        recoveryKey: view.recoveryKey,
        queryStateClass: view.queryStateClass,
      };
      document.title = "EasySynQ — Page unavailable";
      if (!previousRouteErrorOwnership.current || effectiveTransition) {
        document.getElementById("route-error-heading")?.focus();
      }
      previousRouteErrorOwnership.current = true;
      return;
    }

    previousRouteErrorOwnership.current = false;
    document.title = view.title;
    if (pathnameChanged) publishAnnouncement?.(null);

    if (pendingRouteMain.current) {
      document.getElementById("main-content")?.focus();
      if (pendingRouteMain.current.announcement) {
        publishAnnouncement?.(pendingRouteMain.current.announcement);
      }
      pendingRouteMain.current = null;
    } else if (view.focusOwner === "route-main" && (pathnameChanged || materialTransition)) {
      // Initial deep links and StrictMode's second mount-effect pass leave previousView null or
      // unchanged. Detail and subview owners retain their own focus behavior.
      document.getElementById("main-content")?.focus();
      if (materialTransition) publishAnnouncement?.(view.announcement);
    }
    previous.current = {
      pathname,
      chromeKey: view.chromeKey,
      recoveryKey: view.recoveryKey,
      queryStateClass: view.queryStateClass,
    };
  }, [hash, pathname, publishAnnouncement, routeErrorOwnsChrome, search, view]);
}
