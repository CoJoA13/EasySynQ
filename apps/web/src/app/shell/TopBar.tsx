// apps/web/src/app/shell/TopBar.tsx
import { ActionIcon, Burger, Group, Kbd, Menu, Text, UnstyledButton } from "@mantine/core";
import { Link } from "react-router-dom";
import { IconSearch, IconTasks, IconUser } from "../../lib/icons";
import { useAuth } from "../../lib/auth";
import { NotificationBell } from "../../features/notifications/NotificationBell";
import { usePermissions } from "./usePermissions";

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
  const perms = usePermissions();
  return (
    <Group h="100%" px="md" gap="md" wrap="nowrap">
      <Group gap="sm" wrap="nowrap">
        <Burger
          opened={navOpened}
          onClick={onToggleNav}
          hiddenFrom="md"
          size="sm"
          aria-label="Toggle navigation"
          aria-expanded={navOpened}
        />
        {/* S-ui-redesign: the brand moved to the head of the rail; it stays here only while the rail
            is collapsed behind the burger (below `md`), so the product is still named on a phone. */}
        <Group gap="xs" wrap="nowrap" hiddenFrom="md">
          <img src="/easysynq-mark-simple.svg" alt="" aria-hidden="true" width={22} height={22} />
          <Text fw={700}>EasySynQ</Text>
        </Group>
      </Group>
      {/* Still a BUTTON (S-web-6: it opens the palette, it is not a text input), styled as the
          field it stands in for — placeholder text and the ⌘K hint — so the palette is discoverable
          without a reader having to know the shortcut. Icon-only below `sm`. */}
      <UnstyledButton
        onClick={onOpenSearch}
        aria-label="Search (⌘K)"
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 8,
          height: 36,
          paddingInline: 10,
          borderRadius: "var(--es-radius-sm)",
          border: "1px solid var(--es-border-2)",
          background: "var(--es-surface)",
          color: "var(--es-text-muted)",
          flex: "0 1 360px",
          minWidth: 0,
        }}
      >
        <IconSearch size={16} />
        <Text component="span" c="dimmed" size="sm" visibleFrom="sm" style={{ flex: 1, textAlign: "left" }}>
          Search documents, records, tasks…
        </Text>
        <Kbd size="xs" visibleFrom="sm">
          ⌘K
        </Kbd>
      </UnstyledButton>
      <Group gap="xs" wrap="nowrap" ml="auto">
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
            {perms.can("config.update") && (
              <Menu.Item component={Link} to="/admin/config">
                Administration
              </Menu.Item>
            )}
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
