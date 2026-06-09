import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { CapaBoardPage } from "./CapaBoardPage";

test("groups CAPAs into lifecycle columns (ActionPlan+Implement merge; Rejected in Closed)", async () => {
  renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  const action = await screen.findByRole("group", { name: "Action" });
  expect(within(action).getByText(/Scrap-rate spike/)).toBeInTheDocument();
  const closed = screen.getByRole("group", { name: "Closed" });
  expect(within(closed).getByText(/Duplicate complaint/)).toBeInTheDocument();
});

test("the Open tile counts non-terminal CAPAs and by-source breaks down", async () => {
  renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  expect(await screen.findByText("5")).toBeInTheDocument();
  expect(screen.getByText("Audit · 3")).toBeInTheDocument();
});

test("filtering by severity narrows the cards", async () => {
  renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  await screen.findByText(/Supplier re-evaluation/);
  // Mantine v7 Select renders a readonly input + a listbox both with aria-label="Severity".
  // getAllByLabelText returns [input, listbox]; click the input (index 0) to open the dropdown.
  const [severityInput] = screen.getAllByLabelText("Severity");
  await userEvent.click(severityInput!);
  await userEvent.click(await screen.findByRole("option", { name: "Critical" }));
  expect(screen.getByText(/Delivered batch missing CoA/)).toBeInTheDocument();
  expect(screen.queryByText(/Supplier re-evaluation/)).toBeNull();
});

test("opening a card shows the detail drawer", async () => {
  renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  await userEvent.click(await screen.findByRole("button", { name: /REC-000031/ }));
  expect(await screen.findByText("Closed-loop thread")).toBeInTheDocument();
});

test("a list-view row opens the drawer via keyboard (Enter), not mouse-only", async () => {
  renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  await screen.findByText(/Supplier re-evaluation/);
  await userEvent.click(screen.getByRole("radio", { name: "List" }));
  const row = await screen.findByRole("row", { name: /REC-000031/ });
  row.focus();
  await userEvent.keyboard("{Enter}");
  expect(await screen.findByText("Closed-loop thread")).toBeInTheDocument();
});

test("renders a calm no-access panel on a 403", async () => {
  server.use(
    http.get("/api/v1/capas", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  expect(await screen.findByText(/don't have access/)).toBeInTheDocument();
});

test("no axe violations", async () => {
  const { container } = renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  await screen.findByText(/Supplier re-evaluation/);
  expect(await axe(container)).toHaveNoViolations();
});

test("shows the Raise CAPA button when the caller holds capa.create and opens the modal", async () => {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [{ key: "capa.create", effect: "ALLOW", source: null }],
      }),
    ),
  );
  const u = userEvent.setup();
  renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  const raise = await screen.findByRole("button", { name: /Raise CAPA/ });
  await u.click(raise);
  expect(await screen.findByLabelText(/^Title/)).toBeInTheDocument();
});
