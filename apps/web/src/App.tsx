import { useQuery } from "@tanstack/react-query";
import { Suspense, lazy, useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import { AppShell } from "./app/shell/AppShell";
import { AuthStartupScreen } from "./app/startup/AuthStartupScreen";
import { SetupStartupScreen } from "./app/startup/SetupStartupScreen";
import { fetchSetupState } from "./app/startup/setupState";
import { AdminShell } from "./admin/AdminShell";
import { CapaLayout } from "./features/capa/CapaLayout";
import { AuditsLayout } from "./features/audits/AuditsLayout";
import { DriftLayout } from "./features/drift/DriftLayout";
import { useAuth } from "./lib/auth";
import { LoadingState } from "./lib/states";
import { MutationFeedbackProvider } from "./lib/mutationFeedback";
import { RouteChromeProvider, useRouteChrome } from "./lib/routeChrome";

// U15: route-level code splitting. Every ROUTE ELEMENT is a lazy chunk so the initial
// bundle no longer carries the admin console, the setup wizard, the ingestion console and
// every register a given operator may never open. Layout/chrome (AppShell, AdminShell and
// the three sub-layouts) stay eager — they render on every route in their subtree, so
// splitting them would only add a waterfall.
const SetupWizard = lazy(() => import("./SetupWizard").then((m) => ({ default: m.SetupWizard })));
const ConfigAdmin = lazy(() =>
  import("./admin/ConfigAdmin").then((m) => ({ default: m.ConfigAdmin })),
);
const ProcessesAdmin = lazy(() =>
  import("./admin/ProcessesAdmin").then((m) => ({ default: m.ProcessesAdmin })),
);
const RolesAdmin = lazy(() =>
  import("./admin/RolesAdmin").then((m) => ({ default: m.RolesAdmin })),
);
const UsersAdmin = lazy(() =>
  import("./admin/UsersAdmin").then((m) => ({ default: m.UsersAdmin })),
);
const NewDocumentWizard = lazy(() =>
  import("./features/authoring/NewDocumentWizard").then((m) => ({ default: m.NewDocumentWizard })),
);
const DocumentDetailPage = lazy(() =>
  import("./features/document/DocumentDetailPage").then((m) => ({ default: m.DocumentDetailPage })),
);
const HomePage = lazy(() =>
  import("./features/home/HomePage").then((m) => ({ default: m.HomePage })),
);
const LibraryPage = lazy(() =>
  import("./features/library/LibraryPage").then((m) => ({ default: m.LibraryPage })),
);
const ReviewApprovePage = lazy(() =>
  import("./features/review/ReviewApprovePage").then((m) => ({ default: m.ReviewApprovePage })),
);
const TasksInbox = lazy(() =>
  import("./features/review/TasksInbox").then((m) => ({ default: m.TasksInbox })),
);
const SearchResultsPage = lazy(() =>
  import("./features/search/SearchResultsPage").then((m) => ({ default: m.SearchResultsPage })),
);
const CompliancePage = lazy(() =>
  import("./features/compliance/CompliancePage").then((m) => ({ default: m.CompliancePage })),
);
const CapaBoardPage = lazy(() =>
  import("./features/capa/CapaBoardPage").then((m) => ({ default: m.CapaBoardPage })),
);
const ComplaintsPage = lazy(() =>
  import("./features/capa/ComplaintsPage").then((m) => ({ default: m.ComplaintsPage })),
);
const NcrsPage = lazy(() =>
  import("./features/capa/NcrsPage").then((m) => ({ default: m.NcrsPage })),
);
const AuditsListPage = lazy(() =>
  import("./features/audits/AuditsListPage").then((m) => ({ default: m.AuditsListPage })),
);
const AuditDetailPage = lazy(() =>
  import("./features/audits/AuditDetailPage").then((m) => ({ default: m.AuditDetailPage })),
);
const ProgrammePage = lazy(() =>
  import("./features/audits/ProgrammePage").then((m) => ({ default: m.ProgrammePage })),
);
const IngestionRunsPage = lazy(() =>
  import("./features/ingestion/IngestionRunsPage").then((m) => ({ default: m.IngestionRunsPage })),
);
const IngestionRunPage = lazy(() =>
  import("./features/ingestion/IngestionRunPage").then((m) => ({ default: m.IngestionRunPage })),
);
const DriftStatusPage = lazy(() =>
  import("./features/drift/DriftStatusPage").then((m) => ({ default: m.DriftStatusPage })),
);
const SupersededCopiesPage = lazy(() =>
  import("./features/drift/SupersededCopiesPage").then((m) => ({
    default: m.SupersededCopiesPage,
  })),
);
const ObjectivesRegisterPage = lazy(() =>
  import("./features/objectives/ObjectivesRegisterPage").then((m) => ({
    default: m.ObjectivesRegisterPage,
  })),
);
const ReportsRegisterPage = lazy(() =>
  import("./features/reports/ReportsRegisterPage").then((m) => ({
    default: m.ReportsRegisterPage,
  })),
);
const ObjectiveDetailPage = lazy(() =>
  import("./features/objectives/ObjectiveDetailPage").then((m) => ({
    default: m.ObjectiveDetailPage,
  })),
);
const ManagementReviewsRegisterPage = lazy(() =>
  import("./features/management-review/ManagementReviewsRegisterPage").then((m) => ({
    default: m.ManagementReviewsRegisterPage,
  })),
);
const ManagementReviewDetailPage = lazy(() =>
  import("./features/management-review/ManagementReviewDetailPage").then((m) => ({
    default: m.ManagementReviewDetailPage,
  })),
);
const DcrsRegisterPage = lazy(() =>
  import("./features/dcr/DcrsRegisterPage").then((m) => ({ default: m.DcrsRegisterPage })),
);
const DcrDiffPage = lazy(() =>
  import("./features/dcr/DcrDiffPage").then((m) => ({ default: m.DcrDiffPage })),
);
const ImprovementRegisterPage = lazy(() =>
  import("./features/improvement/ImprovementRegisterPage").then((m) => ({
    default: m.ImprovementRegisterPage,
  })),
);
const RisksRegisterPage = lazy(() =>
  import("./features/risk/RisksRegisterPage").then((m) => ({ default: m.RisksRegisterPage })),
);
const ContextRegisterPage = lazy(() =>
  import("./features/context/ContextRegisterPage").then((m) => ({
    default: m.ContextRegisterPage,
  })),
);
const InterestedPartiesRegisterPage = lazy(() =>
  import("./features/interested-parties/InterestedPartiesRegisterPage").then((m) => ({
    default: m.InterestedPartiesRegisterPage,
  })),
);
const NotificationsPage = lazy(() =>
  import("./features/notifications/NotificationsPage").then((m) => ({
    default: m.NotificationsPage,
  })),
);
const NotificationSettingsPage = lazy(() =>
  import("./features/notifications/NotificationSettingsPage").then((m) => ({
    default: m.NotificationSettingsPage,
  })),
);
const RecordsPage = lazy(() =>
  import("./features/records/RecordsPage").then((m) => ({ default: m.RecordsPage })),
);
const RecordDetailPage = lazy(() =>
  import("./features/records/RecordDetailPage").then((m) => ({ default: m.RecordDetailPage })),
);

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

  const verifyBootstrapAcknowledgment = async (): Promise<void> => {
    await setupState.refetch({ cancelRefetch: false });
  };

  const setupValue = setupState.data?.setup_state;
  const operational = setupValue === "OPERATIONAL";
  const preOperational = setupValue === "UNINITIALIZED" || setupValue === "IN_SETUP";
  const authenticationRequired = setupValue === "IN_SETUP" || operational;

  // Tokens live in memory only (lib/auth), so every reload starts logged-out. When the install is
  // authentication is required and we hold no token, bounce through Keycloak to authenticate
  // (seamless while the SSO session is live). A one-shot sessionStorage flag stops a failed sign-in
  // from looping. UNINITIALIZED remains public so the first native administrator can be created.
  useEffect(() => {
    if (status.kind !== "ready" || setupState.status !== "success") return;
    if (authenticationRequired && !token) {
      if (!sessionStorage.getItem("es_auth_redirect")) {
        sessionStorage.setItem("es_auth_redirect", "1");
        void login();
      }
    } else if (token) {
      sessionStorage.removeItem("es_auth_redirect");
    }
  }, [status.kind, authenticationRequired, token, login, setupState.status]);

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
    <MutationFeedbackProvider>
      {/* One Suspense boundary around the whole route table. This only shows its fallback
          because the routers opt OUT of react-router's default startTransition wrapping (see
          main.tsx) — inside a transition React keeps the previous page on screen instead, which
          would make an uncached route look like a frozen app. */}
      <Suspense fallback={<LoadingState label="Loading page" />}>
        <Routes>
          <Route
            path="/setup"
            element={
              operational ? (
                <Navigate to="/" replace />
              ) : setupValue === "UNINITIALIZED" || setupValue === "IN_SETUP" ? (
                <SetupWizard
                  setupState={setupValue}
                  token={token}
                  login={login}
                  onBootstrapAcknowledged={verifyBootstrapAcknowledgment}
                  onFinalized={verifyFinalization}
                />
              ) : (
                <Navigate to="/" replace />
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
            <Route path="records" element={<RecordsPage />} />
            <Route path="records/:recordId" element={<RecordDetailPage />} />
          </Route>
          <Route
            path="*"
            element={operational ? <AppShell notFound /> : <Navigate to="/setup" replace />}
          />
        </Routes>
      </Suspense>
    </MutationFeedbackProvider>
  );
}
