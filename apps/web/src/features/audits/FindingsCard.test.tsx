import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, test, vi } from "vitest";
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

const audit: Audit = {
  id: "au000001-0001-0001-0001-000000000001", identifier: "REC-000061",
  title: "Purchasing & Suppliers audit", plan_id: "pl000001-0001-0001-0001-000000000001",
  lead_auditor_user_id: null, state: "InProgress", started_at: "2026-05-28",
  completed_at: null, result_summary: null, created_at: "2026-05-20T09:00:00+00:00",
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
  expect(onCorrect).toHaveBeenCalledWith(expect.objectContaining({ id: "fd000001-0001-0001-0001-000000000001" }));
});
