import { Container, Loader } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { Navigate, Route, Routes } from "react-router-dom";
import { SetupWizard } from "./SetupWizard";
import { AppShell } from "./app/shell/AppShell";
import { AdminShell } from "./admin/AdminShell";
import { RolesAdmin } from "./admin/RolesAdmin";
import { UsersAdmin } from "./admin/UsersAdmin";
import { NewDocumentWizard } from "./features/authoring/NewDocumentWizard";
import { HomePage } from "./features/home/HomePage";
import { LibraryPage } from "./features/library/LibraryPage";
import { apiGet } from "./lib/api";
import { useAuth } from "./lib/auth";

function Reserved({ what }: { what: string }) {
  return <div>{what} — coming in a later slice.</div>;
}

export function App() {
  const { ready, token, login } = useAuth();

  // The public setup-state probe decides wizard-vs-shell (S8a). The latch (423) protects the API
  // regardless; this is just the SPA's routing signal.
  const setupState = useQuery({
    queryKey: ["setup-state"],
    queryFn: () => apiGet<{ setup_state: string }>("/api/v1/setup/state"),
  });

  if (!ready || setupState.isLoading) {
    return (
      <Container size="sm" py="xl">
        <Loader />
      </Container>
    );
  }

  const operational = setupState.data?.setup_state === "OPERATIONAL";

  return (
    <Routes>
      <Route
        path="/setup"
        element={
          operational ? (
            <Navigate to="/" replace />
          ) : (
            <SetupWizard token={token} login={login} onFinalized={() => void setupState.refetch()} />
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
        <Route path="documents/:id" element={<Reserved what="Document detail" />} />
        <Route path="tasks/:id" element={<Reserved what="Review & approve" />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
