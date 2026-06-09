// apps/web/src/features/capa/RaiseCapaModal.test.tsx
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { expect, test, vi } from "vitest";
import { AuthContext } from "../../lib/auth";
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
