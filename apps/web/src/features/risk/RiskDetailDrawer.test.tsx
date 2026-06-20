import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { riskListFixture, riskSpawnedCapaFixture } from "../../test/msw/handlers";
import { RiskDetailDrawer } from "./RiskDetailDrawer";

const CRITICAL = "ab000001-0001-0001-0001-000000000001"; // risk, unlinked, untreated
const HIGH = "ab000002-0002-0002-0002-000000000002"; // risk, treated
const OPP = "ab000003-0003-0003-0003-000000000003"; // opportunity
const LINKED = "ab000004-0004-0004-0004-000000000004"; // risk, linked_capa_id set

function grant(...keys: string[]) {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: keys.map((key) => ({ key, effect: "ALLOW", source: "test" })),
      }),
    ),
  );
}

const noop = () => {};

it("shows the score, band, and treatment for a risk", async () => {
  renderWithProviders(<RiskDetailDrawer riskId={HIGH} onClose={noop} headEditable={false} />);
  const dialog = await screen.findByRole("dialog");
  // wait for the async useRisk load before asserting the body
  expect(
    await within(dialog).findByText("Untrained operators on the new line"),
  ).toBeInTheDocument();
  expect(within(dialog).getByText(/likelihood 3 × severity 4 = rating 12/i)).toBeInTheDocument();
  expect(within(dialog).getByText("Roll out the training matrix")).toBeInTheDocument();
});

it("spawn seam: an unlinked risk with capa.create shows the treat button; spawning links it", async () => {
  grant("capa.create");
  let spawned = false;
  const unlinked = riskListFixture.data[0]!; // CRITICAL, unlinked
  server.use(
    http.get("/api/v1/risks/:id", () =>
      HttpResponse.json(
        spawned ? { ...unlinked, linked_capa_id: riskSpawnedCapaFixture.id } : unlinked,
      ),
    ),
    http.post("/api/v1/risks/:id/capa", () => {
      spawned = true;
      return HttpResponse.json(riskSpawnedCapaFixture, { status: 201 });
    }),
  );
  const user = userEvent.setup();
  renderWithProviders(<RiskDetailDrawer riskId={CRITICAL} onClose={noop} headEditable={false} />);
  const dialog = await screen.findByRole("dialog");
  const treat = await within(dialog).findByRole("button", { name: /treat.*spawn capa/i });
  await user.click(treat);
  // after the spawn + refetch, the linked-CAPA reference appears and the treat button is gone
  await waitFor(() =>
    expect(within(dialog).getByText(/open the linked capa/i)).toBeInTheDocument(),
  );
  expect(
    within(dialog).queryByRole("button", { name: /treat.*spawn capa/i }),
  ).not.toBeInTheDocument();
});

it("spawn seam: a linked risk shows the CAPA link and no treat button", async () => {
  grant("capa.create");
  renderWithProviders(<RiskDetailDrawer riskId={LINKED} onClose={noop} headEditable={false} />);
  const dialog = await screen.findByRole("dialog");
  const link = await within(dialog).findByRole("link", { name: /open the linked capa/i });
  expect(link).toHaveAttribute("href", expect.stringContaining("/capa?capa="));
  expect(
    within(dialog).queryByRole("button", { name: /treat.*spawn capa/i }),
  ).not.toBeInTheDocument();
});

it("spawn seam: an opportunity has no corrective-action section", async () => {
  grant("capa.create"); // even WITH capa.create, an opportunity offers no spawn (the server 422s it)
  renderWithProviders(<RiskDetailDrawer riskId={OPP} onClose={noop} headEditable={false} />);
  const dialog = await screen.findByRole("dialog");
  expect(await within(dialog).findByText("Automate the inspection step")).toBeInTheDocument();
  expect(within(dialog).queryByText(/corrective action/i)).not.toBeInTheDocument();
  expect(
    within(dialog).queryByRole("button", { name: /treat.*spawn capa/i }),
  ).not.toBeInTheDocument();
});

it("spawn button is hidden without capa.create (gated on the key, never shown to a reader)", async () => {
  // default /me/permissions = empty → no capa.create
  renderWithProviders(<RiskDetailDrawer riskId={CRITICAL} onClose={noop} headEditable={false} />);
  const dialog = await screen.findByRole("dialog");
  expect(await within(dialog).findByText(/no corrective action raised/i)).toBeInTheDocument();
  expect(
    within(dialog).queryByRole("button", { name: /treat.*spawn capa/i }),
  ).not.toBeInTheDocument();
});

it("edit is gated on headEditable AND register.manage", async () => {
  grant("register.manage");
  const { unmount } = renderWithProviders(
    <RiskDetailDrawer riskId={HIGH} onClose={noop} headEditable />,
  );
  const dialog = await screen.findByRole("dialog");
  expect(await within(dialog).findByRole("button", { name: "Edit risk" })).toBeInTheDocument();
  unmount();

  // head not editable → no edit button, a calm read-only note instead
  renderWithProviders(<RiskDetailDrawer riskId={HIGH} onClose={noop} headEditable={false} />);
  const dialog2 = await screen.findByRole("dialog");
  expect(await within(dialog2).findByText(/effective \(read-only\)/i)).toBeInTheDocument();
  expect(within(dialog2).queryByRole("button", { name: "Edit risk" })).not.toBeInTheDocument();
});
