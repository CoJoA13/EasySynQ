import { http, HttpResponse } from "msw";
import { axe } from "jest-axe";
import { expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "react-router-dom";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { DcrsRegisterPage } from "./DcrsRegisterPage";

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname + loc.search}</div>;
}

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

it("closing the deep-linked drawer clears the ?dcr param", async () => {
  const u = userEvent.setup();
  renderWithProviders(
    <>
      <DcrsRegisterPage />
      <LocationProbe />
    </>,
    { route: "/dcrs?dcr=dcr00001-0001-0001-0001-000000000001" },
  );
  await screen.findByText(/Corrective action requires/);
  expect(screen.getByTestId("loc")).toHaveTextContent("dcr=dcr00001");
  // Mantine Drawer dismisses on Escape; closeDrawer clears the param with replace:true.
  await u.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByText(/Corrective action requires/)).toBeNull());
  expect(screen.getByTestId("loc")).not.toHaveTextContent("dcr=");
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
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
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
