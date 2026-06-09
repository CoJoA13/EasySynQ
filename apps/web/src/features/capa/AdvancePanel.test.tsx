// apps/web/src/features/capa/AdvancePanel.test.tsx
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { expect, test } from "vitest";
import { AuthContext } from "../../lib/auth";
import type { Capa } from "../../lib/types";
import { theme } from "../../theme/mantine";
import { server } from "../../test/msw/server";
import { TEST_AUTH } from "../../test/render";
import { AdvancePanel } from "./AdvancePanel";
import { capaApprovalFixture } from "../../test/msw/handlers";

const capa = (over: Partial<Capa> = {}): Capa => ({
  id: "ca000001-0001-0001-0001-000000000001",
  identifier: "REC-000031",
  title: "T",
  source: "audit",
  severity: "Major",
  process_id: "pr000001-0001-0001-0001-000000000001",
  close_state: "Raised",
  cycle_marker: 0,
  origin_finding_id: null,
  raised_by: null,
  created_at: null,
  stages: [],
  ...over,
});

function grant(...keys: string[]) {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "PROCESS", selector: null },
        permissions: keys.map((key) => ({ key, effect: "ALLOW", source: null })),
      }),
    ),
  );
}

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

test("shows the containment form at Raised when the caller holds capa.update", async () => {
  grant("capa.update");
  wrap(<AdvancePanel capa={capa()} />);
  expect(await screen.findByRole("button", { name: /Record correction/ })).toBeInTheDocument();
});

test("shows a read-only line (no form) when the caller lacks the stage key", async () => {
  grant(); // no keys
  wrap(<AdvancePanel capa={capa()} />);
  expect(await screen.findByText(/don't hold the permission/i)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Record correction/ })).toBeNull();
});

test("at RootCause with a pending approval, shows 'awaiting approval' not the propose form", async () => {
  grant("capa.plan_action");
  server.use(http.get("/api/v1/capas/:id/approval", () => HttpResponse.json(capaApprovalFixture)));
  wrap(<AdvancePanel capa={capa({ close_state: "RootCause" })} />);
  expect(await screen.findByText(/awaiting approval/i)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Propose action plan/ })).toBeNull();
});

test("at RootCause with no approval, shows the propose form", async () => {
  grant("capa.plan_action");
  server.use(http.get("/api/v1/capas/:id/approval", () => HttpResponse.json(null)));
  wrap(<AdvancePanel capa={capa({ close_state: "RootCause" })} />);
  expect(await screen.findByRole("button", { name: /Propose action plan/ })).toBeInTheDocument();
});

test("renders nothing for a terminal CAPA", () => {
  grant("capa.close");
  const { container } = wrap(<AdvancePanel capa={capa({ close_state: "Closed" })} />);
  expect(container.querySelector("button")).toBeNull();
});
