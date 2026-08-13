import { http, HttpResponse } from "msw";
import { axe } from "jest-axe";
import { expect, it } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation, useNavigate } from "react-router-dom";
import { RouteAnnouncement, RouteChromeProvider, useRouteChrome } from "../../lib/routeChrome";
import { renderWithProviders } from "../../test/render";
import { ConflictingSelectorNavigation } from "../../test/ConflictingSelectorNavigation";
import { server } from "../../test/msw/server";
import { expectResponsiveTable } from "../../test/responsiveTable";
import { DcrsRegisterPage } from "./DcrsRegisterPage";

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname + loc.search}</div>;
}

const DCR_A = "dcr00001-0001-0001-0001-000000000001";
const DCR_B = "dcr00002-0002-0002-0002-000000000002";

function DcrUrlControls() {
  const navigate = useNavigate();
  return (
    <>
      <button onClick={() => navigate(`/dcrs?state=Open&dcr=${DCR_B}`)}>replace dcr</button>
      <button onClick={() => navigate("/dcrs?state=Open")}>remove dcr</button>
      <button onClick={() => navigate(-1)}>back</button>
    </>
  );
}

function DcrDetailChrome({ children }: { children: React.ReactNode }) {
  useRouteChrome();
  return (
    <>
      {children}
      <RouteAnnouncement />
    </>
  );
}

function recordDcrDetailRequests() {
  const ids: string[] = [];
  const listener = ({ request }: { request: Request }) => {
    const id = new URL(request.url).pathname.match(/^\/api\/v1\/dcrs\/([^/]+)$/)?.[1];
    if (id) ids.push(id);
  };
  server.events.on("request:match", listener);
  return { ids, stop: () => server.events.removeListener("request:match", listener) };
}

function containerSizeFor(element: HTMLElement) {
  const container = element.closest(".mantine-Container-root");
  expect(container).not.toBeNull();
  return (container as HTMLElement).style.getPropertyValue("--container-size");
}

it("keeps the loading and loaded register at the same xl width", async () => {
  let release: (() => void) | undefined;
  const blocked = new Promise<void>((resolve) => {
    release = resolve;
  });
  server.use(
    http.get("/api/v1/dcrs", async () => {
      await blocked;
      return HttpResponse.json({ data: [] });
    }),
  );

  renderWithProviders(<DcrsRegisterPage />);
  expect(containerSizeFor(screen.getByRole("status", { name: "Loading change requests" }))).toBe(
    "var(--container-size-xl)",
  );
  act(() => release?.());
  expect(containerSizeFor(await screen.findByRole("heading", { name: "Change requests" }))).toBe(
    "var(--container-size-xl)",
  );
});

it("contains the complete DCR table in one 1040 px scroll region", async () => {
  renderWithProviders(<DcrsRegisterPage />, { route: "/dcrs" });
  await screen.findByText("DCR-2026-0001");
  const table = expectResponsiveTable(1040);
  expect(within(table).getAllByRole("columnheader")).toHaveLength(7);
  expect(within(table).getAllByRole("button", { name: "DCR-2026-0001" })).toHaveLength(1);
});

it("lists change requests and opens the drawer when an identifier is clicked", async () => {
  renderWithProviders(<DcrsRegisterPage />);
  expect(await screen.findByText("DCR-2026-0001")).toBeInTheDocument(); // first assertion waits for skeleton
  expect(screen.getByText("DCR-2026-0002")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "DCR-2026-0001" }));
  expect(await screen.findByText(/Corrective action requires/)).toBeInTheDocument(); // drawer detail loaded
});

it("opens the drawer on a ?dcr=<id> deep-link", async () => {
  renderWithProviders(<DcrsRegisterPage />, {
    route: "/dcrs?dcr=dcr00001-0001-0001-0001-000000000001",
  });
  expect(await screen.findByText(/Corrective action requires/)).toBeInTheDocument();
});

