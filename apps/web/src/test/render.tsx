import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderOptions } from "@testing-library/react";
import { useEffect, type ReactElement, type ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { AuthContext, type AuthState } from "../lib/auth";
import { MutationFeedbackProvider } from "../lib/mutationFeedback";
import { theme } from "../theme/mantine";

// Unit tests observe settled UI states. Mantine 7 transition frames can retain a timer
// across unmount when loading changes during flushSync; reduced motion avoids that path.
// Browser tests keep the application theme and its real transition behavior.
const testTheme = { ...theme, respectReducedMotion: true };

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
    useEffect(
      () => () => {
        queryClient.clear();
      },
      [queryClient],
    );

    return (
      <MantineProvider theme={testTheme}>
        <QueryClientProvider client={queryClient}>
          <AuthContext.Provider value={auth}>
            <MutationFeedbackProvider>
              <MemoryRouter initialEntries={[route]} useTransitions={false}>
                {children}
              </MemoryRouter>
            </MutationFeedbackProvider>
          </AuthContext.Provider>
        </QueryClientProvider>
      </MantineProvider>
    );
  }
  return render(ui, { wrapper: Wrapper, ...rest });
}
