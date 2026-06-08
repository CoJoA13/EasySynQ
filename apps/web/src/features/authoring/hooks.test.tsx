import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { expect, test } from "vitest";
import { AuthContext } from "../../lib/auth";
import { server } from "../../test/msw/server";
import { TEST_AUTH } from "../../test/render";
import { theme } from "../../theme/mantine";
import { createdDocFixture } from "../../test/msw/handlers";
import { useReleaseDocument } from "./hooks";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <MantineProvider theme={theme}>
      <QueryClientProvider client={client}>
        <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
      </QueryClientProvider>
    </MantineProvider>
  );
}

test("useReleaseDocument posts release and returns the now-Effective document", async () => {
  server.use(
    http.post("/api/v1/documents/:id/release", ({ params }) =>
      HttpResponse.json({ ...createdDocFixture, id: String(params.id), current_state: "Effective" }),
    ),
  );
  const { result } = renderHook(() => useReleaseDocument(), { wrapper });
  const doc = await result.current.mutateAsync("11111111-1111-1111-1111-111111111111");
  expect(doc.current_state).toBe("Effective");
});
