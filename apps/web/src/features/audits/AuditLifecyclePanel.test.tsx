import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import type { Audit } from "../../lib/types";
import { AuditLifecyclePanel } from "./AuditLifecyclePanel";

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

const base: Audit = {
  id: "au000001-0001-0001-0001-000000000001", identifier: "REC-000061",
  title: "Purchasing & Suppliers audit", plan_id: "pl000001-0001-0001-0001-000000000001",
  lead_auditor_user_id: null, state: "InProgress", started_at: "2026-05-28",
  completed_at: null, result_summary: null, created_at: "2026-05-20T09:00:00+00:00",
};
const SYSTEM = { level: "SYSTEM" } as const;

test("renders the 7-node stepper with done/current/pending and aria-current on the current step", async () => {
  grant(["audit.conduct"]);
  renderWithProviders(<AuditLifecyclePanel audit={base} scope={SYSTEM} />);
  // The current node sits inside the aria-current="step" wrapper.
  const current = await screen.findByText(/● In progress/);
  expect(current.closest("[aria-current='step']")).not.toBeNull();
  // Done steps carry the ✓ glyph; pending the ○.
  expect(screen.getByText(/✓ Scheduled/)).toBeInTheDocument();
  expect(screen.getByText(/○ Reported/)).toBeInTheDocument();
});

test("offers exactly the one legal next transition, gated audit.conduct", async () => {
  grant(["audit.conduct"]);
  renderWithProviders(<AuditLifecyclePanel audit={base} scope={SYSTEM} />);
  expect(await screen.findByRole("button", { name: "Draft findings" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Issue report" })).toBeNull();
});

test("without the gate key → a calm read-only line, no button", async () => {
  renderWithProviders(<AuditLifecyclePanel audit={base} scope={SYSTEM} />);
  expect(
    await screen.findByText(/don't hold the permission to advance this audit/),
  ).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Draft findings" })).toBeNull();
});

test("the close phase gates on audit.close, not audit.conduct", async () => {
  grant(["audit.conduct"]); // conduct alone must NOT show the close-phase action
  renderWithProviders(
    <AuditLifecyclePanel audit={{ ...base, state: "Closing" }} scope={SYSTEM} />,
  );
  expect(
    await screen.findByText(/don't hold the permission to advance this audit/),
  ).toBeInTheDocument();
  grant(["audit.close"]);
  renderWithProviders(
    <AuditLifecyclePanel audit={{ ...base, state: "Closing" }} scope={SYSTEM} />,
  );
  expect(await screen.findByRole("button", { name: "Close audit" })).toBeInTheDocument();
});

test("409 audit_close_blocked surfaces the server message calmly", async () => {
  grant(["audit.close"]);
  server.use(
    http.post("/api/v1/audits/:id/close", () =>
      HttpResponse.json(
        { code: "audit_close_blocked", title: "Cannot close: 1 live NC finding(s) without a Closed CAPA (close the CAPA, or correct the finding NC→Observation/OFI)" },
        { status: 409 },
      ),
    ),
  );
  const u = userEvent.setup();
  renderWithProviders(
    <AuditLifecyclePanel audit={{ ...base, state: "Closing" }} scope={SYSTEM} />,
  );
  await u.click(await screen.findByRole("button", { name: "Close audit" }));
  expect(await screen.findByText(/Cannot close: 1 live NC finding/)).toBeInTheDocument();
});

test("a Closed audit shows the terminal line (no action)", async () => {
  grant(["audit.close"]);
  renderWithProviders(
    <AuditLifecyclePanel
      audit={{ ...base, state: "Closed", completed_at: "2026-06-01" }}
      scope={SYSTEM}
    />,
  );
  expect(await screen.findByText(/Audit closed/)).toBeInTheDocument();
  expect(screen.queryByRole("button")).toBeNull();
});
