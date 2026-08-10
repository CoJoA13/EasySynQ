import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import { SetupWizard } from "./SetupWizard";
import { AppShell } from "./app/shell/AppShell";
import { AuthStartupScreen } from "./app/startup/AuthStartupScreen";
import { SetupStartupScreen } from "./app/startup/SetupStartupScreen";
import { fetchSetupState } from "./app/startup/setupState";
import { AdminShell } from "./admin/AdminShell";
import { ConfigAdmin } from "./admin/ConfigAdmin";
import { ProcessesAdmin } from "./admin/ProcessesAdmin";
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
import { CapaBoardPage } from "./features/capa/CapaBoardPage";
import { CapaLayout } from "./features/capa/CapaLayout";
import { ComplaintsPage } from "./features/capa/ComplaintsPage";
import { NcrsPage } from "./features/capa/NcrsPage";
import { AuditsLayout } from "./features/audits/AuditsLayout";
import { AuditsListPage } from "./features/audits/AuditsListPage";
import { AuditDetailPage } from "./features/audits/AuditDetailPage";
import { ProgrammePage } from "./features/audits/ProgrammePage";
import { IngestionRunsPage } from "./features/ingestion/IngestionRunsPage";
import { IngestionRunPage } from "./features/ingestion/IngestionRunPage";
import { DriftLayout } from "./features/drift/DriftLayout";
import { DriftStatusPage } from "./features/drift/DriftStatusPage";
import { SupersededCopiesPage } from "./features/drift/SupersededCopiesPage";
import { ObjectivesRegisterPage } from "./features/objectives/ObjectivesRegisterPage";
import { ReportsRegisterPage } from "./features/reports/ReportsRegisterPage";
import { ObjectiveDetailPage } from "./features/objectives/ObjectiveDetailPage";
import { ManagementReviewsRegisterPage } from "./features/management-review/ManagementReviewsRegisterPage";
import { ManagementReviewDetailPage } from "./features/management-review/ManagementReviewDetailPage";
import { DcrsRegisterPage } from "./features/dcr/DcrsRegisterPage";
import { DcrDiffPage } from "./features/dcr/DcrDiffPage";
import { ImprovementRegisterPage } from "./features/improvement/ImprovementRegisterPage";
import { RisksRegisterPage } from "./features/risk/RisksRegisterPage";
import { ContextRegisterPage } from "./features/context/ContextRegisterPage";
import { InterestedPartiesRegisterPage } from "./features/interested-parties/InterestedPartiesRegisterPage";
import { NotificationsPage } from "./features/notifications/NotificationsPage";
import { NotificationSettingsPage } from "./features/notifications/NotificationSettingsPage";
import { useAuth } from "./lib/auth";
import { RouteChromeProvider, useRouteChrome } from "./lib/routeChrome";

type FinalizationVerification = "idle" | "checking" | "error";

export function LegacyImportRedirect() {
  const { runId } = useParams();
  const { search } = useLocation();
  const target = runId ? `/imports/${encodeURIComponent(runId)}${search}` : `/imports${search}`;
  return <Navigate to={target} replace />;
}

export function App() {
  return (
    <RouteChromeProvider>
      <AppContent />
    </RouteChromeProvider>
  );
}

