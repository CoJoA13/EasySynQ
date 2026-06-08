import { ActionIcon, Burger, Group, Indicator, Menu, Text, TextInput } from "@mantine/core";
import type { KeyboardEvent } from "react";
import { useAuth } from "../../lib/auth";

// S-web-6: the ⌘K box is now live — it opens the command palette (AppShell owns the modal state).
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
  function onSearchKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onOpenSearch();
    }
  }
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
      <TextInput
        placeholder="Search (⌘K)"
        w={280}
        aria-label="Open search"
        readOnly
        onClick={onOpenSearch}
        onKeyDown={onSearchKeyDown}
        visibleFrom="sm"
        styles={{ input: { cursor: "pointer" } }}
      />
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
