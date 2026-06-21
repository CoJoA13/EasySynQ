import { expect, it } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { ApiError } from "../../lib/api";
import { AuthContext } from "../../lib/auth";
import { TEST_AUTH } from "../../test/render";
import { server } from "../../test/msw/server";
import {
  usePublishRiskRegister,
  useReleaseRiskRegister,
  useStartRiskRegisterRevision,
} from "./mutations";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
    </QueryClientProvider>
  );
}

it("useStartRiskRegisterRevision POSTs start-revision and lands UnderRevision", async () => {
  const { result } = renderHook(() => useStartRiskRegisterRevision(), { wrapper });
  const updated = await result.current.mutateAsync();
  expect(updated.state).toBe("UnderRevision");
});

it("usePublishRiskRegister POSTs publish (with the change reason) and lands InReview", async () => {
  const { result } = renderHook(() => usePublishRiskRegister(), { wrapper });
  const updated = await result.current.mutateAsync({ change_reason: "Q3 reassessment" });
  expect(updated.state).toBe("InReview");
});

it("useReleaseRiskRegister POSTs release and lands Effective", async () => {
  const { result } = renderHook(() => useReleaseRiskRegister(), { wrapper });
  const updated = await result.current.mutateAsync();
  expect(updated.state).toBe("Effective");
});

it("a lifecycle mutation rejects with the server's ApiError on a 409 (e.g. SoD-2 self-release)", async () => {
  server.use(
    http.post("/api/v1/risks/register/release", () =>
      HttpResponse.json(
        { code: "sod_violation", title: "You can't release a revision you authored." },
        { status: 409 },
      ),
    ),
  );
  const { result } = renderHook(() => useReleaseRiskRegister(), { wrapper });
  await expect(result.current.mutateAsync()).rejects.toBeInstanceOf(ApiError);
});
