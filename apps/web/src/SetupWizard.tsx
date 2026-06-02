import {
  Alert,
  Button,
  Code,
  Container,
  Group,
  List,
  PasswordInput,
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
}

const browserTz = (): string => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
};

// The first-run wizard (S8a). Shown whenever setup_state != OPERATIONAL. It is resumable: the
// active step is derived from the live setup state + gates (doc 08 §2). Steps deferred to S8b/S8c
// (storage/WORM, backup+restore, auth) are intentionally absent here.
export function SetupWizard({
  token,
  login,
  onFinalized,
}: {
  token: string | null;
  login: () => void;
  onFinalized: () => void;
}) {
  const detail = useQuery({
    queryKey: ["setup-detail", token],
    queryFn: () => apiGet<SetupDetail>("/api/v1/setup", token),
    enabled: !!token,
  });

  const [secret, setSecret] = useState("");
  const [legalName, setLegalName] = useState("");
  const [shortCode, setShortCode] = useState("");
  const [timezone, setTimezone] = useState(browserTz());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const state = detail.data?.setup_state ?? "UNINITIALIZED";
  const orgSet = detail.data?.gates?.["G-E"] ?? false;
  const active = token && state === "IN_SETUP" ? (orgSet ? 2 : 1) : 0;

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

  const finalize = (): Promise<void> =>
    run(() => apiSend("POST", "/api/v1/setup/finalize", token, {}), onFinalized);

  return (
    <Container size="sm" py="xl">
      <Stack gap="lg">
        <Stack gap={4}>
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
              <Text size="sm">Your organization profile. The timezone is authoritative for effective dates.</Text>
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

          <Stepper.Step label="Finalize" description="Go live">
            <Stack gap="md" mt="md">
              <Text size="sm">Review and finalize — this unlocks the QMS.</Text>
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
