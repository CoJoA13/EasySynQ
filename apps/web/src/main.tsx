import { MantineProvider } from "@mantine/core";
import "@mantine/core/styles.css";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { ApplicationErrorBoundary } from "./app/errors/ApplicationErrorBoundary";
import { ApplicationErrorScreen } from "./app/errors/ApplicationErrorScreen";
import "./index.css";
import { AuthProvider } from "./lib/auth";
import { theme } from "./theme/mantine";

const queryClient = new QueryClient();
const root = document.getElementById("root");
if (!root) throw new Error("#root not found");

ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <MantineProvider theme={theme} defaultColorScheme="auto">
      <ApplicationErrorBoundary fallback={() => <ApplicationErrorScreen />}>
        <QueryClientProvider client={queryClient}>
          {/* U15: react-router wraps navigation in startTransition by default, and React will
              not replace already-revealed content with a Suspense fallback during a transition —
              so a click to an UNCACHED route chunk would leave the previous page frozen with no
              indication. Opting out lets the route-level Suspense boundary in App show its
              LoadingState; a cached chunk resolves synchronously, so there is no flash. */}
          <BrowserRouter useTransitions={false}>
            <AuthProvider>
              <App />
            </AuthProvider>
          </BrowserRouter>
        </QueryClientProvider>
      </ApplicationErrorBoundary>
    </MantineProvider>
  </React.StrictMode>,
);
