import { ActionIcon, Drawer, Group } from "@mantine/core";
import { useEffect, useId, useState, type ReactNode } from "react";
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
  const [viewportWidth, setViewportWidth] = useState(() =>
    typeof window === "undefined" ? DRAWER_MAX : window.innerWidth,
  );
  const paneId = useId();
  const resizeMax = Math.min(DRAWER_MAX, viewportWidth);
  const canResize = resizeMax > DRAWER_MIN;
  const effectiveWidth = canResize ? Math.min(width, resizeMax) : DRAWER_MIN;

  useEffect(() => {
    const updateViewportWidth = () => setViewportWidth(window.innerWidth);
    window.addEventListener("resize", updateViewportWidth);
    return () => window.removeEventListener("resize", updateViewportWidth);
  }, []);

  const resizeBy = (delta: number) => {
    if (!canResize) return;
    setWidth((current) =>
      Math.min(resizeMax, clampDrawerWidth(Math.min(current, resizeMax) + delta)),
    );
  };

  const startResize = (e: React.PointerEvent) => {
    e.preventDefault();
    const move = (ev: PointerEvent) => {
      const currentMax = Math.min(DRAWER_MAX, window.innerWidth);
      if (currentMax > DRAWER_MIN) {
        setWidth(Math.min(currentMax, clampDrawerWidth(window.innerWidth - ev.clientX)));
      }
    };
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
      setWidth(resizeMax);
    }
  };

  return (
    <Drawer
      opened={opened}
      onClose={onClose}
      position="right"
      size={canResize ? effectiveWidth : "100%"}
      title={title}
      trapFocus
      closeOnEscape
      closeOnClickOutside
      withOverlay
    >
      {canResize && (
        <>
          {/* A focusable separator with value metadata is the WAI-ARIA Window Splitter pattern.
              The generic jsx-a11y rules do not recognize separator as an operable role. */}
          {/* eslint-disable jsx-a11y/no-noninteractive-element-interactions, jsx-a11y/no-noninteractive-tabindex */}
          <div
            role="separator"
            aria-label="Resize panel"
            aria-controls={paneId}
            aria-orientation="vertical"
            aria-valuemin={DRAWER_MIN}
            aria-valuemax={resizeMax}
            aria-valuenow={effectiveWidth}
            aria-valuetext={`${effectiveWidth} pixels`}
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
              disabled={effectiveWidth === DRAWER_MIN}
              onClick={() => resizeBy(-RESIZE_STEP)}
            >
              −
            </ActionIcon>
            <ActionIcon
              variant="subtle"
              size="sm"
              aria-label="Widen panel"
              title="Widen panel"
              disabled={effectiveWidth === resizeMax}
              onClick={() => resizeBy(RESIZE_STEP)}
            >
              +
            </ActionIcon>
          </Group>
        </>
      )}
      <div id={paneId}>{children}</div>
    </Drawer>
  );
}
