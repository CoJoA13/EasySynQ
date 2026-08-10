import { QueryClient, useQuery } from "@tanstack/react-query";
import { cleanup, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { renderWithProviders } from "./render";

function PendingQuery({ onAbort }: { onAbort: () => void }) {
  useQuery({
    queryKey: ["pending-test-query"],
    queryFn: ({ signal }) =>
      new Promise<never>(() => {
        signal.addEventListener("abort", onAbort, { once: true });
      }),
    retry: false,
  });
  return null;
}

test("renderWithProviders clears a pending query client when testing-library unmounts", async () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onAbort = vi.fn();

  renderWithProviders(<PendingQuery onAbort={onAbort} />, { queryClient });

  await waitFor(() => expect(queryClient.getQueryCache().getAll()).toHaveLength(1));
  cleanup();

  expect(onAbort).toHaveBeenCalledTimes(1);
  expect(queryClient.getQueryCache().getAll()).toHaveLength(0);
});
