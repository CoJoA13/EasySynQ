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

// A register page is the top of its own document, so the header always emits the one `h1`. This
// is the assertion that would have to be deleted — not merely edited — to reintroduce
// RES-REGISTER-HEADING-LEVELS, which is why it is stated as a level and not as a tag name.
it("renders the title as the page's h1", () => {
  renderHeader({ title: "Internal audit" });
  expect(screen.getByRole("heading", { level: 1, name: "Internal audit" })).toBeInTheDocument();
});

// `size` is appearance only and must NOT reach the level. A sub-register that looks smaller is
// still the top of its own document — the two CAPA sub-registers are sibling routes under a
// headingless tab strip, so their old `order={3}` never encoded real nesting.
it("keeps a smaller size on the h1 rather than lowering the level", () => {
  renderHeader({ title: "Nonconforming Output (NCR)", size: "h3" });
  const heading = screen.getByRole("heading", { level: 1, name: "Nonconforming Output (NCR)" });
  expect(heading.tagName).toBe("H1");
  expect(screen.queryByRole("heading", { level: 3 })).toBeNull();
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
  const group = screen.getByRole("heading", { level: 1 }).parentElement;
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
