import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { useLocation } from "react-router-dom";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { ImprovementRegisterPage } from "./ImprovementRegisterPage";

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname + loc.search}</div>;
}

function grantManage() {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [{ key: "improvement.manage", effect: "ALLOW", source: null }],
      }),
    ),
  );
}

test("lists initiatives with stage badge + opens the detail drawer on identifier click", async () => {
  renderWithProviders(<ImprovementRegisterPage />, { route: "/improvement" });
  expect(await screen.findByText("IMP-2026-0001")).toBeInTheDocument();
  expect(screen.getByText("Reduce supplier onboarding lead time")).toBeInTheDocument();
  expect(screen.getByLabelText("State: Closed")).toBeInTheDocument(); // IMP-2026-0003 row badge

  await userEvent.click(screen.getByRole("button", { name: "IMP-2026-0001" }));
  // The drawer fetches the detail + the SEPARATE stage-events endpoint; the timeline comment is
  // drawer-only (never in the register table), so its appearance proves the drawer opened.
  expect(await screen.findByText("Kicking off the work.")).toBeInTheDocument();
});

test("deep-links the drawer open from ?initiative=<id> on mount", async () => {
  renderWithProviders(<ImprovementRegisterPage />, {
    route: "/improvement?initiative=10000000-0000-0000-0000-000000000002",
  });
  expect(await screen.findByText("Kicking off the work.")).toBeInTheDocument();
});

test("closing the deep-linked drawer clears the ?initiative param", async () => {
  const u = userEvent.setup();
  renderWithProviders(
    <>
      <ImprovementRegisterPage />
      <LocationProbe />
    </>,
    { route: "/improvement?initiative=10000000-0000-0000-0000-000000000002" },
  );
  await screen.findByText("Kicking off the work.");
  expect(screen.getByTestId("loc")).toHaveTextContent("initiative=10000000");
  await u.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByText("Kicking off the work.")).toBeNull());
  expect(screen.getByTestId("loc")).not.toHaveTextContent("initiative=");
});

test("filtering by stage narrows the rows", async () => {
  renderWithProviders(<ImprovementRegisterPage />, { route: "/improvement" });
  await screen.findByText("IMP-2026-0001");
  const [stageInput] = screen.getAllByLabelText("Stage");
  await userEvent.click(stageInput!);
  await userEvent.click(await screen.findByRole("option", { name: "Closed" }));
  expect(screen.getByText("IMP-2026-0003")).toBeInTheDocument();
  expect(screen.queryByText("IMP-2026-0001")).toBeNull();
});

test("filtering by source narrows the rows", async () => {
  renderWithProviders(<ImprovementRegisterPage />, { route: "/improvement" });
  await screen.findByText("IMP-2026-0001");
  const [sourceInput] = screen.getAllByLabelText("Source");
  await userEvent.click(sourceInput!);
  await userEvent.click(await screen.findByRole("option", { name: "OFI finding" }));
  expect(screen.getByText("IMP-2026-0002")).toBeInTheDocument();
  expect(screen.queryByText("IMP-2026-0001")).toBeNull();
});

test("the debounced search narrows by title", async () => {
  renderWithProviders(<ImprovementRegisterPage />, { route: "/improvement" });
  await screen.findByText("IMP-2026-0001");
  await userEvent.type(screen.getByLabelText("Search"), "calibration");
  await waitFor(() => expect(screen.queryByText("IMP-2026-0001")).toBeNull());
  expect(screen.getByText("IMP-2026-0002")).toBeInTheDocument();
});

test("shows a no-match message when filters exclude everything", async () => {
  renderWithProviders(<ImprovementRegisterPage />, { route: "/improvement" });
  await screen.findByText("IMP-2026-0001");
  await userEvent.type(screen.getByLabelText("Search"), "zzzznomatch");
  expect(await screen.findByText("No initiatives match your filters.")).toBeInTheDocument();
});

test("renders a calm no-access panel on a 403", async () => {
  server.use(
    http.get("/api/v1/improvement-initiatives", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(<ImprovementRegisterPage />, { route: "/improvement" });
  expect(await screen.findByText(/don't have access/)).toBeInTheDocument();
});

test("renders the empty state when there are no initiatives", async () => {
  server.use(http.get("/api/v1/improvement-initiatives", () => HttpResponse.json({ data: [] })));
  renderWithProviders(<ImprovementRegisterPage />, { route: "/improvement" });
  expect(await screen.findByText("No improvement initiatives yet.")).toBeInTheDocument();
});

test("no axe violations", async () => {
  const { container } = renderWithProviders(<ImprovementRegisterPage />, { route: "/improvement" });
  await screen.findByText("IMP-2026-0001");
  expect(await axe(container)).toHaveNoViolations();
});

test("hides the New initiative button without improvement.manage", async () => {
  renderWithProviders(<ImprovementRegisterPage />, { route: "/improvement" });
  await screen.findByText("IMP-2026-0001");
  expect(screen.queryByRole("button", { name: "New initiative" })).toBeNull();
});

test("shows New initiative when the caller holds improvement.manage and opens the modal", async () => {
  grantManage();
  const u = userEvent.setup();
  renderWithProviders(<ImprovementRegisterPage />, { route: "/improvement" });
  const raise = await screen.findByRole("button", { name: "New initiative" });
  await u.click(raise);
  expect(await screen.findByLabelText(/^Title/)).toBeInTheDocument();
});

test("shows the FSM transition affordance in the drawer only with improvement.manage", async () => {
  // The detail fixture is InProgress → the cockpit offers "Mark completed" (a one-click move). Without
  // the manage key the cockpit renders nothing; the gate is per-key (SYSTEM fallback in v1).
  grantManage();
  renderWithProviders(<ImprovementRegisterPage />, {
    route: "/improvement?initiative=10000000-0000-0000-0000-000000000002",
  });
  expect(await screen.findByRole("button", { name: "Mark completed" })).toBeInTheDocument();
});

test("a Close move requires a comment before the confirm button enables", async () => {
  grantManage();
  // Override the detail to a Completed initiative so the cockpit offers "Close initiative".
  server.use(
    http.get("/api/v1/improvement-initiatives/:id", () =>
      HttpResponse.json({
        id: "10000000-0000-0000-0000-000000000002",
        identifier: "IMP-2026-0002",
        title: "Improve calibration record completeness",
        description: null,
        target_outcome: "100% calibration records on file.",
        source: "OFI",
        source_link_id: "30000000-0000-0000-0000-000000000001",
        process_id: null,
        owner_user_id: null,
        stage: "Completed",
        opened_at: "2026-06-12T09:00:00Z",
        closed_at: null,
        created_by: "20000000-0000-0000-0000-0000000000aa",
        created_at: "2026-06-12T09:00:00Z",
        updated_at: "2026-06-13T09:00:00Z",
      }),
    ),
  );
  const u = userEvent.setup();
  renderWithProviders(<ImprovementRegisterPage />, {
    route: "/improvement?initiative=10000000-0000-0000-0000-000000000002",
  });
  await u.click(await screen.findByRole("button", { name: "Close initiative" }));
  const confirm = await screen.findByRole("button", { name: "Confirm close" });
  expect(confirm).toBeDisabled();
  await u.type(screen.getByLabelText(/^Comment/), "Lead time cut to 14 days.");
  expect(confirm).toBeEnabled();
});
