import { ActionIcon, Burger, Button, Group, Indicator, Menu, Text } from "@mantine/core";
import { Link } from "react-router-dom";
import { IconBell, IconSearch, IconTasks, IconUser } from "../../lib/icons";
import { useAuth } from "../../lib/auth";
import { useAckCount } from "./useAckCount";

// S-web-6: the search box is a real button (not a read-only text input) — assistive tech announces an
// action, not an edit field — and it is rendered on every breakpoint (no `visibleFrom`) so touch users
// on small screens can open the palette without the keyboard hotkeys. To keep the no-wrap header from
// overflowing on ~320px phones it is icon-only below `sm` (the "Search (⌘K)" label is desktop-only);
// the `aria-label` (which matches the visible label on `sm+`, so no label/name mismatch) keeps the
// button named when the label is hidden.
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
  const ackCount = useAckCount();
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
        <Indicator label={ackCount} size={16} disabled={ackCount === 0}>
          <ActionIcon
            component={Link}
            to="/tasks?type=DOC_ACK&state=PENDING"
            variant="subtle"
            aria-label="Acknowledgements"
          >
            <IconBell />
          </ActionIcon>
        </Indicator>
        <Menu position="bottom-end">
          <Menu.Target>
            <ActionIcon variant="subtle" aria-label="Account">
              <IconUser />
            </ActionIcon>
          </Menu.Target>
          <Menu.Dropdown>
            <Menu.Item onClick={logout}>Sign out</Menu.Item>
          </Menu.Dropdown>
        </Menu>
      </Group>
    </Group>
  );
}
