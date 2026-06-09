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
  await vi.waitFor(() => expect(onCreated).toHaveBeenCalled());
});

test("does not offer review_output as a source", async () => {
  const u = userEvent.setup();
  wrap(<RaiseCapaModal opened onClose={vi.fn()} onCreated={vi.fn()} />);
  await u.click(await screen.findByLabelText("Source"));
  expect(screen.queryByRole("option", { name: /Mgmt review/ })).toBeNull();
});
