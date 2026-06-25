import { Button, Stack, Switch, Text, Title } from "@mantine/core";
import { useEffect, useState } from "react";
import { ErrorState, LoadingState, MutationErrorState, NoAccessState } from "../lib/states";
import type { OrgConfig, OrgConfigUpdate } from "../lib/types";
import { NotificationHealthPanel } from "./NotificationHealthPanel";
import { WorkingCalendarEditor } from "./WorkingCalendarEditor";
import { useOrgConfig, useUpdateOrgConfig } from "./hooks";

interface Working {
  notifications_email_enabled: boolean;
  notifications_escalation_pierce_quiet_hours: boolean;
}

function toWorking(c: OrgConfig): Working {
  return {
    notifications_email_enabled: c.notifications_email_enabled,
    notifications_escalation_pierce_quiet_hours: c.notifications_escalation_pierce_quiet_hours,
  };
}

function buildUpdate(w: Working, b: OrgConfig): OrgConfigUpdate {
  const body: OrgConfigUpdate = {};
  if (w.notifications_email_enabled !== b.notifications_email_enabled) {
    body.notifications_email_enabled = w.notifications_email_enabled;
  }
  if (
    w.notifications_escalation_pierce_quiet_hours !== b.notifications_escalation_pierce_quiet_hours
  ) {
    body.notifications_escalation_pierce_quiet_hours =
      w.notifications_escalation_pierce_quiet_hours;
  }
  return body;
}

export function ConfigAdmin() {
  const cfg = useOrgConfig();
  const update = useUpdateOrgConfig();
  const [working, setWorking] = useState<Working | null>(null);

  useEffect(() => {
    if (cfg.data) setWorking(toWorking(cfg.data));
  }, [cfg.data]);

  // The page no-access gate is the data-403 (config.update), NOT a usePermissions probe (which would
  // flash NoAccessState to a legitimate admin on a cold /admin cache). forbidden → error → loading.
  if (cfg.forbidden) {
    return <NoAccessState message="You need config.update to manage notification configuration." />;
  }
  if (cfg.isError) {
    return <ErrorState title="Couldn't load configuration" onRetry={() => void cfg.refetch()} />;
  }
  if (cfg.isLoading || !working || !cfg.data) {
    return <LoadingState label="Loading configuration" />;
  }

  const body = buildUpdate(working, cfg.data);
  const dirty = Object.keys(body).length > 0;
  const save = () => update.mutate(body);

  return (
    <Stack gap="xl">
      <Stack gap="md">
        <Title order={2}>Notifications</Title>
        <Switch
          label="Email delivery (organisation-wide)"
          aria-label="Email delivery (organisation-wide)"
          description="When on, the worker sends email notifications via the configured SMTP relay. Default off — enable after configuring SMTP. Emails carry a summary + link only, never controlled content."
          checked={working.notifications_email_enabled}
          onChange={(e) =>
            setWorking({ ...working, notifications_email_enabled: e.currentTarget.checked })
          }
        />
        <Switch
          label="Escalation pierces quiet hours"
          aria-label="Escalation pierces quiet hours"
          description="When on (the default), critical and escalation notifications are delivered immediately even inside a user's quiet hours."
          checked={working.notifications_escalation_pierce_quiet_hours}
          onChange={(e) =>
            setWorking({
              ...working,
              notifications_escalation_pierce_quiet_hours: e.currentTarget.checked,
            })
          }
        />
        <div>
          <Button onClick={save} disabled={!dirty} loading={update.isPending}>
            Save changes
          </Button>
          {update.isSuccess && !dirty && (
            <Text size="sm" c="dimmed" mt="xs">
              Saved.
            </Text>
          )}
        </div>
        {update.isError && (
          <MutationErrorState title="Couldn't save configuration" error={update.error} />
        )}
      </Stack>

      <WorkingCalendarEditor />

      <NotificationHealthPanel />
    </Stack>
  );
}
