// Convert a server-built absolute notification deep_link (app_base_url + a route fragment) into a
// react-router-navigable "pathname + search". The link is same-origin and server-trusted, and we
// navigate IN-APP via useNavigate, so there is no open-redirect surface. Any parse failure (or an
// empty path) falls back to /tasks so a malformed/foreign link never throws or leaves a dead click.
export function toRoutePath(deepLink: string): string {
  try {
    const u = new URL(deepLink);
    return (u.pathname || "/tasks") + u.search;
  } catch {
    return "/tasks";
  }
}
