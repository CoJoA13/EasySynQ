import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderOptions } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { AuthContext, type AuthState } from "../lib/auth";
import { theme } from "../theme/mantine";

export const TEST_AUTH: AuthState = {
  status: { kind: "ready" },
  token: "test-token",
  user: { profile: { sub: "bbbb1111-1111-1111-1111-111111111111" } } as AuthState["user"],
  login: async () => undefined,
  retry: async () => undefined,
  logout: async () => undefined,
};

export function renderWithProviders(
  ui: ReactElement,
  opts: { route?: string; auth?: AuthState; queryClient?: QueryClient } & Omit<
    RenderOptions,
    "wrapper"
  > = {},
) {
  const {
    route = "/",
    auth = TEST_AUTH,
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } }),
    ...rest
  } = opts;
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <MantineProvider theme={theme}>
        <QueryClientProvider client={queryClient}>
          <AuthContext.Provider value={auth}>
            <MemoryRouter initialEntries={[route]}>{children}</MemoryRouter>
          </AuthContext.Provider>
        </QueryClientProvider>
      </MantineProvider>
    );
  }
  return render(ui, { wrapper: Wrapper, ...rest });
}