it("synchronizes a URL-seeded DCR drawer across selector replacement and removal", async () => {
  const user = userEvent.setup();
  const requests = recordDcrDetailRequests();
  renderWithProviders(
    <RouteChromeProvider>
      <DcrDetailChrome>
        <DcrsRegisterPage />
        <DcrUrlControls />
      </DcrDetailChrome>
    </RouteChromeProvider>,
    { route: `/dcrs?state=Open&dcr=${DCR_A}` },
  );

  const dialog = await screen.findByRole("dialog");
  await waitFor(() => expect(requests.ids).toContain(DCR_A));
  expect(await within(dialog).findByText(/Corrective action requires/)).toBeInTheDocument();
  expect(document.title).toBe("EasySynQ — Change request details");
  expect(document.title).not.toContain(DCR_A);
  expect(screen.getByRole("status", { name: "Page navigation" })).not.toHaveTextContent(DCR_A);
  await waitFor(() => expect(dialog).toContainElement(document.activeElement as HTMLElement));

  await user.click(screen.getByRole("button", { name: "replace dcr" }));
  expect(await screen.findByRole("dialog")).toBeInTheDocument();
  await waitFor(() => expect(requests.ids).toContain(DCR_B));
  expect(document.title).toBe("EasySynQ — Change request details");
  expect(document.title).not.toContain(DCR_B);

  await user.click(screen.getByRole("button", { name: "remove dcr" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(document.title).toBe("EasySynQ — Document change requests");
  requests.stop();
});

it("keeps a locally opened DCR drawer open when a filter changes", async () => {
  const user = userEvent.setup();
  renderWithProviders(
    <>
      <DcrsRegisterPage />
      <LocationProbe />
    </>,
    { route: "/dcrs" },
  );
  await user.click(await screen.findByRole("button", { name: "DCR-2026-0001" }));
  expect(await screen.findByRole("dialog")).toBeInTheDocument();
  expect(screen.getByTestId("loc")).not.toHaveTextContent("dcr=");

  const [stateInput] = screen.getAllByLabelText("State");
  await user.click(stateInput!);
  await user.click(await screen.findByRole("option", { name: "Cancelled" }));
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  expect(screen.getByTestId("loc")).toHaveTextContent("state=Cancelled");
});

it.each([
  [DCR_A, DCR_B],
  [DCR_B, DCR_A],
] as const)(
  "closes a locally opened DCR drawer for conflicting selectors %s then %s",
  async (first, second) => {
    const user = userEvent.setup();
    renderWithProviders(
      <RouteChromeProvider>
        <ConflictingSelectorNavigation
          route="/dcrs"
          selector="dcr"
          values={[first, second]}
          unrelated={["state", "Open"]}
        >
          <DcrsRegisterPage />
        </ConflictingSelectorNavigation>
      </RouteChromeProvider>,
      { route: "/dcrs" },
    );

    await user.click(await screen.findByRole("button", { name: "DCR-2026-0001" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "navigate to conflicting selectors" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByRole("status", { name: "Page navigation" })).toBeEmptyDOMElement();
    expect(screen.getByLabelText("Current location")).toHaveTextContent("state=Open");
    expect(screen.getByLabelText("Current location").textContent?.match(/dcr=/g)).toHaveLength(2);
    expect(screen.getByLabelText("Effective recovery key")).toHaveTextContent(/^route:\/dcrs$/);
    expect(document.title).toBe("EasySynQ — Document change requests");
  },
);

it("closing the deep-linked drawer replaces only ?dcr and preserves filters", async () => {
  const u = userEvent.setup();
  renderWithProviders(
    <>
      <DcrsRegisterPage />
      <LocationProbe />
    </>,
    { route: "/dcrs?state=Open&dcr=dcr00001-0001-0001-0001-000000000001" },
  );
  await screen.findByText(/Corrective action requires/);
  expect(screen.getByTestId("loc")).toHaveTextContent("dcr=dcr00001");
  // Mantine Drawer dismisses on Escape; closeDrawer clears the param with replace:true.
  await u.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByText(/Corrective action requires/)).toBeNull());
  expect(screen.getByTestId("loc")).not.toHaveTextContent("dcr=");
  expect(screen.getByTestId("loc")).toHaveTextContent("state=Open");
});

it("Back closes a DCR drawer opened by an external pushed URL", async () => {
  const user = userEvent.setup();
  renderWithProviders(
    <>
      <DcrsRegisterPage />
      <DcrUrlControls />
    </>,
    { route: "/dcrs" },
  );
  await screen.findByText("DCR-2026-0001");
  await user.click(screen.getByRole("button", { name: "replace dcr" }));
  expect(await screen.findByRole("dialog")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "back" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

it("filters by state", async () => {
  renderWithProviders(<DcrsRegisterPage />);
  expect(await screen.findByText("DCR-2026-0001")).toBeInTheDocument();
  // Mantine v7 Select renders a readonly input + a listbox both with aria-label="State";
  // getAllByLabelText returns [input, listbox] — click the input (index 0) to open the dropdown
  // (the CapaBoardPage precedent).
  const [stateInput] = screen.getAllByLabelText("State");
  await userEvent.click(stateInput!);
  await userEvent.click(await screen.findByRole("option", { name: "Cancelled" }));
  await waitFor(() => expect(screen.queryByText("DCR-2026-0001")).not.toBeInTheDocument());
  expect(screen.getByText("DCR-2026-0004")).toBeInTheDocument();
});

it("uses the canonical human label for the InApproval filter option", async () => {
  renderWithProviders(<DcrsRegisterPage />);
  expect(await screen.findByText("DCR-2026-0001")).toBeInTheDocument();
  const [stateInput] = screen.getAllByLabelText("State");
  await userEvent.click(stateInput!);
  expect(await screen.findByRole("option", { name: "In approval" })).toBeInTheDocument();
  expect(screen.queryByRole("option", { name: "InApproval" })).toBeNull();
});

it("shows the target document's identifier in the Target column when resolved", async () => {
  renderWithProviders(<DcrsRegisterPage />);
  const idCell = await screen.findByText("DCR-2026-0001");
  const row = idCell.closest("tr")!;
  // DCR-2026-0001's target resolves to SOP-QMS-001 / Document Control Procedure (the fixture).
  expect(within(row).getByText("SOP-QMS-001")).toBeInTheDocument();
  expect(within(row).getByText("Document Control Procedure")).toBeInTheDocument();
});

it("filters rows by the debounced search (each row isolates on its own target identity)", async () => {
  const u = userEvent.setup();
  renderWithProviders(<DcrsRegisterPage />);
  expect(await screen.findByText("DCR-2026-0001")).toBeInTheDocument();
  expect(screen.getByText("DCR-2026-0003")).toBeInTheDocument();
  const search = screen.getByLabelText("Search");
  // "SOP-QMS-001" is the target identifier of DCR-2026-0001 only → the others drop out.
  await u.type(search, "SOP-QMS-001");
  await waitFor(() => expect(screen.queryByText("DCR-2026-0003")).not.toBeInTheDocument());
  expect(screen.getByText("DCR-2026-0001")).toBeInTheDocument();
  expect(screen.queryByText("DCR-2026-0002")).not.toBeInTheDocument();
  // The reverse: a term unique to DCR-2026-0003's target isolates IT (proves per-row matching, not
  // a fixture artifact where only one row carried a target identity).
  await u.clear(search);
  await u.type(search, "Internal Audit");
  await waitFor(() => expect(screen.queryByText("DCR-2026-0001")).not.toBeInTheDocument());
  expect(screen.getByText("DCR-2026-0003")).toBeInTheDocument();
});

it("shows a no-match state when the search excludes every row", async () => {
  renderWithProviders(<DcrsRegisterPage />);
  expect(await screen.findByText("DCR-2026-0001")).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("Search"), "zzz-no-such-dcr");
  expect(await screen.findByText("No change requests match your filters.")).toBeInTheDocument();
});

it("shows a calm no-access panel on a 403", async () => {
  server.use(
    http.get("/api/v1/dcrs", () =>
      HttpResponse.json({ code: "permission_denied", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(<DcrsRegisterPage />);
  expect(await screen.findByText("No access")).toBeInTheDocument();
});

it("shows an empty state when there are no DCRs", async () => {
  server.use(http.get("/api/v1/dcrs", () => HttpResponse.json({ data: [] })));
  renderWithProviders(<DcrsRegisterPage />);
  expect(await screen.findByText("No change requests yet.")).toBeInTheDocument();
});

it("has no accessibility violations", async () => {
  const { container } = renderWithProviders(<DcrsRegisterPage />);
  await screen.findByText("DCR-2026-0001");
  expect(await axe(container)).toHaveNoViolations();
});

function grant(...keys: string[]) {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: keys.map((key) => ({ key, effect: "ALLOW" })),
      }),
    ),
  );
}

it("hides the Raise DCR button without changeRequest.create", async () => {
  renderWithProviders(<DcrsRegisterPage />);
  await screen.findByText("DCR-2026-0001");
  expect(screen.queryByRole("button", { name: "Raise DCR" })).toBeNull();
});

it("raises a DCR and opens the new request's drawer", async () => {
  grant("changeRequest.create");
  renderWithProviders(<DcrsRegisterPage />);
  await screen.findByText("DCR-2026-0001");
  await userEvent.click(screen.getByRole("button", { name: "Raise DCR" }));
  await userEvent.click(await screen.findByRole("radio", { name: "Create" }));
  await userEvent.type(screen.getByLabelText(/Reason for change/), "New WI.");
  await userEvent.click(screen.getByLabelText(/Reason class/));
  await userEvent.click(await screen.findByRole("option", { name: "Other" }));
  await userEvent.click(screen.getByRole("button", { name: "Raise" }));
  // the new DCR's drawer opens (the default detail handler resolves dcrDetailFixture)
  expect(await screen.findByText(/Corrective action requires/)).toBeInTheDocument();
});

it("has no a11y violations with the Raise button visible", async () => {
  grant("changeRequest.create");
  const { container } = renderWithProviders(<DcrsRegisterPage />);
  await screen.findByText("DCR-2026-0001");
  expect(screen.getByRole("button", { name: "Raise DCR" })).toBeInTheDocument();
  expect(await axe(container)).toHaveNoViolations();
});
