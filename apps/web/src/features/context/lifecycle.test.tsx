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
  usePublishContextRegister,
  useReleaseContextRegister,
  useStartContextRegisterRevision,
} from "./mutations";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
    </QueryClientProvider>
  );
}

it("useStartContextRegisterRevision POSTs start-revision and lands UnderRevision", async () => {
  const { result } = renderHook(() => useStartContextRegisterRevision(), { wrapper });
  const updated = await result.current.mutateAsync();
  expect(updated.state).toBe("UnderRevision");
});

it("usePublishContextRegister POSTs publish (with the change reason) and lands InReview", async () => {
  const { result } = renderHook(() => usePublishContextRegister(), { wrapper });
  const updated = await result.current.mutateAsync({ change_reason: "Annual review" });
  expect(updated.state).toBe("InReview");
});

it("useReleaseContextRegister POSTs release and lands Effective", async () => {
  const { result } = renderHook(() => useReleaseContextRegister(), { wrapper });
  const updated = await result.current.mutateAsync();
  expect(updated.state).toBe("Effective");
});

it("a lifecycle mutation rejects with the server's ApiError on a 409 (e.g. SoD-2 self-release)", async () => {
  server.use(
    http.post("/api/v1/context/register/release", () =>
      HttpResponse.json(
        { code: "sod_violation", title: "You can't release a revision you authored." },
        { status: 409 },
      ),
    ),
  );
  const { result } = renderHook(() => useReleaseContextRegister(), { wrapper });
  await expect(result.current.mutateAsync()).rejects.toBeInstanceOf(ApiError);
});
