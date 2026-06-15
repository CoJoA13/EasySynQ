import { MantineProvider } from "@mantine/core";
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AsOf } from "./AsOf";

const NOW = Date.parse("2026-06-15T12:00:00Z");

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
});
afterEach(() => {
  vi.useRealTimers();
});

function renderAsOf(at: number | null) {
  return render(
    <MantineProvider>
      <div data-testid="wrap">
        <AsOf at={at} />
      </div>
    </MantineProvider>,
  );
}

it("renders nothing when there is no timestamp", () => {
  // MantineProvider injects <style> tags into the container, so scope the emptiness check to the wrapper.
  renderAsOf(null);
  expect(screen.getByTestId("wrap")).toBeEmptyDOMElement();
});

it("renders the relative label and a timezone-explicit title", () => {
  renderAsOf(NOW - 5 * 60_000);
  const el = screen.getByText(/Updated 5 min ago/);
  expect(el).toBeInTheDocument();
  expect(el).toHaveAttribute("title", expect.stringMatching(/2026/));
});

it("refreshes the relative label on an interval as time passes (Codex #144 regression)", () => {
  renderAsOf(NOW - 60_000); // 1 minute before "now"
  expect(screen.getByText(/Updated 1 min ago/)).toBeInTheDocument();
  // Advance the (fake) clock by 2 minutes — the 30s interval fires and the label re-computes; without
  // the tick it would stay frozen at "1 min ago".
  act(() => {
    vi.advanceTimersByTime(120_000);
  });
  expect(screen.getByText(/Updated 3 min ago/)).toBeInTheDocument();
});
