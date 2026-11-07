import { AppShell as MantineAppShell, CloseButton, Group, ScrollArea, Text } from "@mantine/core";
import { useDisclosure, useHotkeys } from "@mantine/hooks";
import { useEffect } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { ApplicationErrorBoundary } from "../errors/ApplicationErrorBoundary";
import { NotFoundPage } from "../errors/NotFoundPage";
import { RouteErrorPage } from "../errors/RouteErrorPage";
import { CommandPalette } from "../../features/search/CommandPalette";
import { classifyEffectiveView } from "../../lib/effectiveView";
import { MutationFeedbackOutlet } from "../../lib/mutationFeedback";
import { RouteAnnouncement } from "../../lib/routeChrome";
import { Breadcrumb } from "./Breadcrumb";
import { LeftRail } from "./LeftRail";
import { RailFoot } from "./RailFoot";
import { TopBar } from "./TopBar";

export interface AppShellProps {
  notFound?: boolean;
}

export function AppShell({ notFound = false }: AppShellProps) {
  const [navOpened, { toggle: toggleNav, close: closeNav }] = useDisclosure(false);
  const [searchOpened, { open: openSearch, close: closeSearch }] = useDisclosure(false);
  const { pathname, search } = useLocation();
  const routeResetKey = classifyEffectiveView(pathname, new URLSearchParams(search)).recoveryKey;
  // ⌘K / Ctrl-K must fire even while focus is in an input (empty tagsToIgnore); "/" must NOT hijack
  // typing (the default ignore-list covers INPUT/TEXTAREA/SELECT). Hence two separate bindings.
  useHotkeys([["mod+K", openSearch]], []);
  useHotkeys([["/", openSearch]]);
  // In the `alt` layout the open mobile navbar is full-width, 100dvh and stacked ABOVE the header,
  // so it covers the burger that opened it (Codex P1 on #673). The navbar therefore carries its own
  // close control below `md`, closes itself when a destination is chosen, and closes on Escape.
  useEffect(() => {
    closeNav();
  }, [pathname, closeNav]);
  useEffect(() => {
    if (!navOpened) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeNav();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [navOpened, closeNav]);
  return (
    // Shell dimensions come from the layout tokens, not literals. tokens.css is the single source
    // for both Mantine and Tailwind, and these two ramps existed with ZERO readers before S-ui-1.
    // Mantine's rem() passes a bare `var(...)` through untouched (it only converts numbers and
    // px/number-like strings), so the custom property reaches --app-shell-header-height verbatim.
    <MantineAppShell
      // `alt`: the rail runs the full viewport height and the top bar sits beside it, so the brand
      // at the head of the rail is the top-left anchor (the Figma AppShell) instead of hanging
      // under a full-width header.
      layout="alt"
      header={{ height: "var(--es-topbar-h)" }}
      navbar={{
        width: "var(--es-sidebar-w)",
        breakpoint: "md",
        collapsed: { mobile: !navOpened },
      }}
      padding="lg"
      // S-ui-redesign: the three shell regions take their own surfaces — a white rail and top bar on
      // the paper-toned page ground — so the chrome reads as chrome. Tokens, so dark comes free.
      styles={{
        header: { background: "var(--es-surface)", borderColor: "var(--es-border)" },
        navbar: { background: "var(--es-sidebar)", borderColor: "var(--es-border)" },
        main: { background: "var(--es-bg)" },
      }}
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
        <MantineAppShell.Section hiddenFrom="md" px="sm" pt="sm">
          <Group justify="space-between" wrap="nowrap">
            <Group gap="xs" wrap="nowrap">
              <img
                src="/easysynq-mark-simple.svg"
                alt=""
                aria-hidden="true"
                width={22}
                height={22}
              />
              <Text fw={700}>EasySynQ</Text>
            </Group>
            <CloseButton aria-label="Close navigation" onClick={closeNav} />
          </Group>
        </MantineAppShell.Section>
        {/* The rail outgrew short viewports (nav items accrue per register slice) — without a
            scrollable section the overflow is simply clipped and unreachable. */}
        <MantineAppShell.Section grow component={ScrollArea}>
          <LeftRail />
        </MantineAppShell.Section>
        {/* The foot does NOT grow and sits outside the ScrollArea, so the theme control and the
            organization clock stay reachable at any viewport height while the nav list scrolls. */}
        <MantineAppShell.Section>
          <RailFoot />
        </MantineAppShell.Section>
      </MantineAppShell.Navbar>
      <MantineAppShell.Main id="main-content" tabIndex={-1}>
        <Breadcrumb notFound={notFound} />
        <MutationFeedbackOutlet />
        <RouteAnnouncement />
        <ApplicationErrorBoundary
          resetKey={routeResetKey}
          fallback={({ onReset, error }) => <RouteErrorPage onRetry={onReset} error={error} />}
        >
          {notFound ? <NotFoundPage /> : <Outlet />}
        </ApplicationErrorBoundary>
      </MantineAppShell.Main>
      <CommandPalette opened={searchOpened} onClose={closeSearch} />
    </MantineAppShell>
  );
}
