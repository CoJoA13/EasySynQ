import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { expect, test } from "vitest";
import { RouteAnnouncement, RouteChromeProvider, useRouteChrome } from "../../lib/routeChrome";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { CapaBoardPage } from "./CapaBoardPage";
import { CapaLayout } from "./CapaLayout";
import { ComplaintsPage } from "./ComplaintsPage";
import { NcrsPage } from "./NcrsPage";

function tree() {
  return (
    <Routes>
      <Route path="capa" element={<CapaLayout />}>
        <Route index element={<CapaBoardPage />} />
        <Route path="complaints" element={<ComplaintsPage />} />
        <Route path="ncrs" element={<NcrsPage />} />
      </Route>
    </Routes>
  );
}

const CAPA_A = "ca000001-0001-0001-0001-000000000001";
const CAPA_B = "ca000002-0002-0002-0002-000000000002";

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname + loc.search}</div>;
}

function CapaUrlControls() {
  const navigate = useNavigate();
  return (
    <>
      <button onClick={() => navigate(`/capa?capa=${CAPA_B}`)}>replace capa</button>
      <button onClick={() => navigate("/capa")}>remove capa</button>
      <button onClick={() => navigate(-1)}>back</button>
    </>
  );
}

function CapaDetailChrome({ children }: { children: React.ReactNode }) {
  useRouteChrome();
  return (
    <>
      {children}
      <RouteAnnouncement />
    </>
  );
}

function recordCapaDetailRequests() {
  const ids: string[] = [];
  const listener = ({ request }: { request: Request }) => {
    const id = new URL(request.url).pathname.match(/^\/api\/v1\/capas\/([^/]+)$/)?.[1];
    if (id) ids.push(id);
  };
  server.events.on("request:match", listener);
  return { ids, stop: () => server.events.removeListener("request:match", listener) };
}

test("navigates board → complaints → ncrs through the tab bar", async () => {
  const u = userEvent.setup();
  renderWithProviders(tree(), { route: "/capa" });
  // the board face (its own title) renders at the index route
  expect(await screen.findByText("Nonconformity and CAPA")).toBeInTheDocument();
  await u.click(screen.getByRole("tab", { name: "Complaints" }));
  expect(await screen.findByText("CMP-000007")).toBeInTheDocument();
  await u.click(screen.getByRole("tab", { name: "NCRs" }));
  expect(await screen.findByText("NCR-000052")).toBeInTheDocument();
});

test("synchronizes a URL-seeded CAPA drawer across selector replacement and removal", async () => {
  const user = userEvent.setup();
  const requests = recordCapaDetailRequests();
  renderWithProviders(
    <RouteChromeProvider>
      <CapaDetailChrome>
        <CapaBoardPage />
        <CapaUrlControls />
      </CapaDetailChrome>
    </RouteChromeProvider>,
    { route: `/capa?capa=${CAPA_A}` },
  );

  const dialog = await screen.findByRole("dialog");
  await waitFor(() => expect(requests.ids).toContain(CAPA_A));
  expect(document.title).toBe("EasySynQ — CAPA details");
  expect(document.title).not.toContain(CAPA_A);
  expect(screen.getByRole("status", { name: "Page navigation" })).not.toHaveTextContent(CAPA_A);
  await waitFor(() => expect(dialog).toContainElement(document.activeElement as HTMLElement));

  await user.click(screen.getByRole("button", { name: "replace capa" }));
  expect(await screen.findByRole("dialog")).toBeInTheDocument();
  await waitFor(() => expect(requests.ids).toContain(CAPA_B));
  expect(document.title).not.toContain(CAPA_B);

  await user.click(screen.getByRole("button", { name: "remove capa" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(document.title).toBe("EasySynQ — CAPA");
  requests.stop();
});

test("keeps a locally opened CAPA drawer open while the board filter changes", async () => {
  const user = userEvent.setup();
  renderWithProviders(
    <>
      <CapaBoardPage />
      <LocationProbe />
    </>,
    { route: "/capa" },
  );
  await user.click(await screen.findByRole("button", { name: /REC-000031/ }));
  expect(await screen.findByRole("dialog")).toBeInTheDocument();
  expect(screen.getByTestId("loc")).toHaveTextContent("/capa");

  const [severityInput] = screen.getAllByLabelText("Severity");
  await user.click(severityInput!);
  await user.click(await screen.findByRole("option", { name: "Critical" }));
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  expect(screen.getByTestId("loc")).toHaveTextContent("/capa");
});

test("closing a deep-linked CAPA drawer replaces only ?capa and Back restores the register", async () => {
  const user = userEvent.setup();
  renderWithProviders(
    <>
      <CapaBoardPage />
      <CapaUrlControls />
      <LocationProbe />
    </>,
    { route: `/capa?source=audit&capa=${CAPA_A}` },
  );
  await screen.findByRole("dialog");
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(screen.getByTestId("loc")).toHaveTextContent("source=audit");
  expect(screen.getByTestId("loc")).not.toHaveTextContent("capa=");

  await user.click(screen.getByRole("button", { name: "replace capa" }));
  expect(await screen.findByRole("dialog")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "back" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});
