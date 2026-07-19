import {
  Alert,
  Button,
  Checkbox,
  Code,
  Container,
  Group,
  List,
  PasswordInput,
  SegmentedControl,
  Stack,
  Stepper,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { ApiError, apiGet, apiSend } from "./lib/api";

interface SetupDetail {
  setup_state: string;
  gates: Record<string, boolean>;
  org_profile: {
    legal_name: string | null;
    short_code: string | null;
    timezone: string | null;
  };
  backup: {
    configured: boolean;
    destination: string | null;
    last_restore_test_at: string | null;
    last_restore_test_result: string | null;
  };
  auth: {
    configured: boolean;
    method: string | null;
    last_test_at: string | null;
  };
  // Soft gate (R13): false until a fresh off-host audit anchor is configured. Never blocks finalize.
  tamper_evident: boolean;
}

const browserTz = (): string => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
};

// The first-run wizard (S8a–S8c). Shown whenever setup_state != OPERATIONAL. Resumable: the active
// step is derived from the live setup state + gates (doc 08 §2): bootstrap → org → storage → backup
// → auth → finalize.
export function SetupWizard({
  token,
  login,
  onFinalized,
}: {
  token: string | null;
  login: () => void;
  onFinalized: () => void;
}) {
  const [testRunning, setTestRunning] = useState(false);
  const detail = useQuery({
    queryKey: ["setup-detail", token],
    queryFn: () => apiGet<SetupDetail>("/api/v1/setup", token),
    enabled: !!token,
    // While the async restore-test drill runs, poll so G-C flips in the UI without a manual refresh.
    refetchInterval: testRunning ? 3000 : false,
  });

  const [secret, setSecret] = useState("");
  const [legalName, setLegalName] = useState("");
  const [shortCode, setShortCode] = useState("");
  const [timezone, setTimezone] = useState(browserTz());
  const [lockMode, setLockMode] = useState("GOVERNANCE");
  const [backupDest, setBackupDest] = useState("/var/lib/easysynq/backups");
  const [authMethod, setAuthMethod] = useState("LOCAL");
  const [mfaAck, setMfaAck] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const state = detail.data?.setup_state ?? "UNINITIALIZED";
  const orgSet = detail.data?.gates?.["G-E"] ?? false;
  const wormVerified = detail.data?.gates?.["G-B"] ?? false;
  const restorePassed = detail.data?.gates?.["G-C"] ?? false;
  const authConfigured = detail.data?.gates?.["G-D"] ?? false;
  const backupConfigured = detail.data?.backup?.configured ?? false;
  const restoreResult = detail.data?.backup?.last_restore_test_result ?? null;
  // Resumable step order (doc 08 R4: org → storage → backup → auth → finalize).
  const active =
    token && state === "IN_SETUP"
      ? !orgSet
        ? 1
        : !wormVerified
          ? 2
          : !restorePassed
            ? 3
            : !authConfigured
              ? 4
              : 5
      : 0;

  // Stop polling once the drill lands a result (PASS flips G-C; FAIL surfaces the reason).
  useEffect(() => {
    if (testRunning && (restorePassed || restoreResult === "FAIL")) setTestRunning(false);
  }, [testRunning, restorePassed, restoreResult]);

  // Prefill the org name from the persisted profile once it loads (resume), without clobbering edits.
  const persistedName = detail.data?.org_profile.legal_name;
  useEffect(() => {
    if (persistedName) setLegalName((cur) => cur || persistedName);
  }, [persistedName]);

  const run = async (fn: () => Promise<unknown>, after?: () => void): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      after?.();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const activate = (): Promise<void> =>
    run(
      () => apiSend("POST", "/api/v1/setup/bootstrap", token, { secret }),
      () => {
        setSecret("");
        void detail.refetch();
      },
    );

  const saveOrg = (): Promise<void> =>
    run(
      () =>
        apiSend("PATCH", "/api/v1/setup/org-profile", token, {
          legal_name: legalName,
          short_code: shortCode,
          timezone,
        }),
      () => void detail.refetch(),
    );

  const verifyStorage = (): Promise<void> =>
    run(
      () => apiSend("POST", "/api/v1/setup/verify-storage", token, { object_lock_mode: lockMode }),
      () => void detail.refetch(),
    );

  const configureBackup = (): Promise<void> =>
    run(
      () => apiSend("POST", "/api/v1/setup/configure-backup", token, { destination: backupDest }),
      () => void detail.refetch(),
    );

  const runRestoreTest = (): Promise<void> =>
    run(
      () => apiSend("POST", "/api/v1/setup/run-restore-test", token, {}),
      () => {
        setTestRunning(true);
        void detail.refetch();
      },
    );

  const configureAuth = (): Promise<void> =>
    run(
      () =>
        apiSend("POST", "/api/v1/setup/configure-auth", token, {
          method: authMethod,
          mfa_acknowledged: mfaAck,
        }),
      () => void detail.refetch(),
    );

  const finalize = (): Promise<void> =>
    run(() => apiSend("POST", "/api/v1/setup/finalize", token, {}), onFinalized);

  return (
    // Main landmark + useRouteChrome focus target for the pre-operational /setup route (rendered
    // outside AppShell; /setup and / never render together, so #main-content is never duplicated).
    <Container component="main" id="main-content" tabIndex={-1} size="sm" py="xl">
      <Stack gap="lg">
        <Stack gap={4}>
          <img
            src="/easysynq-mark.svg"
            alt=""
            aria-hidden="true"
            width={44}
            height={44}
            style={{ marginBottom: 4 }}
          />
          <Title order={1}>Welcome to EasySynQ</Title>
          <Text c="dimmed">First-run setup — stand up your controlled QMS.</Text>
        </Stack>

        {error && (
          <Alert color="red" title="Something went wrong">
            {error}
          </Alert>
        )}

        <Stepper active={active}>
          <Stepper.Step label="Activate" description="Bootstrap admin">
            <Stack gap="md" mt="md">
              {!token ? (
                <>
                  <Text size="sm">
                    Sign in with your identity provider, then enter the one-time install secret
                    (printed by <Code>easysynq setup mint-bootstrap</Code>) to become the first
                    administrator.
                  </Text>
                  <Group>
                    <Button onClick={login}>Sign in to begin</Button>
                  </Group>
                </>
              ) : (
                <>
                  <Text size="sm">
                    Paste the one-time install secret from{" "}
                    <Code>easysynq setup mint-bootstrap</Code>. You become the first System
                    Administrator.
                  </Text>
                  <PasswordInput
                    label="Install secret"
                    value={secret}
                    onChange={(e) => setSecret(e.currentTarget.value)}
                  />
                  <Group>
                    <Button onClick={() => void activate()} loading={busy} disabled={!secret}>
                      Activate
                    </Button>
                  </Group>
                </>
              )}
            </Stack>
          </Stepper.Step>

          <Stepper.Step label="Organization" description="Profile">
            <Stack gap="md" mt="md">
              <Text size="sm">
                Your organization profile. The timezone is authoritative for effective dates.
              </Text>
              <TextInput
                label="Legal name"
                value={legalName}
                onChange={(e) => setLegalName(e.currentTarget.value)}
              />
              <TextInput
                label="Short code"
                description="2–32 chars, A–Z 0–9 -"
                value={shortCode}
                onChange={(e) => setShortCode(e.currentTarget.value.toUpperCase())}
              />
              <TextInput
                label="Timezone (IANA)"
                value={timezone}
                onChange={(e) => setTimezone(e.currentTarget.value)}
              />
              <Group>
                <Button
                  onClick={() => void saveOrg()}
                  loading={busy}
                  disabled={!legalName || !shortCode}
                >
                  Save & continue
                </Button>
              </Group>
            </Stack>
          </Stepper.Step>

          <Stepper.Step label="Storage" description="WORM verify">
            <Stack gap="md" mt="md">
              <Text size="sm">
                Verify the vault bucket enforces WORM object-lock (a probe writes a tiny object and
                confirms an early delete is denied).
              </Text>
              <SegmentedControl
                value={lockMode}
                onChange={setLockMode}
                data={["GOVERNANCE", "COMPLIANCE"]}
              />
              <Text size="xs" c="dimmed">
                GOVERNANCE (recommended) keeps fresh-bucket restore + lawful-erasure possible.
                COMPLIANCE is immutable even to root.
              </Text>
              <Group>
                <Button onClick={() => void verifyStorage()} loading={busy}>
                  Verify WORM storage
                </Button>
                {wormVerified && (
                  <Text size="sm" c="teal">
                    ✓ verified
                  </Text>
                )}
              </Group>
            </Stack>
          </Stepper.Step>

          <Stepper.Step label="Backup" description="Restore-test">
            <Stack gap="md" mt="md">
              <Text size="sm">
                Configure admin-controlled backups, then prove a restore actually works. The drill
                backs up, restores into an isolated scratch namespace, and verifies integrity (row
                counts, blob SHA-256 re-hash, FK checks). Setup cannot finalize until it passes — a
                configured-but-unverified backup is treated as no backup.
              </Text>
              <TextInput
                label="Backup destination"
                description="A mounted local/NFS path the worker can write"
                value={backupDest}
                onChange={(e) => setBackupDest(e.currentTarget.value)}
              />
              <Group>
                <Button
                  variant="default"
                  onClick={() => void configureBackup()}
                  loading={busy}
                  disabled={!backupDest}
                >
                  {backupConfigured ? "Update backup config" : "Save backup config"}
                </Button>
                <Button
                  onClick={() => void runRestoreTest()}
                  loading={busy || testRunning}
                  disabled={!backupConfigured}
                >
                  Run backup + restore-test drill
                </Button>
                {restorePassed ? (
                  <Text size="sm" c="teal">
                    ✓ restore verified
                  </Text>
                ) : testRunning ? (
                  <Text size="sm" c="dimmed">
                    drill running…
                  </Text>
                ) : restoreResult === "FAIL" ? (
                  <Text size="sm" c="red">
                    ✗ last drill failed — fix and retry
                  </Text>
                ) : null}
              </Group>
            </Stack>
          </Stepper.Step>

          <Stepper.Step label="Authentication" description="Login proof">
            <Stack gap="md" mt="md">
              <Text size="sm">
                Confirm how users sign in and prove a real (non-bootstrap) login works, so the
                install is never stranded on a misconfigured identity provider. Local break-glass
                login always stays available. Sign-in is always via your Keycloak realm; federated
                SSO (LDAP/OIDC/SAML) is configured in Keycloak.
              </Text>
              <SegmentedControl
                value={authMethod}
                onChange={setAuthMethod}
                data={[
                  { label: "Local accounts", value: "LOCAL" },
                  { label: "Federated SSO", value: "FEDERATED" },
                ]}
              />
              <Checkbox
                checked={mfaAck}
                onChange={(e) => setMfaAck(e.currentTarget.checked)}
                label="I understand multi-factor authentication is strongly recommended (enrol it in Keycloak)."
              />
              <Group>
                <Button onClick={() => void configureAuth()} loading={busy}>
                  Verify authentication
                </Button>
                {authConfigured && (
                  <Text size="sm" c="teal">
                    ✓ login proven
                  </Text>
                )}
              </Group>
            </Stack>
          </Stepper.Step>

          <Stepper.Step label="Finalize" description="Go live">
            <Stack gap="md" mt="md">
              <Text size="sm">Review and finalize — this unlocks the QMS.</Text>
              {detail.data && !detail.data.tamper_evident && (
                <Alert color="yellow" title="Not yet tamper-evident">
                  No fresh off-host audit-checkpoint anchor is configured, so this install cannot
                  claim tamper-evidence yet. This does not block finalize — configure an off-host
                  sink afterward to clear the warning (doc 08 §8.3).
                </Alert>
              )}
              <List size="sm">
                {Object.entries(detail.data?.gates ?? {}).map(([key, ok]) => (
                  <List.Item key={key}>
                    {key}: {ok ? "✓ ready" : "✗ not ready"}
                  </List.Item>
                ))}
              </List>
              <Group>
                <Button onClick={() => void finalize()} loading={busy} color="teal">
                  Finalize setup
                </Button>
              </Group>
            </Stack>
          </Stepper.Step>
        </Stepper>
      </Stack>
    </Container>
  );
}
