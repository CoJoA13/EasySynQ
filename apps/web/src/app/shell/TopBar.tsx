// apps/web/src/app/shell/TopBar.tsx
import { ActionIcon, Burger, Button, Group, Menu, Text } from "@mantine/core";
import { Link } from "react-router-dom";
import { IconSearch, IconTasks, IconUser } from "../../lib/icons";
import { useAuth } from "../../lib/auth";
import { NotificationBell } from "../../features/notifications/NotificationBell";

// S-notify-fe: the ack-count Indicator is retired — the bell is now the merged NOTIFICATION bell
// (awareness), and the Tasks icon stays the explicit WORK entry. DOC_ACK assignments flow as
// notifications, so the bell's unread badge encompasses new-ack awareness; the durable open-ack work
// count lives at /tasks. (useAckCount is unchanged and still powers Home's DoCard.)
//
// S-web-6: the search box is a real button (not a read-only text input) and renders on every breakpoint;
// it is icon-only below `sm` to keep the no-wrap header from overflowing on ~320px phones.
export function TopBar({
  navOpened,
  onToggleNav,
  onOpenSearch,
}: {
  navOpened: boolean;
  onToggleNav: () => void;
  onOpenSearch: () => void;
}) {
  const { logout } = useAuth();
  return (
    <Group h="100%" px="md" justify="space-between" wrap="nowrap">
      <Group gap="sm" wrap="nowrap">
        <Burger
          opened={navOpened}
          onClick={onToggleNav}
          hiddenFrom="md"
          size="sm"
          aria-label="Toggle navigation"
        />
        <Text fw={700}>EasySynQ</Text>
      </Group>
      <Button
        variant="default"
        color="gray"
        fw={400}
        onClick={onOpenSearch}
        aria-label="Search (⌘K)"
      >
        <IconSearch size={16} />
        <Text component="span" c="dimmed" ml={6} visibleFrom="sm">
          Search (⌘K)
        </Text>
      </Button>
      <Group gap="xs" wrap="nowrap">
        <ActionIcon component={Link} to="/tasks" variant="subtle" aria-label="Tasks">
          <IconTasks />
        </ActionIcon>
        <NotificationBell />
        <Menu position="bottom-end">
          <Menu.Target>
            <ActionIcon variant="subtle" aria-label="Account">
              <IconUser />
            </ActionIcon>
          </Menu.Target>
          <Menu.Dropdown>
            <Menu.Item component={Link} to="/settings/notifications">
              Notification settings
            </Menu.Item>
            <Menu.Item onClick={logout}>Sign out</Menu.Item>
          </Menu.Dropdown>
        </Menu>
      </Group>
    </Group>
  );
}
