import {
  Alert,
  Button,
  Container,
  Group,
  SegmentedControl,
  Stack,
  Switch,
  Text,
  Title,
} from "@mantine/core";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ErrorState, LoadingState, MutationErrorState } from "../../lib/states";
import type {
  NotificationClass,
  NotificationDigestMode,
  NotificationPreferences,
  NotificationPreferencesUpdate,
} from "../../lib/types";
import { CLASS_META } from "./classMeta";
import { useNotificationPreferences } from "./hooks";
import { useUpdateNotificationPreferences } from "./mutations";

const MODE_DATA: { value: NotificationDigestMode; label: string }[] = [
  { value: "immediate", label: "Immediate" },
  { value: "daily", label: "Daily" },
  { value: "off", label: "Off" },
];

const CLASS_KEYS: NotificationClass[] = CLASS_META.map((c) => c.key);

// The page's local working-state — a mutable mirror of the effective preferences. Quiet hours are
// modelled as an enabled flag + two HH:MM strings so the both-or-neither contract is structural (the FE
// can only ever send both quiet fields together, or both null). Timing fields are present from the start
// so Task 3 only adds controls, not state.
interface Working {
  email_enabled: boolean;
  digest_modes: Record<NotificationClass, NotificationDigestMode>;
  digest_hour: number;
  timezone: string;
  quietEnabled: boolean;
  quietStart: string;
  quietEnd: string;
}

function toWorking(p: NotificationPreferences): Working {
  return {
    email_enabled: p.email_enabled,
    digest_modes: { ...p.digest_modes },
    digest_hour: p.digest_hour,
    timezone: p.timezone,
    quietEnabled: p.quiet_start !== null && p.quiet_end !== null,
    quietStart: p.quiet_start ?? "22:00",
    quietEnd: p.quiet_end ?? "07:00",
  };
}

// Diff the working state against the loaded baseline → a PARTIAL update of only the changed fields.
function buildUpdate(w: Working, b: NotificationPreferences): NotificationPreferencesUpdate {
  const body: NotificationPreferencesUpdate = {};
  if (w.email_enabled !== b.email_enabled) body.email_enabled = w.email_enabled;

  const modes: Partial<Record<NotificationClass, NotificationDigestMode>> = {};
  for (const c of CLASS_KEYS) {
    if (w.digest_modes[c] !== b.digest_modes[c]) modes[c] = w.digest_modes[c];
  }
  if (Object.keys(modes).length > 0) body.digest_modes = modes;

  if (w.digest_hour !== b.digest_hour) body.digest_hour = w.digest_hour;
  if (w.timezone !== b.timezone) body.timezone = w.timezone;

  const wStart = w.quietEnabled ? w.quietStart : null;
  const wEnd = w.quietEnabled ? w.quietEnd : null;
  if (wStart !== b.quiet_start || wEnd !== b.quiet_end) {
    body.quiet_start = wStart;
    body.quiet_end = wEnd;
  }
  return body;
}

export function NotificationSettingsPage() {
  const prefs = useNotificationPreferences();
  const update = useUpdateNotificationPreferences();
  const qc = useQueryClient();
  const [working, setWorking] = useState<Working | null>(null);

  // Seed/refresh the working state from the loaded prefs (the only refetch is the post-save
  // invalidation, so syncing on data identity resets to the saved values after a Save).
  useEffect(() => {
    if (prefs.data) setWorking(toWorking(prefs.data));
  }, [prefs.data]);

  const baseline = prefs.data;
  const body: NotificationPreferencesUpdate =
    working && baseline ? buildUpdate(working, baseline) : {};
  const dirty = Object.keys(body).length > 0;
  const quietInvalid = !!working?.quietEnabled && (!working.quietStart || !working.quietEnd);

  function save() {
    if (!dirty || quietInvalid || !baseline) return;
    // Capture a snapshot of baseline + the body we're about to send so we can apply the changes
    // optimistically into the query cache on success. TanStack Query's structural sharing keeps the
    // same prefs.data reference when the re-fetch returns structurally identical data, which would
    // leave the useEffect no-op and dirty=true — hiding the "Saved." confirmation. Writing the
    // merged result into the cache ensures prefs.data gets a new reference even when the server
    // round-trip returns the same bytes.
    const capturedBaseline = baseline;
    const capturedBody = body;
    update.mutate(body, {
      onSuccess: () => {
        const merged: NotificationPreferences = {
          ...capturedBaseline,
          ...capturedBody,
          digest_modes: {
            ...capturedBaseline.digest_modes,
            ...(capturedBody.digest_modes ?? {}),
          },
        };
        qc.setQueryData(["notification-preferences"], merged);
      },
    });
  }

  return (
    <Container size="sm" py="xl">
      <Stack gap="lg">
        <Group justify="space-between">
          <Title order={1}>Notification settings</Title>
          <Button component={Link} to="/" variant="subtle">
            Back to app
          </Button>
        </Group>

        {prefs.isLoading || !working ? (
          <LoadingState label="Loading preferences" />
        ) : prefs.isError ? (
          <ErrorState title="Couldn't load preferences" onRetry={() => void prefs.refetch()} />
        ) : (
          <Stack gap="lg">
            <Alert variant="light" color="gray" title="Your in-app bell is always immediate">
              These settings control your <strong>email</strong> only. The in-app notification bell
              shows every notification as it happens, whatever you choose below.
            </Alert>

            <Switch
              label="Email notifications"
              aria-label="Email notifications"
              description="Emails carry only a summary and a link — never controlled content — and require your administrator to enable email delivery for the organisation."
              checked={working.email_enabled}
              onChange={(e) => setWorking({ ...working, email_enabled: e.currentTarget.checked })}
            />

            <Stack gap="xs">
              <Title order={2} size="h4">
                Email cadence by type
              </Title>
              <Text size="sm" c="dimmed">
                Immediate = email as it happens · Daily = bundled into your daily digest · Off =
                in-app only, no email.
              </Text>
              {CLASS_META.map((c) => (
                <Stack key={c.key} gap={4} mt="xs">
                  <Group gap="xs">
                    <Text fw={600}>{c.label}</Text>
                    {c.inAppOnly && (
                      <Text size="xs" c="dimmed">
                        (in-app only today)
                      </Text>
                    )}
                  </Group>
                  <Text size="sm" c="dimmed">
                    {c.helper}
                  </Text>
                  <SegmentedControl
                    fullWidth
                    aria-label={`Email cadence — ${c.label}`}
                    value={working.digest_modes[c.key]}
                    onChange={(v) =>
                      setWorking({
                        ...working,
                        digest_modes: {
                          ...working.digest_modes,
                          [c.key]: v as NotificationDigestMode,
                        },
                      })
                    }
                    data={MODE_DATA}
                  />
                </Stack>
              ))}
              {!working.email_enabled && (
                <Text size="sm" c="dimmed" mt="xs">
                  Email is currently off — these per-type cadences apply once email is on.
                </Text>
              )}
            </Stack>

            {/* Task 3 inserts the "Daily digest" timing section here. */}

            <Group>
              <Button onClick={save} disabled={!dirty || quietInvalid} loading={update.isPending}>
                Save changes
              </Button>
              {update.isSuccess && !dirty && (
                <Text size="sm" c="dimmed">
                  Saved.
                </Text>
              )}
            </Group>
            {update.isError && (
              <MutationErrorState title="Couldn't save your preferences" error={update.error} />
            )}
          </Stack>
        )}
      </Stack>
    </Container>
  );
}
