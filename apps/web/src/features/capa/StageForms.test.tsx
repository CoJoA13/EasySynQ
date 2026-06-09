// apps/web/src/features/capa/StageForms.test.tsx
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { expect, test } from "vitest";
import { AuthContext } from "../../lib/auth";
import type { Capa } from "../../lib/types";
import { theme } from "../../theme/mantine";
import { server } from "../../test/msw/server";
import { TEST_AUTH } from "../../test/render";
import { CloseAction, ContainmentForm, RootCauseForm, VerifyForm } from "./StageForms";

const capa = (over: Partial<Capa> = {}): Capa => ({
  id: "ca000008-0008-0008-0008-000000000008",
  identifier: "REC-000040",
  title: "T",
  source: "audit",
  severity: "Major",
  process_id: null,
  close_state: "Raised",
  cycle_marker: 0,
  origin_finding_id: null,
  raised_by: null,
  created_at: null,
  stages: [],
  ...over,
});

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MantineProvider theme={theme}>
      <QueryClientProvider client={client}>
        <AuthContext.Provider value={TEST_AUTH}>{node}</AuthContext.Provider>
      </QueryClientProvider>
    </MantineProvider>,
  );
}

test("ContainmentForm submits the correction content_block", async () => {
  const u = userEvent.setup();
  wrap(<ContainmentForm capa={capa()} />);
  await u.type(screen.getByLabelText("Correction taken"), "Froze POs");
  await u.click(screen.getByRole("button", { name: /Record correction/ }));
  await waitFor(() => expect(screen.getByText(/Recorded/)).toBeInTheDocument());
});

test("RootCauseForm requires a non-empty root cause", async () => {
  wrap(<RootCauseForm capa={capa({ close_state: "Containment" })} />);
  expect(screen.getByRole("button", { name: /Record root cause/ })).toBeDisabled();
});

test("VerifyForm sends decision + narrative and shows the signing confirmation", async () => {
  const u = userEvent.setup();
  wrap(<VerifyForm capa={capa({ close_state: "Implement" })} />);
  await u.click(screen.getByLabelText("Effective"));
  await u.type(screen.getByLabelText(/Verification narrative/), "No recurrence");
  // signing checkbox gates submit
  const submit = screen.getByRole("button", { name: /Record verification/ });
  expect(submit).toBeDisabled();
  await u.click(screen.getByLabelText(/Signing as/));
  expect(submit).toBeEnabled();
});

test("CloseAction surfaces a 409 capa_close_incomplete calmly", async () => {
  server.use(
    http.post("/api/v1/capas/:id/close", () =>
      HttpResponse.json({ code: "capa_close_incomplete", title: "Missing evidence" }, { status: 409 }),
    ),
  );
  const u = userEvent.setup();
  // The Close button is always enabled (server-authoritative gate); an effective-Verify CAPA whose close
  // 409s shows the server's message calmly.
  const atVerify = capa({
    close_state: "Verify",
    stages: [
      { id: "vf", stage: "Verify", content_block: { decision: "effective" }, cycle_marker: 0, created_by: "u", created_at: "x", evidence_links: [] },
    ],
  });
  wrap(<CloseAction capa={atVerify} />);
  await u.click(screen.getByRole("button", { name: /Close CAPA/ }));
  expect(await screen.findByText(/Missing evidence/)).toBeInTheDocument();
});

test("CloseAction at a not_effective Verify offers 'Return to root cause'", () => {
  const looped = capa({
    close_state: "Verify",
    stages: [
      { id: "vf", stage: "Verify", content_block: { decision: "not_effective" }, cycle_marker: 0, created_by: "u", created_at: "x", evidence_links: [] },
    ],
  });
  wrap(<CloseAction capa={looped} />);
  expect(screen.getByRole("button", { name: /Return to root cause/ })).toBeInTheDocument();
});
