import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, test, vi } from "vitest";
import { useLocation } from "react-router-dom";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import type { Audit } from "../../lib/types";
import { FindingsCard } from "./FindingsCard";

function grant(keys: string[]) {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: keys.map((key) => ({ key, effect: "ALLOW", source: null })),
      }),
    ),
  );
}

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname + loc.search}</div>;
}

const audit: Audit = {
  id: "au000001-0001-0001-0001-000000000001",
  identifier: "REC-000061",
  title: "Purchasing & Suppliers audit",
  plan_id: "pl000001-0001-0001-0001-000000000001",
  lead_auditor_user_id: null,
  state: "InProgress",
  started_at: "2026-05-28",
  completed_at: null,
  result_summary: null,
  created_at: "2026-05-20T09:00:00+00:00",
};
const SYSTEM = { level: "SYSTEM" } as const;
const noop = { onLog: () => {}, onCorrect: () => {} };

test("renders the findings created-asc with per-row content; Log gated on finding.create", async () => {
  renderWithProviders(<FindingsCard audit={audit} scope={SYSTEM} {...noop} />);
  expect(await screen.findByText("REC-000062")).toBeInTheDocument();
  expect(screen.getByText(/Findings \(4\)/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Log finding/ })).toBeNull();
  grant(["finding.create"]);
  renderWithProviders(<FindingsCard audit={audit} scope={SYSTEM} {...noop} />);
  expect((await screen.findAllByRole("button", { name: /Log finding/ })).length).toBeGreaterThan(0);
});

test("Closed audit: Log/Correct hidden with the closed note", async () => {
  grant(["finding.create"]);
  renderWithProviders(
    <FindingsCard audit={{ ...audit, state: "Closed" }} scope={SYSTEM} {...noop} />,
  );
  expect(await screen.findByText(/closed with the audit/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Log finding/ })).toBeNull();
  expect(screen.queryByRole("button", { name: /^Correct/ })).toBeNull();
});

test("close-readiness note: Reported/Closing + 1 blocking NC (live, CAPA not Closed)", async () => {
  // Fixtures: fd000001 NC live, its CAPA ca000001 is RootCause → 1 blocker. The corrected NC
  // (fd000003, superseded) and the OFI must NOT count.
  renderWithProviders(
    <FindingsCard audit={{ ...audit, state: "Closing" }} scope={SYSTEM} {...noop} />,
  );
  expect(
    await screen.findByText(/1 live NC finding without a Closed CAPA — closing will be blocked/),
  ).toBeInTheDocument();
});

