// apps/web/src/features/capa/RaiseCapaModal.test.tsx
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { expect, test, vi } from "vitest";
import { AuthContext } from "../../lib/auth";
import type { CapaRaiseBody } from "../../lib/types";
import { server } from "../../test/msw/server";
import { theme } from "../../theme/mantine";
import { TEST_AUTH } from "../../test/render";
import { RaiseCapaModal } from "./RaiseCapaModal";

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

test("creates a CAPA and calls onCreated with the new id", async () => {
  const u = userEvent.setup();
  const onCreated = vi.fn();
  wrap(<RaiseCapaModal opened onClose={vi.fn()} onCreated={onCreated} />);
  await u.type(screen.getByLabelText(/^Title/), "Torque wrench miscalibration");
  await u.click(await screen.findByLabelText(/^Severity/));
  await u.click(await screen.findByRole("option", { name: "Minor" }));
  await u.click(screen.getByRole("button", { name: /Raise CAPA/ }));
  // onCreated must receive the CREATED CAPA's id (the MSW POST /capas handler returns this id) — the
  // component's whole contract is opening the new CAPA's drawer.
  await vi.waitFor(() =>
    expect(onCreated).toHaveBeenCalledWith("ca-new-0000-0000-0000-000000000000"),
  );
});

test("the Raise button is disabled until title AND severity are filled", async () => {
  const u = userEvent.setup();
  wrap(<RaiseCapaModal opened onClose={vi.fn()} onCreated={vi.fn()} />);
  expect(screen.getByRole("button", { name: /Raise CAPA/ })).toBeDisabled();
  await u.type(screen.getByLabelText(/^Title/), "x");
  expect(screen.getByRole("button", { name: /Raise CAPA/ })).toBeDisabled(); // severity still missing
  await u.click(await screen.findByLabelText(/^Severity/));
  await u.click(await screen.findByRole("option", { name: "Minor" }));
  expect(screen.getByRole("button", { name: /Raise CAPA/ })).toBeEnabled();
});

test("offers exactly the three allowed sources (review_output omitted)", async () => {
  const u = userEvent.setup();
  wrap(<RaiseCapaModal opened onClose={vi.fn()} onCreated={vi.fn()} />);
  await u.click(await screen.findByLabelText("Source"));
  expect(screen.queryByRole("option", { name: /Mgmt review/ })).toBeNull();
  expect(screen.getAllByRole("option")).toHaveLength(3);
});

// A bound Process-Owner picks their owned process so the server's PROCESS-scoped capa.create enforce
// passes — the create body must carry that process_id. (Default MSW GET /processes → 2 processes.)
test("submits the chosen process_id with the raise", async () => {
  let captured: CapaRaiseBody | undefined;
  server.use(
    http.post("/api/v1/capas", async ({ request }) => {
      captured = (await request.json()) as CapaRaiseBody;
      return HttpResponse.json({ id: "ca-new-0000-0000-0000-000000000000" });
    }),
  );
  const u = userEvent.setup();
  wrap(<RaiseCapaModal opened onClose={vi.fn()} onCreated={vi.fn()} />);
  await u.type(screen.getByLabelText(/^Title/), "Calibration drift");
  await u.click(await screen.findByLabelText(/^Severity/));
  await u.click(await screen.findByRole("option", { name: "Minor" }));
  await u.click(await screen.findByLabelText("Process (optional)"));
  await u.click(await screen.findByRole("option", { name: "Purchasing" }));
  await u.click(screen.getByRole("button", { name: /Raise CAPA/ }));
  await vi.waitFor(() => expect(captured?.process_id).toBe("pr000001-0001-0001-0001-000000000001"));
});

// Degrade gracefully: a caller who can't read any process (GET /processes empty) gets no picker, and
// the raise stays a process-less SYSTEM/ad-hoc CAPA — byte-identical to the pre-picker behaviour.
test("omits the process picker when no processes are readable", async () => {
  server.use(http.get("/api/v1/processes", () => HttpResponse.json([])));
  wrap(<RaiseCapaModal opened onClose={vi.fn()} onCreated={vi.fn()} />);
  expect(await screen.findByLabelText(/^Title/)).toBeInTheDocument();
  expect(screen.queryByLabelText("Process (optional)")).toBeNull();
});

// A PROCESS-only caller (no SYSTEM capa.create) MUST pick a process — a process-less raise would 403
// at the server's SYSTEM-scope enforce — so requireProcess makes the picker required + gates submit.
test("requireProcess makes the picker required and gates Raise until a process is picked", async () => {
  const u = userEvent.setup();
  wrap(<RaiseCapaModal opened requireProcess onClose={vi.fn()} onCreated={vi.fn()} />);
  await u.type(screen.getByLabelText(/^Title/), "Press tool wear");
  await u.click(await screen.findByLabelText(/^Severity/));
  await u.click(await screen.findByRole("option", { name: "Minor" }));
  // The label is the required "Process" (not "Process (optional)"), and the button stays disabled.
  expect(screen.queryByLabelText("Process (optional)")).toBeNull();
  expect(screen.getByRole("button", { name: /Raise CAPA/ })).toBeDisabled();
  // The required label renders with Mantine's asterisk, so query by prefix (not the optional label).
  await u.click(await screen.findByLabelText(/^Process/));
  await u.click(await screen.findByRole("option", { name: "Purchasing" }));
  expect(screen.getByRole("button", { name: /Raise CAPA/ })).toBeEnabled();
});
