import { ActionIcon, Drawer, Group } from "@mantine/core";
import { useState, type ReactNode } from "react";
import { clampDrawerWidth, DRAWER_DEFAULT, DRAWER_MAX, DRAWER_MIN } from "./drawerWidth";

const RESIZE_STEP = 32;

// Right-side detail drawer (DP-3). Mantine Drawer gives focus-trap + Escape + scrim + ARIA dialog
// semantics for free. A left-edge handle resizes the width (clamped 360–640).
export function DetailDrawer({
  opened,
  onClose,
  title,
  children,
}: {
  opened: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
}) {
  const [width, setWidth] = useState(DRAWER_DEFAULT);
  const resizeBy = (delta: number) => setWidth((current) => clampDrawerWidth(current + delta));

  const startResize = (e: React.PointerEvent) => {
    e.preventDefault();
    const move = (ev: PointerEvent) => setWidth(clampDrawerWidth(window.innerWidth - ev.clientX));
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  const resizeWithKeyboard = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowLeft") {
      e.preventDefault();
      resizeBy(RESIZE_STEP);
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      resizeBy(-RESIZE_STEP);
    } else if (e.key === "Home") {
      e.preventDefault();
      setWidth(DRAWER_MIN);
    } else if (e.key === "End") {
      e.preventDefault();
      setWidth(DRAWER_MAX);
    }
  };

  return (
    <Drawer
      opened={opened}
      onClose={onClose}
      position="right"
      size={width}
      title={title}
      trapFocus
      closeOnEscape
      closeOnClickOutside
      withOverlay
    >
      {/* A focusable separator with value metadata is the WAI-ARIA Window Splitter pattern. The
          generic jsx-a11y rules do not recognize separator as an operable role. */}
      {/* eslint-disable jsx-a11y/no-noninteractive-element-interactions, jsx-a11y/no-noninteractive-tabindex */}
      <div
        role="separator"
        aria-label="Resize panel"
        aria-orientation="vertical"
        aria-valuemin={DRAWER_MIN}
        aria-valuemax={DRAWER_MAX}
        aria-valuenow={width}
        aria-valuetext={`${width} pixels`}
        tabIndex={0}
        onPointerDown={startResize}
        onKeyDown={resizeWithKeyboard}
        style={{
          position: "absolute",
          insetBlock: 0,
          insetInlineStart: 0,
          width: 6,
          cursor: "col-resize",
        }}
      />
      {/* eslint-enable jsx-a11y/no-noninteractive-element-interactions, jsx-a11y/no-noninteractive-tabindex */}
      <Group role="group" aria-label="Panel width controls" gap={4} justify="flex-end" mb="xs">
        <ActionIcon
          variant="subtle"
          size="sm"
          aria-label="Narrow panel"
          title="Narrow panel"
          disabled={width === DRAWER_MIN}
          onClick={() => resizeBy(-RESIZE_STEP)}
        >
          −
        </ActionIcon>
        <ActionIcon
          variant="subtle"
          size="sm"
          aria-label="Widen panel"
          title="Widen panel"
          disabled={width === DRAWER_MAX}
          onClick={() => resizeBy(RESIZE_STEP)}
        >
          +
        </ActionIcon>
      </Group>
      {children}
    </Drawer>
  );
}
