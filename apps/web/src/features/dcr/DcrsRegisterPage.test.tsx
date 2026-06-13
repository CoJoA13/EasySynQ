import { http, HttpResponse } from "msw";
import { axe } from "jest-axe";
import { expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { DcrsRegisterPage } from "./DcrsRegisterPage";

it("lists change requests and opens the drawer when an identifier is clicked", async () => {
  renderWithProviders(<DcrsRegisterPage />);
  expect(await screen.findByText("DCR-2026-0001")).toBeInTheDocument(); // first assertion waits for skeleton
  expect(screen.getByText("DCR-2026-0002")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "DCR-2026-0001" }));
  expect(await screen.findByText(/Corrective action requires/)).toBeInTheDocument(); // drawer detail loaded
});

it("opens the drawer on a ?dcr=<id> deep-link", async () => {
  renderWithProviders(<DcrsRegisterPage />, { route: "/dcrs?dcr=dcr00001-0001-0001-0001-000000000001" });
  expect(await screen.findByText(/Corrective action requires/)).toBeInTheDocument();
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

it("shows a calm no-access panel on a 403", async () => {
  server.use(http.get("/api/v1/dcrs", () => HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 })));
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
