import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { useLocation, useNavigate } from "react-router-dom";
import { expect, test } from "vitest";
import { RouteAnnouncement, RouteChromeProvider, useRouteChrome } from "../../lib/routeChrome";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { expectResponsiveTable } from "../../test/responsiveTable";
import { ConflictingSelectorNavigation } from "../../test/ConflictingSelectorNavigation";
import { ImprovementRegisterPage } from "./ImprovementRegisterPage";

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname + loc.search}</div>;
}

const INITIATIVE_A = "10000000-0000-0000-0000-000000000001";
const INITIATIVE_B = "10000000-0000-0000-0000-000000000002";

function InitiativeUrlControls() {
  const navigate = useNavigate();
  return (
    <>
      <button onClick={() => navigate(`/improvement?stage=Open&initiative=${INITIATIVE_B}`)}>
        replace initiative
      </button>
      <button onClick={() => navigate("/improvement?stage=Open")}>remove initiative</button>
      <button onClick={() => navigate(-1)}>back</button>
    </>
  );
}

function InitiativeDetailChrome({ children }: { children: React.ReactNode }) {
  useRouteChrome();
  return (
    <>
      {children}
      <RouteAnnouncement />
    </>
  );
}

function recordInitiativeDetailRequests() {
  const ids: string[] = [];
  const listener = ({ request }: { request: Request }) => {
    const id = new URL(request.url).pathname.match(
      /^\/api\/v1\/improvement-initiatives\/([^/]+)$/,
    )?.[1];
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

test("keeps the loading and loaded register at the same xl width", async () => {
  let release: (() => void) | undefined;
  const blocked = new Promise<void>((resolve) => {
    release = resolve;
  });
  server.use(
    http.get("/api/v1/improvement-initiatives", async () => {
      await blocked;
      return HttpResponse.json({ data: [] });
    }),
  );

  renderWithProviders(<ImprovementRegisterPage />, { route: "/improvement" });
  expect(
    containerSizeFor(screen.getByRole("status", { name: "Loading improvement initiatives" })),
  ).toBe("var(--container-size-xl)");
  act(() => release?.());
  expect(containerSizeFor(await screen.findByRole("heading", { name: "Improvement" }))).toBe(
    "var(--container-size-xl)",
  );
});

test("contains the complete improvement table in one 920 px scroll region", async () => {
  renderWithProviders(<ImprovementRegisterPage />, { route: "/improvement" });
  await screen.findByText("IMP-2026-0001");
  const table = expectResponsiveTable(920);
  expect(within(table).getAllByRole("columnheader")).toHaveLength(6);
  expect(within(table).getAllByRole("button", { name: "IMP-2026-0001" })).toHaveLength(1);
});

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

test("synchronizes a URL-seeded improvement drawer across selector replacement and removal", async () => {
  const user = userEvent.setup();
  const requests = recordInitiativeDetailRequests();
  renderWithProviders(
    <RouteChromeProvider>
      <InitiativeDetailChrome>
        <ImprovementRegisterPage />
        <InitiativeUrlControls />
      </InitiativeDetailChrome>
    </RouteChromeProvider>,
    { route: `/improvement?stage=Open&initiative=${INITIATIVE_A}` },
  );

  const dialog = await screen.findByRole("dialog");
  await waitFor(() => expect(requests.ids).toContain(INITIATIVE_A));
  expect(await screen.findByText("Kicking off the work.")).toBeInTheDocument();
  expect(document.title).toBe("EasySynQ — Improvement details");
  expect(document.title).not.toContain(INITIATIVE_A);
  expect(screen.getByRole("status", { name: "Page navigation" })).not.toHaveTextContent(
    INITIATIVE_A,
  );
  await waitFor(() => expect(dialog).toContainElement(document.activeElement as HTMLElement));

  await user.click(screen.getByRole("button", { name: "replace initiative" }));
  expect(await screen.findByRole("dialog")).toBeInTheDocument();
  await waitFor(() => expect(requests.ids).toContain(INITIATIVE_B));
  expect(document.title).not.toContain(INITIATIVE_B);

  await user.click(screen.getByRole("button", { name: "remove initiative" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(document.title).toBe("EasySynQ — Improvement");
  requests.stop();
});

test("keeps a locally opened improvement drawer open when a filter updates the URL", async () => {
  const user = userEvent.setup();
  renderWithProviders(
    <>
      <ImprovementRegisterPage />
      <LocationProbe />
    </>,
    { route: "/improvement" },
  );
  await user.click(await screen.findByRole("button", { name: "IMP-2026-0001" }));
  expect(await screen.findByRole("dialog")).toBeInTheDocument();
  expect(screen.getByTestId("loc")).not.toHaveTextContent("initiative=");

  const [stageInput] = screen.getAllByLabelText("Stage");
  await user.click(stageInput!);
  await user.click(await screen.findByRole("option", { name: "Closed" }));
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  expect(screen.getByTestId("loc")).toHaveTextContent("stage=Closed");
});

test.each([
  [INITIATIVE_A, INITIATIVE_B],
  [INITIATIVE_B, INITIATIVE_A],
] as const)(
  "closes a locally opened improvement drawer for conflicting selectors %s then %s",
  async (first, second) => {
    const user = userEvent.setup();
    renderWithProviders(
      <RouteChromeProvider>
        <ConflictingSelectorNavigation
          route="/improvement"
          selector="initiative"
          values={[first, second]}
          unrelated={["stage", "Open"]}
        >
          <ImprovementRegisterPage />
        </ConflictingSelectorNavigation>
      </RouteChromeProvider>,
      { route: "/improvement" },
    );

    await user.click(await screen.findByRole("button", { name: "IMP-2026-0001" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "navigate to conflicting selectors" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByRole("status", { name: "Page navigation" })).toBeEmptyDOMElement();
    expect(screen.getByLabelText("Current location")).toHaveTextContent("stage=Open");
    expect(
      screen.getByLabelText("Current location").textContent?.match(/initiative=/g),
    ).toHaveLength(2);
    expect(screen.getByLabelText("Effective recovery key")).toHaveTextContent(
      /^route:\/improvement$/,
    );
    expect(document.title).toBe("EasySynQ — Improvement");
  },
);

test("closing the deep-linked drawer replaces only ?initiative and preserves filters", async () => {
  const u = userEvent.setup();
  renderWithProviders(
    <>
      <ImprovementRegisterPage />
      <LocationProbe />
    </>,
    { route: "/improvement?stage=Open&initiative=10000000-0000-0000-0000-000000000002" },
  );
  await screen.findByText("Kicking off the work.");
  expect(screen.getByTestId("loc")).toHaveTextContent("initiative=10000000");
  await u.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByText("Kicking off the work.")).toBeNull());
  expect(screen.getByTestId("loc")).not.toHaveTextContent("initiative=");
  expect(screen.getByTestId("loc")).toHaveTextContent("stage=Open");
});

test("Back closes an improvement drawer opened by an external pushed URL", async () => {
  const user = userEvent.setup();
  renderWithProviders(
    <>
      <ImprovementRegisterPage />
      <InitiativeUrlControls />
    </>,
    { route: "/improvement" },
  );
  await screen.findByText("IMP-2026-0001");
  await user.click(screen.getByRole("button", { name: "replace initiative" }));
  expect(await screen.findByRole("dialog")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "back" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
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
      HttpResponse.json({ code: "permission_denied", title: "Forbidden" }, { status: 403 }),
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

test("honors a PROCESS-scoped improvement.manage grant in the cockpit", async () => {
  const PROC = "50000000-0000-0000-0000-0000000000cc";
  // The initiative is process-scoped; the grant is ALLOW only at that PROCESS scope (empty at SYSTEM).
  // The cockpit shows only if it asks at the initiative's scope (the CAPA AdvancePanel pattern) — a
  // SYSTEM-only check would get [] here and render nothing.
  server.use(
    http.get("/api/v1/improvement-initiatives/:id", () =>
      HttpResponse.json({
        id: "10000000-0000-0000-0000-000000000002",
        identifier: "IMP-2026-0002",
        title: "Improve calibration record completeness",
        description: null,
        target_outcome: null,
        source: "OFI",
        source_link_id: "30000000-0000-0000-0000-000000000001",
        process_id: PROC,
        owner_user_id: null,
        stage: "InProgress",
        opened_at: "2026-06-12T09:00:00Z",
        closed_at: null,
        created_by: "20000000-0000-0000-0000-0000000000aa",
        created_at: "2026-06-12T09:00:00Z",
        updated_at: null,
      }),
    ),
    http.get("/api/v1/me/permissions", ({ request }) => {
      const url = new URL(request.url);
      const ok =
        url.searchParams.get("scope_level") === "PROCESS" &&
        url.searchParams.get("scope_id") === PROC;
      return HttpResponse.json({
        scope: { level: url.searchParams.get("scope_level") ?? "SYSTEM", selector: null },
        permissions: ok ? [{ key: "improvement.manage", effect: "ALLOW", source: null }] : [],
      });
    }),
  );
  renderWithProviders(<ImprovementRegisterPage />, {
    route: "/improvement?initiative=10000000-0000-0000-0000-000000000002",
  });
  expect(await screen.findByRole("button", { name: "Mark completed" })).toBeInTheDocument();
});

test("a Cancel move requires a comment before the confirm button enables", async () => {
  // The detail fixture is InProgress → the cockpit offers "Cancel initiative" (a comment-required move).
  grantManage();
  const u = userEvent.setup();
  renderWithProviders(<ImprovementRegisterPage />, {
    route: "/improvement?initiative=10000000-0000-0000-0000-000000000002",
  });
  await u.click(await screen.findByRole("button", { name: "Cancel initiative" }));
  const confirm = await screen.findByRole("button", { name: "Confirm cancellation" });
  expect(confirm).toBeDisabled();
  await u.type(screen.getByLabelText(/^Comment/), "Superseded by a broader program.");
  expect(confirm).toBeEnabled();
});

test("renders a calm error in the drawer when the detail load fails", async () => {
  server.use(
    http.get("/api/v1/improvement-initiatives/:id", () =>
      HttpResponse.json({ code: "error", title: "Server error" }, { status: 500 }),
    ),
  );
  renderWithProviders(<ImprovementRegisterPage />, {
    route: "/improvement?initiative=10000000-0000-0000-0000-000000000002",
  });
  expect(await screen.findByText(/Couldn't load this initiative/)).toBeInTheDocument();
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
