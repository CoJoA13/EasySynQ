import { AppShell as MantineAppShell, ScrollArea } from "@mantine/core";
import { useDisclosure, useHotkeys } from "@mantine/hooks";
import { Outlet } from "react-router-dom";
import { CommandPalette } from "../../features/search/CommandPalette";
import { Breadcrumb } from "./Breadcrumb";
import { LeftRail } from "./LeftRail";
import { TopBar } from "./TopBar";

export function AppShell() {
  const [navOpened, { toggle: toggleNav }] = useDisclosure(false);
  const [searchOpened, { open: openSearch, close: closeSearch }] = useDisclosure(false);
  // ⌘K / Ctrl-K must fire even while focus is in an input (empty tagsToIgnore); "/" must NOT hijack
  // typing (the default ignore-list covers INPUT/TEXTAREA/SELECT). Hence two separate bindings.
  useHotkeys([["mod+K", openSearch]], []);
  useHotkeys([["/", openSearch]]);
  return (
    <MantineAppShell
      header={{ height: 60 }}
      navbar={{ width: 256, breakpoint: "md", collapsed: { mobile: !navOpened } }}
      padding="md"
    >
      {/* Skip-link: zIndex above the Mantine header (z-index 100) so keyboard focus isn't
          obscured by the sticky header (WCAG 2.2 Focus Not Obscured). */}
      <a
        href="#main-content"
        style={{
          position: "absolute",
          left: -9999,
          top: 8,
          zIndex: 9999,
          padding: "8px 12px",
          background: "var(--es-surface)",
          border: "1px solid var(--es-border)",
          borderRadius: "var(--es-radius-sm)",
        }}
        onFocus={(e) => (e.currentTarget.style.left = "8px")}
        onBlur={(e) => (e.currentTarget.style.left = "-9999px")}
      >
        Skip to content
      </a>
      <MantineAppShell.Header>
        <TopBar navOpened={navOpened} onToggleNav={toggleNav} onOpenSearch={openSearch} />
      </MantineAppShell.Header>
      <MantineAppShell.Navbar>
        {/* The rail outgrew short viewports (nav items accrue per register slice) — without a
            scrollable section the overflow is simply clipped and unreachable. */}
        <MantineAppShell.Section grow component={ScrollArea}>
          <LeftRail />
        </MantineAppShell.Section>
      </MantineAppShell.Navbar>
      <MantineAppShell.Main id="main-content">
        <Breadcrumb />
        <Outlet />
      </MantineAppShell.Main>
      <CommandPalette opened={searchOpened} onClose={closeSearch} />
    </MantineAppShell>
  );
}
