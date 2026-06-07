import { Drawer } from "@mantine/core";
import { useState, type ReactNode } from "react";
import { clampDrawerWidth, DRAWER_DEFAULT } from "./drawerWidth";

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
      <div
        role="separator"
        aria-label="Resize panel"
        aria-orientation="vertical"
        onPointerDown={startResize}
        style={{
          position: "absolute",
          insetBlock: 0,
          insetInlineStart: 0,
          width: 6,
          cursor: "col-resize",
        }}
      />
      {children}
    </Drawer>
  );
}