test("the note is omitted pre-Reported, and when capa.read is denied (degrade)", async () => {
  renderWithProviders(<FindingsCard audit={audit} scope={SYSTEM} {...noop} />); // InProgress
  await screen.findByText("REC-000062");
  expect(screen.queryByText(/closing will be blocked/)).toBeNull();
  server.use(
    http.get("/api/v1/capas", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(
    <FindingsCard audit={{ ...audit, state: "Closing" }} scope={SYSTEM} {...noop} />,
  );
  await screen.findByText("REC-000062");
  expect(screen.queryByText(/closing will be blocked/)).toBeNull();
});

test("finding.read denied → a calm no-access note inside the card", async () => {
  server.use(
    http.get("/api/v1/audits/:id/findings", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(<FindingsCard audit={audit} scope={SYSTEM} {...noop} />);
  expect(await screen.findByText(/don't have access to findings/)).toBeInTheDocument();
});

test("per-row CAPA chips come from the capa cross-ref; Correct fires onCorrect", async () => {
  grant(["finding.create"]);
  const onCorrect = vi.fn();
  const u = userEvent.setup();
  renderWithProviders(
    <FindingsCard audit={audit} scope={SYSTEM} onLog={() => {}} onCorrect={onCorrect} />,
  );
  const row = (await screen.findByText("REC-000062")).closest("[data-finding]") as HTMLElement;
  expect(within(row).getByText(/CAPA: Root cause/)).toBeInTheDocument();
  await u.click(within(row).getByRole("button", { name: /Correct/ }));
  expect(onCorrect).toHaveBeenCalledWith(
    expect.objectContaining({ id: "fd000001-0001-0001-0001-000000000001" }),
  );
});

// ---- S-improvement-3b raise-initiative affordance ----
// improvement.manage is resolved at the audit's auditee-process scope. The default test scope is
// SYSTEM, so a SYSTEM grant satisfies usePermissions(SYSTEM). A PROCESS-scoped grant test below
// proves the gate asks at the real scope (the ImprovementRegisterPage PROCESS-scoped pattern).
function rowOf(identifier: string): Promise<HTMLElement> {
  return screen.findByText(identifier).then((el) => el.closest("[data-finding]") as HTMLElement);
}

test("Raise initiative shows for OFI + OBSERVATION findings with improvement.manage", async () => {
  grant(["improvement.manage"]);
  renderWithProviders(<FindingsCard audit={audit} scope={SYSTEM} {...noop} />);
  // fd000002 is OFI, fd000004 is OBSERVATION → both improvable.
  const ofi = await rowOf("REC-000063");
  expect(within(ofi).getByRole("button", { name: "Raise initiative" })).toBeInTheDocument();
  const obs = await rowOf("REC-000065");
  expect(within(obs).getByRole("button", { name: "Raise initiative" })).toBeInTheDocument();
});

test("Raise initiative is hidden for an NC finding", async () => {
  grant(["improvement.manage"]);
  renderWithProviders(<FindingsCard audit={audit} scope={SYSTEM} {...noop} />);
  const nc = await rowOf("REC-000062"); // fd000001 is a live NC
  expect(within(nc).queryByRole("button", { name: "Raise initiative" })).toBeNull();
});

test("Raise initiative is hidden for a superseded finding", async () => {
  grant(["improvement.manage"]);
  renderWithProviders(<FindingsCard audit={audit} scope={SYSTEM} {...noop} />);
  // fd000003 (REC-000064) is an NC superseded by correction — hidden on both counts.
  const sup = await rowOf("REC-000064");
  expect(within(sup).queryByRole("button", { name: "Raise initiative" })).toBeNull();
});

test("Raise initiative is hidden without improvement.manage", async () => {
  renderWithProviders(<FindingsCard audit={audit} scope={SYSTEM} {...noop} />);
  await rowOf("REC-000063"); // the OFI row exists…
  expect(screen.queryByRole("button", { name: "Raise initiative" })).toBeNull(); // …but no Raise
});

test("honors a PROCESS-scoped improvement.manage grant for the OFI affordance", async () => {
  const PROC = "pr000099-0099-0099-0099-000000000099";
  // ALLOW only at this PROCESS scope (empty at SYSTEM). The card asks at scope={PROCESS,PROC}; a
  // SYSTEM-only check would get [] and hide the affordance.
  server.use(
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
  renderWithProviders(
    <FindingsCard audit={audit} scope={{ level: "PROCESS", id: PROC }} {...noop} />,
  );
  const ofi = await rowOf("REC-000063");
  expect(within(ofi).getByRole("button", { name: "Raise initiative" })).toBeInTheDocument();
});

test("clicking Raise initiative drives the spawn modal → navigates to the new initiative", async () => {
  grant(["improvement.manage"]);
  const u = userEvent.setup();
  renderWithProviders(
    <>
      <FindingsCard audit={audit} scope={SYSTEM} {...noop} />
      <LocationProbe />
    </>,
  );
  const ofi = await rowOf("REC-000063");
  await u.click(within(ofi).getByRole("button", { name: "Raise initiative" }));
  // The modal's submit is "Raise" (distinct from the trigger's "Raise initiative").
  await u.type(await screen.findByLabelText(/^Title/), "Automate the supplier scorecard");
  await u.click(screen.getByRole("button", { name: "Raise" }));
  await waitFor(() =>
    expect(screen.getByTestId("loc")).toHaveTextContent(
      "/improvement?initiative=10000000-0000-0000-0000-0000000000f1",
    ),
  );
});
