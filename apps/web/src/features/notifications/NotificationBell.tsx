// apps/web/src/features/notifications/NotificationBell.tsx
import {
  ActionIcon,
  Anchor,
  Button,
  Group,
  Indicator,
  Popover,
  ScrollArea,
  Stack,
  Text,
} from "@mantine/core";
import { useState } from "react";
import { Link } from "react-router-dom";
import { IconBell } from "../../lib/icons";
import { InlineState } from "../../lib/states";
import { useNotificationCount, useNotifications, useNotificationStream } from "./hooks";
import { useMarkAllRead } from "./mutations";
import { NotificationItem } from "./NotificationItem";

// The merged TopBar bell (S-notify-fe). Awareness lives here; the Tasks icon stays the work entry. The
// badge reuses the existing ack-bell's three-state, never-confident-zero pattern: a numeric badge (99+
// past the cap) / a gray indeterminate dot on a failed count / nothing on a true zero. The Indicator is
// the OUTER wrapper so its badge overlays the bell while the Popover anchors to the ActionIcon itself.
export function NotificationBell() {
  useNotificationStream();
  const [opened, setOpened] = useState(false);
  const { count, isError } = useNotificationCount();
  const list = useNotifications("recent", opened);
  const markAll = useMarkAllRead();

  const hasCount = !isError && count > 0;
  const label = isError
    ? "Notifications (count unavailable)"
    : count > 0
      ? `Notifications, ${count} unread`
      : "Notifications";
  const badge = count > 99 ? "99+" : count;
  const rows = list.data ?? [];

  return (
    <Indicator
      label={hasCount ? badge : undefined}
      size={isError ? 10 : 16}
      color={isError ? "gray" : undefined}
      disabled={!hasCount && !isError}
    >
      <Popover
        position="bottom-end"
        width={360}
        opened={opened}
        onChange={setOpened}
        withArrow
        shadow="md"
      >
        <Popover.Target>
          <ActionIcon variant="subtle" aria-label={label} onClick={() => setOpened((o) => !o)}>
            <IconBell />
          </ActionIcon>
        </Popover.Target>
        <Popover.Dropdown p="xs">
          <Stack gap="xs">
            <Group justify="space-between" px="xs">
              <Text fw={600} size="sm">
                Notifications
              </Text>
              <Button
                variant="subtle"
                size="compact-xs"
                onClick={() => markAll.mutate()}
                disabled={markAll.isPending}
              >
                Mark all read
              </Button>
            </Group>
            <ScrollArea.Autosize mah={360}>
              {list.isLoading ? (
                <InlineState kind="loading">Loading notifications…</InlineState>
              ) : list.isError ? (
                <InlineState kind="error" onRetry={() => void list.refetch()}>
                  Couldn&apos;t load notifications.
                </InlineState>
              ) : rows.length === 0 ? (
                <InlineState kind="empty">You&apos;re all caught up.</InlineState>
              ) : (
                <Stack gap="xs">
                  {rows.map((n) => (
                    <NotificationItem
                      key={n.id}
                      notification={n}
                      onNavigate={() => setOpened(false)}
                    />
                  ))}
                </Stack>
              )}
            </ScrollArea.Autosize>
            <Group justify="space-between" px="xs">
              <Anchor
                component={Link}
                to="/settings/notifications"
                size="xs"
                onClick={() => setOpened(false)}
              >
                Notification settings
              </Anchor>
              <Anchor
                component={Link}
                to="/notifications"
                size="xs"
                onClick={() => setOpened(false)}
              >
                See all
              </Anchor>
            </Group>
          </Stack>
        </Popover.Dropdown>
      </Popover>
    </Indicator>
  );
}
