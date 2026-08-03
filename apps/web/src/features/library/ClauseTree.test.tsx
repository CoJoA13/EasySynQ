import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import { renderWithProviders } from "../../test/render";
import { ClauseTree } from "./ClauseTree";

test("renders top-level clauses only while nothing is selected (~12-row spine)", async () => {
  renderWithProviders(<ClauseTree selected={undefined} onSelect={() => {}} />);
  expect(await screen.findByRole("button", { name: /8 Operation/ })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /8\.4/ })).not.toBeInTheDocument();
});

test("the selected top-level clause expands its direct sub-clauses", async () => {
  renderWithProviders(<ClauseTree selected="8" onSelect={() => {}} />);
  expect(
    await screen.findByRole("button", { name: /8\.4 Control of external providers/ }),
  ).toBeInTheDocument();
});

test("a selected SUB-clause keeps its parent subtree expanded and pressed (deep-link)", async () => {
  renderWithProviders(<ClauseTree selected="8.4" onSelect={() => {}} />);
  const sub = await screen.findByRole("button", { name: /8\.4 Control of external providers/ });
  expect(sub).toHaveAttribute("aria-pressed", "true");
});

test("clicking a top-level clause selects it (expansion follows selection)", async () => {
  const onSelect = vi.fn();
  renderWithProviders(<ClauseTree selected={undefined} onSelect={onSelect} />);
  await userEvent.click(await screen.findByRole("button", { name: /8 Operation/ }));
  expect(onSelect).toHaveBeenCalledWith("8");
});

test("rows auto-grow for wrapped titles instead of overflowing (owner: no truncation)", async () => {
  renderWithProviders(<ClauseTree selected={undefined} onSelect={() => {}} />);
  const btn = await screen.findByRole("button", { name: /8 Operation/ });
  // Inspect the INLINE style: jsdom never applies Mantine's stylesheet, so a computed-style
  // assertion would false-PASS against the old fixed-height compact-sm button.
  expect(btn.style.height).toBe("auto");
  expect(btn.style.minHeight).toBe("var(--button-height-compact-sm)");
});
