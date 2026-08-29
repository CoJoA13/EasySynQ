import { MantineProvider, Button } from "@mantine/core";
import { render, screen, within } from "@testing-library/react";
import { expect, it, vi, beforeEach, afterEach } from "vitest";
import { RegisterPageHeader } from "./RegisterPageHeader";

const NOW = Date.parse("2026-06-15T12:00:00Z");

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
});
afterEach(() => {
  vi.useRealTimers();
});

function renderHeader(props: Parameters<typeof RegisterPageHeader>[0]) {
  return render(
    <MantineProvider>
      <div data-testid="wrap">
        <RegisterPageHeader {...props} />
      </div>
    </MantineProvider>,
  );
}

it("renders the title at level 2 by default", () => {
  renderHeader({ title: "Internal audit" });
  expect(screen.getByRole("heading", { level: 2, name: "Internal audit" })).toBeInTheDocument();
});

it("honours an explicit level so a sub-register keeps its own depth", () => {
  renderHeader({ title: "Nonconforming Output (NCR)", order: 3 });
  expect(
    screen.getByRole("heading", { level: 3, name: "Nonconforming Output (NCR)" }),
  ).toBeInTheDocument();
});

it("renders the action a caller supplies", () => {
  renderHeader({ title: "Internal audit", actions: <Button>New audit</Button> });
  expect(screen.getByRole("button", { name: "New audit" })).toBeInTheDocument();
});

// The load-bearing gating case. Callers pass `can("audit.create") && <Button/>`, which is `false`
// for an ungranted reader — NOT undefined. The header must render no button and no wrapper element
// standing in for one, so a denied reader sees exactly the title, as they did before this component
// existed. Asserting the DOM child count is what makes "no empty box" mechanical: a `<div/>` or
// `<span/>` wrapper around a falsy action would pass a queryByRole check and fail this one.
it("renders no action element at all when the permission gate is false", () => {
  const gate = false;
  renderHeader({ title: "Internal audit", actions: gate && <Button>New audit</Button> });
  expect(screen.queryByRole("button")).toBeNull();
  const group = screen.getByRole("heading", { level: 2 }).parentElement;
  expect(group?.children).toHaveLength(1);
});

it("renders the freshness stamp when given one", () => {
  renderHeader({ title: "Internal audit", updatedAt: NOW - 5 * 60_000 });
  expect(within(screen.getByTestId("wrap")).getByText(/Updated/)).toBeInTheDocument();
});

// AsOf already returns null for a falsy stamp; this pins that the header does not add chrome of its
// own around it, so a page with no query stamp (the audit program's plans header) renders clean.
it("renders no freshness stamp when none is supplied", () => {
  renderHeader({ title: "Audit program" });
  expect(within(screen.getByTestId("wrap")).queryByText(/Updated/)).toBeNull();
});
