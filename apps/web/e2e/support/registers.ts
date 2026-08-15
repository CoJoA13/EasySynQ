import type { Page } from "@playwright/test";

export interface RegisterCase {
  key:
    | "tasks"
    | "audits"
    | "objectives"
    | "management-reviews"
    | "dcrs"
    | "improvement"
    | "risks"
    | "context"
    | "interested-parties"
    | "records";
  path: string;
  floor: number;
  headers: readonly string[];
  finalHeader: string;
  searchPlaceholder: string;
  firstFilter?:
    | {
        role: "radiogroup";
        name?: string;
        firstOptionName: string;
      }
    | {
        role: "textbox";
        name: string;
      };
  primaryAction: {
    role: "link" | "button";
    name: string;
  };
}

export const REGISTER_CASES = [
  {
    key: "tasks",
    path: "/tasks",
    floor: 720,
    headers: ["Subject", "Action", "Stage", "State", "Due"],
    finalHeader: "Due",
    searchPlaceholder: "Search tasks…",
    primaryAction: { role: "link", name: "SOP-PUR-014" },
  },
  {
    key: "audits",
    path: "/audits",
    floor: 800,
    headers: ["Audit", "Title", "Lead auditor", "State", "Started"],
    finalHeader: "Started",
    searchPlaceholder: "Search audits…",
    firstFilter: { role: "radiogroup", firstOptionName: "All" },
    primaryAction: { role: "link", name: "REC-000061" },
  },
  {
    key: "objectives",
    path: "/objectives",
    floor: 720,
    headers: ["Ref", "Objective", "Current / target", "Status", "Due"],
    finalHeader: "Due",
    searchPlaceholder: "Search objectives…",
    firstFilter: {
      role: "radiogroup",
      name: "Filter by RAG status",
      firstOptionName: "All",
    },
    primaryAction: { role: "link", name: "OBJ-001" },
  },
  {
    key: "management-reviews",
    path: "/management-reviews",
    floor: 800,
    headers: ["Ref", "Review", "Period", "Review date", "Status"],
    finalHeader: "Status",
    searchPlaceholder: "Search reviews…",
    primaryAction: { role: "link", name: "MR-001" },
  },
  {
    key: "dcrs",
    path: "/dcrs",
    floor: 1040,
    headers: ["Identifier", "Type", "Significance", "Reason", "Target", "State", "Created"],
    finalHeader: "Created",
    searchPlaceholder: "Search change requests…",
    firstFilter: { role: "textbox", name: "State" },
    primaryAction: { role: "button", name: "DCR-2026-0001" },
  },
  {
    key: "improvement",
    path: "/improvement",
    floor: 920,
    headers: ["Identifier", "Title", "Source", "Owner", "Stage", "Opened"],
    finalHeader: "Opened",
    searchPlaceholder: "Search initiatives…",
    firstFilter: { role: "textbox", name: "Stage" },
    primaryAction: { role: "button", name: "IMP-2026-0001" },
  },
  {
    key: "risks",
    path: "/risks",
    floor: 720,
    headers: ["Type", "Risk / opportunity", "Score", "Band", "Treatment"],
    finalHeader: "Treatment",
    searchPlaceholder: "Search risks…",
    firstFilter: { role: "radiogroup", name: "Filter by band", firstOptionName: "All" },
    primaryAction: { role: "button", name: "Supplier single point of failure" },
  },
  {
    key: "context",
    path: "/context",
    floor: 880,
    headers: ["Issue", "Classification", "Category", "Status", "Last reviewed"],
    finalHeader: "Last reviewed",
    searchPlaceholder: "Search issues…",
    firstFilter: {
      role: "radiogroup",
      name: "Filter by classification",
      firstOptionName: "All",
    },
    primaryAction: { role: "button", name: "Skilled and certified QA team" },
  },
  {
    key: "interested-parties",
    path: "/interested-parties",
    floor: 880,
    headers: ["Party", "Type", "Influence", "Status", "Last reviewed"],
    finalHeader: "Last reviewed",
    searchPlaceholder: "Search parties…",
    firstFilter: { role: "textbox", name: "Filter by party type" },
    primaryAction: { role: "button", name: "Acme Manufacturing" },
  },
  {
    key: "records",
    path: "/records",
    floor: 840,
    headers: ["Identifier", "Title", "Type", "Captured by", "Captured", "State"],
    finalHeader: "State",
    searchPlaceholder: "Search identifier or title…",
    firstFilter: { role: "textbox", name: "Record type" },
    primaryAction: { role: "link", name: "Open record REC-000041" },
  },
] as const satisfies readonly RegisterCase[];

export interface RegisterGeometry {
  documentClientWidth: number;
  documentScrollWidth: number;
  containerClientWidth: number;
  containerScrollWidth: number;
  tableWidth: number;
  searchWidth: number;
  farEdgeInsideAfterScroll: boolean;
}

export interface ActiveElementGeometry {
  inside: boolean;
  containerClientWidth: number;
  containerScrollLeft: number;
  containerScrollWidth: number;
  containerLeft: number;
  containerRight: number;
  activeLeft: number;
  activeRight: number;
}

export interface FocusStyles {
  matchesFocusVisible: boolean;
  outlineStyle: string;
  outlineWidth: string;
  outlineOffset: string;
  boxShadow: string;
}

