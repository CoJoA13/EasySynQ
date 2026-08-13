import { MantineProvider, SegmentedControl, Table } from "@mantine/core";
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
    const count = screen.getByText("3 DCRs");
    expect(count).toBeInTheDocument();
    expect(count).toHaveAttribute("aria-live", "polite");
  });

  it("keeps one ordered search and filter tree inside the narrow toolbar", () => {
    wrap(
      <RegisterToolbar q="" onQ={() => undefined} count={3} countNoun="items">
        <SegmentedControl
          aria-label="Filter by state"
          value="all"
          onChange={() => undefined}
          data={[
            { value: "all", label: "All" },
            { value: "active", label: "Active" },
          ]}
        />
      </RegisterToolbar>,
    );

    const search = screen.getByRole("textbox", { name: "Search" });
    const searchRoot = search.closest<HTMLElement>(".mantine-TextInput-root");
    expect(searchRoot).not.toBeNull();
    expect(searchRoot).toHaveStyle({ minWidth: "0rem" });
    const responsiveClass = [...searchRoot!.classList].find((name) => name.startsWith("__m__"));
    expect(responsiveClass).toBeDefined();
    const inlineRules = [...document.querySelectorAll('style[data-mantine-styles="inline"]')]
      .map((style) => style.textContent ?? "")
      .join("\n");
    expect(inlineRules).toContain(`.${responsiveClass}{width:100%;}`);
    expect(inlineRules).toContain("@media(min-width: 48em)");
    expect(inlineRules).toContain("width:calc(16.25rem * var(--mantine-scale))");

    const filter = screen.getByRole("radio", { name: "All" });
    const filterLane = filter.closest<HTMLElement>('[style*="overflow-x"]');
    expect(filterLane).not.toBeNull();
    expect(filterLane).toHaveStyle({
      overflowX: "auto",
      minWidth: "0rem",
      maxWidth: "100%",
    });
    expect(search.compareDocumentPosition(filter) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getAllByRole("textbox", { name: "Search" })).toHaveLength(1);
    expect(screen.getAllByRole("radio")).toHaveLength(2);
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