function AppContent() {
  useRouteChrome();
  const { status, token, login, retry } = useAuth();

  const setupState = useQuery({
    queryKey: ["setup-state"],
    queryFn: ({ signal }) => fetchSetupState(signal),
    retry: false,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchInterval: false,
  });
  const [finalizationVerification, setFinalizationVerification] =
    useState<FinalizationVerification>("idle");

  const verifyFinalization = async (): Promise<void> => {
    setFinalizationVerification("checking");
    try {
      const result = await setupState.refetch({ cancelRefetch: false });
      if (result.status === "success" && result.data.setup_state === "OPERATIONAL") {
        setFinalizationVerification("idle");
        return;
      }
    } catch {
      setFinalizationVerification("error");
      return;
    }
    setFinalizationVerification("error");
  };

  const setupValue = setupState.data?.setup_state;
  const operational = setupValue === "OPERATIONAL";
  const preOperational = setupValue === "UNINITIALIZED" || setupValue === "IN_SETUP";

  // Tokens live in memory only (lib/auth), so every reload starts logged-out. When the install is
  // operational and we hold no token, bounce through Keycloak to re-authenticate (seamless while the
  // SSO session is live). A one-shot sessionStorage flag stops a failed sign-in from looping.
  useEffect(() => {
    if (status.kind !== "ready" || setupState.status !== "success") return;
    if (operational && !token) {
      if (!sessionStorage.getItem("es_auth_redirect")) {
        sessionStorage.setItem("es_auth_redirect", "1");
        void login();
      }
    } else if (token) {
      sessionStorage.removeItem("es_auth_redirect");
    }
  }, [status.kind, operational, token, login, setupState.status]);

  if (status.kind !== "ready") {
    return (
      <AuthStartupScreen
        status={status}
        onRetry={async () => {
          sessionStorage.removeItem("es_auth_redirect");
          await retry();
        }}
        onReload={() => window.location.reload()}
      />
    );
  }

  if (finalizationVerification === "checking") {
    return (
      <SetupStartupScreen
        status={{ kind: "loading", phase: "post-finalization" }}
        onRetry={verifyFinalization}
        onReload={() => window.location.reload()}
      />
    );
  }

  if (finalizationVerification === "error") {
    return (
      <SetupStartupScreen
        status={{ kind: "error", phase: "post-finalization" }}
        onRetry={verifyFinalization}
        onReload={() => window.location.reload()}
      />
    );
  }

  if (setupState.isPending) {
    return (
      <SetupStartupScreen
        status={{ kind: "loading", phase: "initial" }}
        onRetry={async () => {
          await setupState.refetch({ cancelRefetch: false });
        }}
        onReload={() => window.location.reload()}
      />
    );
  }

  if (setupState.isError || (!operational && !preOperational)) {
    return (
      <SetupStartupScreen
        status={{ kind: "error", phase: "initial" }}
        onRetry={async () => {
          await setupState.refetch({ cancelRefetch: false });
        }}
        onReload={() => window.location.reload()}
      />
    );
  }

  // Operational but token-less → keep the shell hidden. A fresh attempt shows named redirect loading;
  // a pre-existing latch has no active provider watchdog, so it must show actionable recovery instead.
  if (operational && !token) {
    const redirectAlreadyAttempted = sessionStorage.getItem("es_auth_redirect") !== null;
    return (
      <AuthStartupScreen
        status={
          redirectAlreadyAttempted
            ? { kind: "error", failure: { kind: "redirect", recovery: "redirect" } }
            : { kind: "loading", operation: "redirect" }
        }
        onRetry={async () => {
          sessionStorage.removeItem("es_auth_redirect");
          await login();
        }}
        onReload={() => window.location.reload()}
      />
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
            <SetupWizard token={token} login={login} onFinalized={verifyFinalization} />
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
        <Route path="processes" element={<ProcessesAdmin token={token} />} />
        <Route path="config" element={<ConfigAdmin />} />
      </Route>
      <Route path="/" element={operational ? <AppShell /> : <Navigate to="/setup" replace />}>
        <Route index element={<HomePage />} />
        <Route path="library" element={<LibraryPage />} />
        <Route path="library/new" element={<NewDocumentWizard />} />
        <Route path="documents/:id" element={<DocumentDetailPage />} />
        <Route path="tasks" element={<TasksInbox />} />
        <Route path="tasks/:id" element={<ReviewApprovePage />} />
        <Route path="notifications" element={<NotificationsPage />} />
        <Route path="settings/notifications" element={<NotificationSettingsPage />} />
        <Route path="search" element={<SearchResultsPage />} />
        <Route path="compliance" element={<CompliancePage />} />
        <Route path="reports/document-control" element={<ReportsRegisterPage />} />
        <Route path="capa" element={<CapaLayout />}>
          <Route index element={<CapaBoardPage />} />
          <Route path="complaints" element={<ComplaintsPage />} />
          <Route path="ncrs" element={<NcrsPage />} />
        </Route>
        <Route path="audits" element={<AuditsLayout />}>
          <Route index element={<AuditsListPage />} />
          <Route path="programme" element={<ProgrammePage />} />
        </Route>
        <Route path="audits/:id" element={<AuditDetailPage />} />
        <Route path="imports" element={<IngestionRunsPage />} />
        <Route path="imports/:runId" element={<IngestionRunPage />} />
        <Route path="ingestion" element={<LegacyImportRedirect />} />
        <Route path="ingestion/:runId" element={<LegacyImportRedirect />} />
        <Route path="drift" element={<DriftLayout />}>
          <Route index element={<DriftStatusPage />} />
          <Route path="superseded-copies" element={<SupersededCopiesPage />} />
        </Route>
        <Route path="objectives" element={<ObjectivesRegisterPage />} />
        <Route path="objectives/:id" element={<ObjectiveDetailPage />} />
        <Route path="management-reviews" element={<ManagementReviewsRegisterPage />} />
        <Route path="management-reviews/:id" element={<ManagementReviewDetailPage />} />
        <Route path="dcrs" element={<DcrsRegisterPage />} />
        <Route path="dcrs/:id/diff" element={<DcrDiffPage />} />
        <Route path="improvement" element={<ImprovementRegisterPage />} />
        <Route path="risks" element={<RisksRegisterPage />} />
        <Route path="context" element={<ContextRegisterPage />} />
        <Route path="interested-parties" element={<InterestedPartiesRegisterPage />} />
      </Route>
      <Route
        path="*"
        element={operational ? <AppShell notFound /> : <Navigate to="/setup" replace />}
      />
    </Routes>
  );
}
