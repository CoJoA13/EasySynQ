import { MantineProvider } from "@mantine/core";
import "@mantine/core/styles.css";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "../src/App";
import { ApplicationErrorBoundary } from "../src/app/errors/ApplicationErrorBoundary";
import { ApplicationErrorScreen } from "../src/app/errors/ApplicationErrorScreen";
import "../src/index.css";
import { AuthContext, type AuthState } from "../src/lib/auth";
import { theme } from "../src/theme/mantine";

const TEST_USER_ID = "bbbb1111-1111-1111-1111-111111111111";
const BROWSER_TEST_TOKEN = "browser-test-token";

declare global {
  interface Window {
    __EASYSYNQ_E2E_LOGIN_CALLS__: number;
  }
}

const tokenless = new URLSearchParams(window.location.search).get("e2e-auth") === "tokenless";
window.__EASYSYNQ_E2E_LOGIN_CALLS__ = 0;

const auth: AuthState = {
  status: { kind: "ready" },
  token: tokenless ? null : BROWSER_TEST_TOKEN,
  user: tokenless
    ? null
    : ({
        access_token: BROWSER_TEST_TOKEN,
        profile: { sub: TEST_USER_ID },
      } as AuthState["user"]),
  login: async () => {
    window.__EASYSYNQ_E2E_LOGIN_CALLS__ += 1;
  },
  retry: async () => undefined,
  logout: async () => undefined,
};

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
const root = document.getElementById("root");
if (!root) throw new Error("#root not found");

ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <MantineProvider theme={theme} defaultColorScheme="auto">
      <ApplicationErrorBoundary fallback={() => <ApplicationErrorScreen />}>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <AuthContext.Provider value={auth}>
              <App />
            </AuthContext.Provider>
          </BrowserRouter>
        </QueryClientProvider>
      </ApplicationErrorBoundary>
    </MantineProvider>
  </React.StrictMode>,
);
