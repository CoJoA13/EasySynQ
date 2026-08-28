import { expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MantineProvider } from "@mantine/core";

import { RegisterFilterBar } from "./RegisterFilterBar";

function wrap(ui: React.ReactNode) {
  return render(<MantineProvider>{ui}</MantineProvider>);
}

it("reports a picked date through onChange", async () => {
  const onChange = vi.fn();
  wrap(<RegisterFilterBar value={{}} onChange={onChange} />);
  await userEvent.type(screen.getByLabelText("Created from"), "2026-03-01");
  expect(onChange).toHaveBeenCalled();
  expect(onChange.mock.calls.at(-1)?.[0]).toMatchObject({ createdFrom: "2026-03-01" });
});

it("offers a clear control only when something is selected", () => {
  const { rerender } = wrap(<RegisterFilterBar value={{}} onChange={vi.fn()} />);
  expect(screen.queryByRole("button", { name: /clear filters/i })).toBeNull();
  rerender(
    <MantineProvider>
      <RegisterFilterBar value={{ createdFrom: "2026-03-01" }} onChange={vi.fn()} />
    </MantineProvider>,
  );
  expect(screen.getByRole("button", { name: /clear filters/i })).toBeInTheDocument();
});

it("clears both bounds at once", async () => {
  const onChange = vi.fn();
  wrap(
    <RegisterFilterBar
      value={{ createdFrom: "2026-03-01", createdTo: "2026-04-01" }}
      onChange={onChange}
    />,
  );
  await userEvent.click(screen.getByRole("button", { name: /clear filters/i }));
  expect(onChange).toHaveBeenCalledWith({});
});
