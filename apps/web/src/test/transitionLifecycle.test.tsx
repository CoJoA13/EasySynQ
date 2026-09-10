import { Button, Transition } from "@mantine/core";
import { act, cleanup, screen } from "@testing-library/react";
import { useState } from "react";
import { expect, test, vi } from "vitest";
import { renderWithProviders } from "./render";

test("a loading change at the first animation frame leaves no callback after test unmount", () => {
  vi.useFakeTimers({
    toFake: ["setTimeout", "clearTimeout", "requestAnimationFrame", "cancelAnimationFrame"],
  });
  let setLoading: (loading: boolean) => void = () => {};
  function DownloadButton() {
    const [loading, updateLoading] = useState(false);
    setLoading = updateLoading;
    return <Button loading={loading}>Download</Button>;
  }
  const unrelated = vi.fn();
  const unrelatedTimer = setTimeout(unrelated, 60_000);
  try {
    renderWithProviders(<DownloadButton />);
    act(() => setLoading(true));
    expect(screen.getByRole("button", { name: "Download" })).toBeDisabled();

    // The pending state update is committed by the first transition frame's flushSync.
    // Running these in separate act calls misses the reentrant scheduling failure.
    act(() => {
      setLoading(false);
      vi.advanceTimersByTime(16);
    });
    act(() => vi.advanceTimersByTime(32));
    expect(screen.getByRole("button", { name: "Download" })).toBeEnabled();
    cleanup();

    const remainingTimers = vi.getTimerCount();
    expect(unrelated).not.toHaveBeenCalled();
    clearTimeout(unrelatedTimer);
    const windowDescriptor = Object.getOwnPropertyDescriptor(globalThis, "window");
    delete (globalThis as { window?: Window }).window;
    try {
      expect(() => vi.runAllTimers()).not.toThrow();
    } finally {
      if (windowDescriptor) Object.defineProperty(globalThis, "window", windowDescriptor);
    }
    expect(remainingTimers).toBe(1);
  } finally {
    cleanup();
    vi.clearAllTimers();
    vi.useRealTimers();
  }
});

test("test transitions retain enter and exit callbacks with visible state changes", () => {
  const calls: string[] = [];
  const transition = (mounted: boolean) => (
    <Transition
      mounted={mounted}
      onEnter={() => calls.push("enter")}
      onEntered={() => calls.push("entered")}
      onExit={() => calls.push("exit")}
      onExited={() => calls.push("exited")}
    >
      {(style) => <div style={style}>Transition content</div>}
    </Transition>
  );
  const view = renderWithProviders(transition(false));
  view.rerender(transition(true));
  expect(screen.getByText("Transition content")).toBeVisible();
  view.rerender(transition(false));
  expect(screen.queryByText("Transition content")).not.toBeInTheDocument();
  expect(calls).toEqual(["enter", "entered", "exit", "exited"]);
});
