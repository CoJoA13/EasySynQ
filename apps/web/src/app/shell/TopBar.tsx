import { ActionIcon, Burger, Group, Indicator, Menu, Text, TextInput } from "@mantine/core";
import { useAuth } from "../../lib/auth";

// S-web-1: ⌘K search is a non-functional slot; task/ack bells show static affordances. Behaviour later.
export function TopBar({
  navOpened,
  onToggleNav,
}: {
  navOpened: boolean;
  onToggleNav: () => void;
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
      <TextInput placeholder="Search (⌘K)" w={280} aria-label="Search" disabled visibleFrom="sm" />
      <Group gap="xs" wrap="nowrap">
        <Indicator disabled>
          <ActionIcon variant="subtle" aria-label="Tasks">
            &#9684;
          </ActionIcon>
        </Indicator>
        <Indicator disabled>
          <ActionIcon variant="subtle" aria-label="Acknowledgements">
            &#128276;
          </ActionIcon>
        </Indicator>
        <Menu position="bottom-end">
          <Menu.Target>
            <ActionIcon variant="subtle" aria-label="Account">
              &#128100;
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
