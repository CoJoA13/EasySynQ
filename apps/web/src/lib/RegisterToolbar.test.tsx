import { MantineProvider, Table } from "@mantine/core";
import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { RegisterToolbar, SortableTh, SubjectCell } from "./RegisterToolbar";

function wrap(ui: ReactNode) {
  return render(<MantineProvider>{ui}</MantineProvider>);
}

function thWrap(ui: ReactNode) {
  return render(
    <MantineProvider>
      <Table>
        <Table.Thead>
          <Table.Tr>{ui}</Table.Tr>
        </Table.Thead>
      </Table>
    </MantineProvider>,
  );
}

describe("RegisterToolbar", () => {
  it("renders a labelled search box that reports input, plus an optional count", () => {
    const onQ = vi.fn();
    wrap(<RegisterToolbar q="" onQ={onQ} count={3} countNoun="DCRs" />);
    fireEvent.change(screen.getByLabelText("Search"), { target: { value: "abc" } });
    expect(onQ).toHaveBeenCalledWith("abc");
    expect(screen.getByText("3 DCRs")).toBeInTheDocument();
  });
});

describe("SortableTh", () => {
  it("reflects aria-sort=descending when active and fires onSort", () => {
    const onSort = vi.fn();
    thWrap(<SortableTh label="Due" sortKey="due" sort="due" dir="desc" onSort={onSort} />);
    expect(screen.getByRole("columnheader")).toHaveAttribute("aria-sort", "descending");
    fireEvent.click(screen.getByRole("button", { name: "Sort by Due" }));
    expect(onSort).toHaveBeenCalledWith("due");
  });

  it("is aria-sort=none when the column is inactive", () => {
    thWrap(<SortableTh label="Ref" sortKey="ref" sort="due" dir="asc" onSort={() => {}} />);
    expect(screen.getByRole("columnheader")).toHaveAttribute("aria-sort", "none");
  });
});

describe("SubjectCell", () => {
  it("shows the identifier over the title", () => {
    wrap(<SubjectCell identifier="SOP-PUR-014" title="Supplier Re-qualification" />);
    expect(screen.getByText("SOP-PUR-014")).toBeInTheDocument();
    expect(screen.getByText("Supplier Re-qualification")).toBeInTheDocument();
  });

  it("falls back to a calm dash when neither is present", () => {
    wrap(<SubjectCell />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});
