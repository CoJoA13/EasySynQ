// apps/web/src/features/capa/EvidenceLinker.test.tsx
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { expect, test } from "vitest";
import { AuthContext } from "../../lib/auth";
import { theme } from "../../theme/mantine";
import { TEST_AUTH } from "../../test/render";
import { EvidenceLinker } from "./EvidenceLinker";

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

test("links a selected record as evidence for the stage", async () => {
  const u = userEvent.setup();
  wrap(
    <EvidenceLinker capaId="ca000008-0008-0008-0008-000000000008" stageId="cr000002-0002-0002-0002-000000000002" />,
  );
  await u.click(await screen.findByLabelText("Record"));
  await u.click(await screen.findByRole("option", { name: /REC-000041/ }));
  const link = screen.getByRole("button", { name: /Link evidence/ });
  await u.click(link);
  await waitFor(() => expect(screen.getByText(/Linked/)).toBeInTheDocument());
});

test("the Link button is disabled until a record is picked", async () => {
  const u = userEvent.setup();
  wrap(
    <EvidenceLinker capaId="ca000008-0008-0008-0008-000000000008" stageId="cr000002-0002-0002-0002-000000000002" />,
  );
  expect(screen.getByRole("button", { name: /Link evidence/ })).toBeDisabled();
  await u.click(await screen.findByLabelText("Record"));
  await u.click(await screen.findByRole("option", { name: /REC-000041/ }));
  expect(screen.getByRole("button", { name: /Link evidence/ })).toBeEnabled();
});
