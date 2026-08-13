import { MantineProvider, Table } from "@mantine/core";
import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useRowKeyboardNav } from "./useRowKeyboardNav";

// Harness mirroring the real usage: the hook attaches to the Mantine Table.Tbody; each row's
// primary focusable element carries data-rownav. Arrow keys move real DOM focus between rows.
function Demo({
  onPrimary = () => undefined,
  onEdit = () => undefined,
}: {
  onPrimary?: (id: string) => void;
  onEdit?: (id: string) => void;
}) {
  const nav = useRowKeyboardNav<HTMLTableSectionElement>();
  return (
    <MantineProvider>
      <Table>
        <Table.Tbody ref={nav.ref} onKeyDown={nav.onKeyDown}>
          {["r1", "r2", "r3"].map((id) => (
            <Table.Tr key={id}>
              <Table.Td>
                <button type="button" data-rownav data-testid={id} onClick={() => onPrimary(id)}>
                  {id}
                </button>
              </Table.Td>
              <Table.Td>
                <button type="button" data-testid={`edit-${id}`} onClick={() => onEdit(id)}>
                  Edit {id}
                </button>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </MantineProvider>
  );
}

describe("useRowKeyboardNav", () => {
  it("ArrowDown / ArrowUp move focus between rows, clamped at the ends", () => {
    const { getByTestId } = render(<Demo />);
    const r1 = getByTestId("r1");
    const r2 = getByTestId("r2");
    const r3 = getByTestId("r3");
    r1.focus();
    fireEvent.keyDown(r1, { key: "ArrowDown" });
    expect(document.activeElement).toBe(r2);
    fireEvent.keyDown(r2, { key: "ArrowDown" });
    expect(document.activeElement).toBe(r3);
    fireEvent.keyDown(r3, { key: "ArrowDown" }); // clamp at the last row
    expect(document.activeElement).toBe(r3);
    fireEvent.keyDown(r3, { key: "ArrowUp" });
    expect(document.activeElement).toBe(r2);
  });

  it("ignores keys other than the arrows", () => {
    const { getByTestId } = render(<Demo />);
    const r1 = getByTestId("r1");
    r1.focus();
    fireEvent.keyDown(r1, { key: "Enter" });
    expect(document.activeElement).toBe(r1);
  });

  it("moves focus without activating a primary control", () => {
    const onPrimary = vi.fn();
    const { getByTestId } = render(<Demo onPrimary={onPrimary} />);
    const r1 = getByTestId("r1");
    const r2 = getByTestId("r2");
    r1.focus();
    fireEvent.keyDown(r1, { key: "ArrowDown" });
    expect(r2).toHaveFocus();
    expect(onPrimary).not.toHaveBeenCalled();
  });

  it("does not intercept arrows from an independent unmarked action", () => {
    const { getByTestId } = render(<Demo />);
    const edit = getByTestId("edit-r2");
    edit.focus();
    const defaultAllowed = fireEvent.keyDown(edit, { key: "ArrowDown" });
    expect(defaultAllowed).toBe(true);
    expect(edit).toHaveFocus();
  });
});
