import { screen } from "@testing-library/react";
import { expect } from "vitest";

export function expectResponsiveTable(minWidth: number): HTMLTableElement {
  const tables = screen.getAllByRole("table");
  expect(tables).toHaveLength(1);
  const table = tables[0]! as HTMLTableElement;
  const owners = [...document.querySelectorAll<HTMLElement>('[style*="--table-min-width"]')].filter(
    (node) => node.contains(table),
  );
  expect(owners).toHaveLength(1);
  expect(owners[0]!.style.getPropertyValue("--table-min-width")).toBe(
    `calc(${minWidth / 16}rem * var(--mantine-scale))`,
  );
  return table;
}