export async function measureActiveElementWithinRegister(
  page: Page,
): Promise<ActiveElementGeometry> {
  return page.locator("table:visible").evaluate((table) => {
    const active = document.activeElement;
    if (!(active instanceof HTMLElement) || !table.contains(active)) {
      throw new Error("Expected the active element inside the visible register table");
    }

    let container = table.parentElement;
    while (container) {
      const overflowX = getComputedStyle(container).overflowX;
      if (overflowX === "auto" || overflowX === "scroll") break;
      container = container.parentElement;
    }
    if (!container) throw new Error("Expected a localized horizontal scroll owner for the table");

    const containerRect = container.getBoundingClientRect();
    const activeRect = active.getBoundingClientRect();
    return {
      inside:
        activeRect.left >= containerRect.left - 1 && activeRect.right <= containerRect.right + 1,
      containerClientWidth: container.clientWidth,
      containerScrollLeft: container.scrollLeft,
      containerScrollWidth: container.scrollWidth,
      containerLeft: containerRect.left,
      containerRight: containerRect.right,
      activeLeft: activeRect.left,
      activeRight: activeRect.right,
    };
  });
}

export async function readActiveFocusStyles(page: Page): Promise<FocusStyles> {
  return page.locator(":focus").evaluate((active) => {
    const style = getComputedStyle(active);
    return {
      matchesFocusVisible: active.matches(":focus-visible"),
      outlineStyle: style.outlineStyle,
      outlineWidth: style.outlineWidth,
      outlineOffset: style.outlineOffset,
      boxShadow: style.boxShadow,
    };
  });
}

export async function assertRegisterTableStructure(
  page: Page,
  fixtureRowCount: number,
): Promise<void> {
  const visibleTables = page.locator("table:visible");
  const tableCount = await visibleTables.count();
  if (tableCount !== 1) {
    throw new Error(`Expected one structural register table, found ${tableCount}`);
  }

  const rowNavCounts = await visibleTables
    .locator("tbody tr")
    .evaluateAll((rows) => rows.map((row) => row.querySelectorAll("[data-rownav]").length));
  if (
    rowNavCounts.length !== fixtureRowCount ||
    rowNavCounts.some((controlCount) => controlCount !== 1)
  ) {
    throw new Error(
      `Expected ${fixtureRowCount} fixture rows with one row-navigation control each, found ${JSON.stringify(rowNavCounts)}`,
    );
  }
}

export async function measureRegister(
  page: Page,
  registerCase: RegisterCase,
): Promise<RegisterGeometry> {
  const visibleTables = page.locator("table:visible");
  const tableCount = await visibleTables.count();
  if (tableCount !== 1) {
    throw new Error(`Expected one visible table for ${registerCase.key}, found ${tableCount}`);
  }

  return visibleTables.evaluate(
    (table, { finalHeader, searchPlaceholder, key }) => {
      const overflowCandidates: HTMLElement[] = [];
      let ancestor = table.parentElement;
      while (ancestor) {
        const overflowX = getComputedStyle(ancestor).overflowX;
        if (overflowX === "auto" || overflowX === "scroll") overflowCandidates.push(ancestor);
        ancestor = ancestor.parentElement;
      }
      // Mantine's ScrollArea root computes to overflow-x:auto because Table.ScrollContainer's
      // custom-property declaration overrides the root's hidden shorthand at computed-value time.
      // It is only a second owner when it has its own horizontal scroll extent. The nearest computed
      // candidate is the table's designated owner even at desktop widths where no scrolling is needed.
      const overflowOwners = overflowCandidates.filter(
        (candidate, index) => index === 0 || candidate.scrollWidth > candidate.clientWidth + 1,
      );
      if (overflowOwners.length !== 1) {
        const ownerDetails = overflowOwners
          .map((owner) => `${owner.tagName.toLowerCase()}.${owner.className}`)
          .join(", ");
        throw new Error(
          `Expected exactly one localized horizontal overflow container for ${key}, found ${overflowOwners.length}: ${ownerDetails}`,
        );
      }
      const container = overflowOwners[0]!;

      const headers = Array.from(table.querySelectorAll("th"));
      const farEdgeHeaders = headers.filter(
        (header) => header.textContent?.replace(/\s+/g, " ").trim() === finalHeader,
      );
      if (farEdgeHeaders.length !== 1) {
        throw new Error(
          `Expected one ${JSON.stringify(finalHeader)} header for ${key}, found ${farEdgeHeaders.length}`,
        );
      }

      const searchInputs = Array.from(document.querySelectorAll("input")).filter(
        (input) => input.placeholder === searchPlaceholder,
      );
      if (searchInputs.length !== 1) {
        throw new Error(
          `Expected one ${JSON.stringify(searchPlaceholder)} search input for ${key}, found ${searchInputs.length}`,
        );
      }

      container.scrollLeft = container.scrollWidth;
      const containerRect = container.getBoundingClientRect();
      const farEdgeRect = farEdgeHeaders[0]!.getBoundingClientRect();
      const root = document.documentElement;

      return {
        documentClientWidth: root.clientWidth,
        documentScrollWidth: root.scrollWidth,
        containerClientWidth: container.clientWidth,
        containerScrollWidth: container.scrollWidth,
        tableWidth: table.getBoundingClientRect().width,
        searchWidth: searchInputs[0]!.getBoundingClientRect().width,
        farEdgeInsideAfterScroll:
          farEdgeRect.left >= containerRect.left - 1 &&
          farEdgeRect.right <= containerRect.right + 1,
      };
    },
    {
      finalHeader: registerCase.finalHeader,
      searchPlaceholder: registerCase.searchPlaceholder,
      key: registerCase.key,
    },
  );
}
