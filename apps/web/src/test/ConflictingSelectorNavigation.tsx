import type { ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { classifyEffectiveView } from "../lib/effectiveView";
import { RouteAnnouncement, useRouteChrome } from "../lib/routeChrome";

export function ConflictingSelectorNavigation({
  route,
  selector,
  values,
  unrelated,
  children,
}: {
  route: string;
  selector: string;
  values: readonly [string, string];
  unrelated: readonly [string, string];
  children: ReactNode;
}) {
  useRouteChrome();
  const { pathname, search } = useLocation();
  const navigate = useNavigate();
  const view = classifyEffectiveView(pathname, new URLSearchParams(search));

  const navigateToConflict = () => {
    const params = new URLSearchParams([[unrelated[0], unrelated[1]]]);
    params.append(selector, values[0]);
    params.append(selector, values[1]);
    navigate(`${route}?${params}`);
  };

  return (
    <>
      <button onClick={navigateToConflict}>navigate to conflicting selectors</button>
      <output aria-label="Current location">{pathname + search}</output>
      <output aria-label="Effective recovery key">{view.recoveryKey}</output>
      {children}
      <RouteAnnouncement />
    </>
  );
}
