import { Button, Container, Loader, Stack, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { SetupWizard } from "./SetupWizard";
import { AppShell } from "./app/shell/AppShell";
import { AdminShell } from "./admin/AdminShell";
import { RolesAdmin } from "./admin/RolesAdmin";
import { UsersAdmin } from "./admin/UsersAdmin";
import { NewDocumentWizard } from "./features/authoring/NewDocumentWizard";
import { DocumentDetailPage } from "./features/document/DocumentDetailPage";
import { HomePage } from "./features/home/HomePage";
import { LibraryPage } from "./features/library/LibraryPage";
import { ReviewApprovePage } from "./features/review/ReviewApprovePage";
import { TasksInbox } from "./features/review/TasksInbox";
import { SearchResultsPage } from "./features/search/SearchResultsPage";
import { CompliancePage } from "./features/compliance/CompliancePage";
import { apiGet } from "./lib/api";
import { useAuth } from "./lib/auth";

export function App() {
  const { ready, token, login } = useAuth();

  // The public setup-state probe decides wizard-vs-shell (S8a). The latch (423) protects the API
  // regardless; this is just the SPA's routing signal.
  const setupState = useQuery({
    queryKey: ["setup-state"],
    queryFn: () => apiGet<{ setup_state: string }>("/api/v1/setup/state"),
  });

  const operational = setupState.data?.setup_state === "OPERATIONAL";

  // Tokens live in memory only (lib/auth), so every reload starts logged-out. When the install is
  // operational and we hold no token, bounce through Keycloak to re-authenticate (seamless while the
  // SSO session is live). A one-shot sessionStorage flag stops a failed sign-in from looping.
  useEffect(() => {
    if (!ready || setupState.isLoading) return;
    if (operational && !token) {
      if (!sessionStorage.getItem("es_auth_redirect")) {
        sessionStorage.setItem("es_auth_redirect", "1");
        login();
      }
    } else if (token) {
      sessionStorage.removeItem("es_auth_redirect");
    }
  }, [ready, operational, token, login, setupState.isLoading]);

  if (!ready || setupState.isLoading) {
    return (
      <Container size="sm" py="xl">
        <Loader />
      </Container>
    );
  }

  // Operational but token-less → we're redirecting to Keycloak. Show a calm interstitial (not the
  // shell, which would flash 401s) with a manual retry in case the auto-redirect was blocked.
  if (operational && !token) {
    return (
      <Container size="sm" py="xl">
        <Stack align="center" gap="md">
          <Loader />
          <Text c="dimmed">Signing in…</Text>
          <Button
            variant="subtle"
            onClick={() => {
              sessionStorage.removeItem("es_auth_redirect");
              login();
            }}
          >
            Sign in again
          </Button>
        </Stack>
      </Container>
    );
  }

  return (
    <Routes>
      <Route
        path="/setup"
        element={
          operational ? (
            <Navigate to="/" replace />
          ) : (
            <SetupWizard
              token={token}
              login={login}
              onFinalized={() => void setupState.refetch()}
            />
          )
        }
      />
      <Route
        path="/admin"
        element={operational ? <AdminShell /> : <Navigate to="/setup" replace />}
      >
        <Route index element={<Navigate to="users" replace />} />
        <Route path="users" element={<UsersAdmin token={token} />} />
        <Route path="roles" element={<RolesAdmin token={token} />} />
      </Route>
      <Route path="/" element={operational ? <AppShell /> : <Navigate to="/setup" replace />}>
        <Route index element={<HomePage />} />
        <Route path="library" element={<LibraryPage />} />
        <Route path="library/new" element={<NewDocumentWizard />} />
        <Route path="documents/:id" element={<DocumentDetailPage />} />
        <Route path="tasks" element={<TasksInbox />} />
        <Route path="tasks/:id" element={<ReviewApprovePage />} />
        <Route path="search" element={<SearchResultsPage />} />
        <Route path="compliance" element={<CompliancePage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
