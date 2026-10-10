import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import {
  INFLUENCE_GLYPH,
  INFLUENCE_LABEL,
  PARTY_TYPE_SINGULAR,
  PARTY_TYPE_TONE,
  STATUS_LABEL,
  STATUS_TONE,
} from "../src/features/interested-parties/labels";
import { TONE_GLYPH } from "../src/lib/status";
import type { InterestedParty } from "../src/lib/types";
import { interestedPartyListFixture } from "../src/test/msw/handlers";
import { installRegisterApi } from "./support/api";
import { measureRegister, REGISTER_CASES } from "./support/registers";

// Two more defects from the owner's live walkthrough. Both are layout, so jsdom cannot see either.
//
// The owner reported them as one thing — "headers cut off" — but measurement separated them into
// two mechanisms with two different fixes, and that separation is the point of this file:
//
//   /objectives "Current / target"  lineBoxes: 2   -> the header WRAPS. A sortable label was a
//     breakable <span> in a flex button, so its automatic minimum size was its longest WORD and
//     `table-layout: auto` was free to starve the column and feed the free-text column beside it.
//
//   /context   "Last reviewed"      hiddenRight: 73 -> the header does NOT wrap (lineBoxes: 1).
//     The table is 880px inside an 807px scrollport, so the column sits past the clip edge — and
//     Mantine's ScrollArea hides its scrollbar until hover, so nothing said the table scrolled.
//     The owner read the result as a misspelling ("Last reviewe"), which is precisely the failure
//     mode: content unreachable, with no affordance saying so.
//
// Widths matter. "Current / target" wraps at 1000px and NOT at 1115 or 1280; "Last reviewed" is
// clipped at 1115 and not at 1280. A guard at one comfortable width would prove nothing.

const WRAP_CASES = [
  { key: "objectives", path: "/objectives", header: "Current / target", width: 1000 },
  { key: "context", path: "/context", header: "Last reviewed", width: 1000 },
  { key: "risks", path: "/risks", header: "Risk / opportunity", width: 1000 },
] as const;

for (const { key, path, header, width } of WRAP_CASES) {
  test(`${key} keeps "${header}" on one line at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await installRegisterApi(page, { route: key });
    await page.goto(path);
    await expect(page.getByRole("textbox", { name: "Search" })).toBeVisible();

    const lineBoxes = await page.evaluate((hdr) => {
      const th = Array.from(document.querySelectorAll("th")).find((t) =>
        (t.textContent ?? "").trim().startsWith(hdr),
      );
      if (!th) throw new Error(`no column header starting "${hdr}"`);
      const label = th.querySelector("button > span:first-child") ?? th;
      // A Range's client rects ARE per-line. `element.getClientRects()` is NOT usable here: the
      // label is a flex item, so it is blockified and always reports exactly one rect however
      // many lines it renders — an assertion on it can never fail.
      const range = document.createRange();
      range.selectNodeContents(label);
      return range.getClientRects().length;
    }, header);

    expect(lineBoxes).toBe(1);
  });
}

const SCROLL_CASES = [
  { key: "context", path: "/context", width: 1115 },
  { key: "interested-parties", path: "/interested-parties", width: 1115 },
] as const;

for (const { key, path, width } of SCROLL_CASES) {
  test(`${key} shows a scrollbar when its table overflows at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await installRegisterApi(page, { route: key });
    await page.goto(path);
    await expect(page.getByRole("textbox", { name: "Search" })).toBeVisible();

    const read = () =>
      page.evaluate(() => {
        // Scope to the TABLE's own viewport. A register page has several ScrollAreas (the SWOT
        // board, the party-type board), and grabbing the first one measured a container that never
        // overflows — which the `overflows` assertion below caught rather than passing vacuously.
        const table = document.querySelector("table");
        const port = table?.closest(".mantine-ScrollArea-viewport") as HTMLElement | null;
        if (!port) throw new Error("no scroll viewport around the table");
        const root = port.closest(".mantine-ScrollArea-root");
        // Qualify by ORIENTATION: a root holds BOTH bars, and the vertical one is `display: none`
        // here because nothing overflows vertically, so a bare `.mantine-ScrollArea-scrollbar`
        // could return the hidden one.
        const bar = root?.querySelector(
          '.mantine-ScrollArea-scrollbar[data-orientation="horizontal"]',
        ) as HTMLElement | null;
        return {
          overflows: port.scrollWidth > port.clientWidth,
          // Mantine always sets scrollbar-width:none on the viewport and draws its own bar, so the
          // native computed style reports "none" whether or not a bar is shown. Measure the element.
          barShown: bar ? getComputedStyle(bar).display !== "none" : false,
        };
      });

    // The fixture must actually overflow, or the assertion below is vacuous. Layout has settled by
    // the time the search box is visible, so this one does not need polling.
    expect((await read()).overflows).toBe(true);

    // ...but the bar DOES. `ScrollAreaScrollbarAuto` holds `useState(false)` and renders nothing
    // until one of its two ResizeObservers fires and flips it, so at the instant the search box
    // becomes visible the element may not exist yet — `barShown` reads false on a tree whose
    // behaviour is correct. A single synchronous snapshot therefore reddens CI at random: it took
    // out `interested-parties` on one run and `context` on the next, on identical trees that both
    // passed locally. Poll instead. This stays load-bearing — with the theme's `ScrollArea` entry
    // removed the type reverts to `hover`, the bar never appears unprompted, and the poll times
    // out — which was re-verified after this change, not assumed from before it.
    await expect.poll(async () => (await read()).barShown, { timeout: 5_000 }).toBe(true);
  });
}

interface PartyColumns {
  columns: { left: number; width: number }[];
  tableLeft: number;
  tableWidth: number;
  scrollLeft: number;
}

async function measurePartyColumns(page: Page): Promise<PartyColumns> {
  return page.getByRole("table").evaluate((table) => {
    const port = table.closest(".mantine-ScrollArea-viewport");
    if (!(port instanceof HTMLElement)) throw new Error("Missing party table scrollport");
    // Focusing a sortable header can scroll it into view. Compare at the same offset so that
    // scrolling cannot masquerade as a change to the column allocation.
    port.scrollLeft = 0;
    const tableBox = table.getBoundingClientRect();
    return {
      columns: Array.from(table.querySelectorAll("thead th"), (header) => {
        const box = header.getBoundingClientRect();
        return { left: box.left - tableBox.left, width: box.width };
      }),
      tableLeft: tableBox.left,
      tableWidth: tableBox.width,
      scrollLeft: port.scrollLeft,
    };
  });
}

async function applyPartyRootFontSize(page: Page, rootFontSize: number) {
  const read = () =>
    page.getByRole("table").evaluate((table) => {
      const text = table.querySelector("tbody tr td:last-child .mantine-Text-root");
      const badge = table.querySelector("tbody .mantine-Badge-root");
      if (!text || !badge || !table.parentElement) throw new Error("Missing party font samples");
      return {
        rootFontSize: parseFloat(getComputedStyle(document.documentElement).fontSize),
        textFontSize: parseFloat(getComputedStyle(text).fontSize),
        badgeFontSize: parseFloat(getComputedStyle(badge).fontSize),
        tableFloor: parseFloat(getComputedStyle(table.parentElement).minWidth),
        tableWidth: table.getBoundingClientRect().width,
      };
    });
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  const before = await read();
  expect(before.rootFontSize).toBe(16);
  expect(before.textFontSize).toBe(14);
  await page.evaluate((size) => {
    document.documentElement.style.fontSize = `${size}px`;
  }, rootFontSize);
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  const after = await read();
  expect(after.rootFontSize).toBe(rootFontSize);
  expect(after.textFontSize).toBe(rootFontSize === 20 ? 17.5 : 14);
  expect(after.tableFloor).toBe(rootFontSize === 20 ? 1100 : 880);
  expect(after.tableWidth).toBeGreaterThanOrEqual(after.tableFloor - 1);
  if (rootFontSize === 20) {
    expect(after.badgeFontSize).toBeGreaterThan(before.badgeFontSize);
    expect(after.badgeFontSize).toBeCloseTo(before.badgeFontSize * 1.25, 2);
  }
  return { before, after };
}

const PARTY_FONT_CASES = [
  ...[320, 1000, 1115, 1280].map((width) => ({ width, rootFontSize: 16 })),
  { width: 320, rootFontSize: 20 },
  { width: 1280, rootFontSize: 20 },
];

for (const { width, rootFontSize } of PARTY_FONT_CASES) {
  const fontDescription = rootFontSize === 20 ? " with a 20px root" : "";
  test(`interested-parties keeps column boundaries through filtering at ${width}px${fontDescription}`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await installRegisterApi(page, { route: "interested-parties" });
    await page.goto("/interested-parties");
    const table = page.getByRole("table");
    const rows = table.locator("tbody tr");
    await expect(rows).toHaveCount(6);
    await testInfo.attach("party-font-measurements", {
      body: JSON.stringify(await applyPartyRootFontSize(page, rootFontSize), null, 2),
      contentType: "application/json",
    });
    await expect(table.getByRole("columnheader")).toHaveText([
      "Party",
      "Type",
      "Influence",
      "Status",
      "Last reviewed",
    ]);

    const snapshots: { state: string; geometry: PartyColumns }[] = [];
    const capture = async (state: string, names: readonly string[]) => {
      await expect(rows).toHaveCount(names.length);
      for (const name of names) {
        await expect(table.getByRole("button", { name, exact: true })).toHaveCount(1);
      }
      snapshots.push({ state, geometry: await measurePartyColumns(page) });
    };
    const allNames = [
      "Acme Manufacturing",
      "Beta Retail Group",
      "National accreditation body",
      "Steel & Alloys Co",
      "Regional logistics partner",
      "Former distributor (legacy)",
    ];
    const influence = page.getByRole("radiogroup", { name: "Filter by influence", exact: true });
    const status = page.getByRole("radiogroup", { name: "Filter by status", exact: true });
    const partyType = page.getByRole("textbox", { name: "Filter by party type", exact: true });
    const search = page.getByRole("textbox", { name: "Search", exact: true });
    const expectParam = (key: string, value: string | null) =>
      expect.poll(() => new URL(page.url()).searchParams.get(key)).toBe(value);

    await capture("unfiltered", allNames);
    if ([320, 1115, 1280].includes(width)) {
      await testInfo.attach("unfiltered", {
        body: await page.screenshot({ fullPage: true }),
        contentType: "image/png",
      });
    }
    await influence.getByText("Low", { exact: true }).click();
    await expectParam("influence", "low");
    await capture("low influence", ["Steel & Alloys Co", "Former distributor (legacy)"]);
    if ([320, 1115, 1280].includes(width)) {
      await testInfo.attach("low-influence", {
        body: await page.screenshot({ fullPage: true }),
        contentType: "image/png",
      });
    }
    await influence.getByText("Unspecified", { exact: true }).click();
    await expectParam("influence", "unspecified");
    await capture("unspecified influence", ["Regional logistics partner"]);
    await influence.getByText("All", { exact: true }).click();
    await expectParam("influence", null);
    await capture("restored influence", allNames);

    await partyType.click();
    await page.getByRole("option", { name: "Partner", exact: true }).click();
    await expectParam("party_type", "partner");
    await capture("partner type", ["Regional logistics partner"]);
    // Mantine Select permits deselection by choosing its current option again.
    await partyType.click();
    await page.getByRole("option", { name: "Partner", exact: true }).click();
    await expectParam("party_type", null);
    await capture("restored party type", allNames);

    await status.getByText("Closed", { exact: true }).click();
    await expectParam("status", "closed");
    await capture("closed status", ["Former distributor (legacy)"]);
    await status.getByText("All", { exact: true }).click();
    await expectParam("status", null);
    await capture("restored status", allNames);

    await search.fill("Acme Manufacturing");
    await expectParam("q", "Acme Manufacturing");
    await capture("name search", ["Acme Manufacturing"]);
    await search.fill("No synthetic party has this name");
    await expectParam("q", "No synthetic party has this name");
    await expect(page.getByText("No parties match your filters.", { exact: true })).toBeVisible();
    await expect(table).toHaveCount(0);
    await search.fill("");
    await expectParam("q", null);
    await capture("restored search", allNames);

    const reviewedHeader = table.getByRole("columnheader", { name: "Last reviewed" });
    const sortReviewed = reviewedHeader.getByRole("button", { name: "Sort by Last reviewed" });
    await sortReviewed.click();
    await expect(reviewedHeader).toHaveAttribute("aria-sort", "ascending");
    await capture("reviewed ascending", allNames);
    await sortReviewed.click();
    await expect(reviewedHeader).toHaveAttribute("aria-sort", "descending");
    await capture("reviewed descending", allNames);

    // Save every state before asserting: a baseline RED must still explain all observed movement.
    await testInfo.attach("party-column-measurements", {
      body: JSON.stringify({ width, rootFontSize, snapshots }, null, 2),
      contentType: "application/json",
    });
    const before = snapshots[0]!.geometry;
    expect(before.columns).toHaveLength(5);
    for (const { state, geometry: after } of snapshots.slice(1)) {
      expect(after.columns, state).toHaveLength(5);
      expect(after.scrollLeft, state).toBe(before.scrollLeft);
      // Name the ledger's decisive final-column edge separately in failure output.
      expect(
        Math.abs(after.columns[4]!.left - before.columns[4]!.left),
        `${state}: Last reviewed`,
      ).toBeLessThanOrEqual(1);
      for (let index = 0; index < 5; index++) {
        expect(
          Math.abs(after.columns[index]!.left - before.columns[index]!.left),
          `${state}: column ${index} left`,
        ).toBeLessThanOrEqual(1);
        expect(
          Math.abs(after.columns[index]!.width - before.columns[index]!.width),
          `${state}: column ${index} width`,
        ).toBeLessThanOrEqual(1);
      }
      expect(
        Math.abs(after.tableLeft - before.tableLeft),
        `${state}: table left`,
      ).toBeLessThanOrEqual(1);
      expect(
        Math.abs(after.tableWidth - before.tableWidth),
        `${state}: table width`,
      ).toBeLessThanOrEqual(1);
    }
  });
}

async function expectPartyContentContained(page: Page): Promise<void> {
  const violations = await page.getByRole("table").evaluate((table) => {
    const failures: string[] = [];
    function inside(element: Element, cell: Element, description: string) {
      const box = element.getBoundingClientRect();
      const cellBox = cell.getBoundingClientRect();
      const style = getComputedStyle(cell);
      const left = cellBox.left + parseFloat(style.paddingLeft);
      const right = cellBox.right - parseFloat(style.paddingRight);
      if (box.left < left - 1 || box.right > right + 1) {
        failures.push(
          `${description}: bounds ${box.left}..${box.right} escape cell ${left}..${right}`,
        );
      }
    }
    function wholeText(element: Element, description: string) {
      const box = element.getBoundingClientRect();
      const range = document.createRange();
      range.selectNodeContents(element);
      const rects = Array.from(range.getClientRects());
      // These elements contain text directly. Range rects therefore describe real rendered lines,
      // unlike a block/flex element's own getClientRects(), which always returns its single box.
      if (rects.length !== 1)
        failures.push(`${description}: expected one text line, got ${rects.length}`);
      for (const rect of rects) {
        if (rect.left < box.left - 1 || rect.right > box.right + 1) {
          failures.push(
            `${description}: text ${rect.left}..${rect.right} escapes label ${box.left}..${box.right}`,
          );
        }
      }
    }
    for (const header of table.querySelectorAll("thead th")) {
      const button = header.querySelector("button");
      if (!button) continue;
      inside(button, header, `header ${header.textContent}`);
      const label = button.querySelector("span:first-child");
      const icon = button.querySelector("svg");
      if (!label || !icon) throw new Error("Missing sortable header label/icon");
      inside(label, header, `header label ${label.textContent}`);
      inside(icon, header, `header icon ${label.textContent}`);
      wholeText(label, `header label ${label.textContent}`);
    }
    for (const [rowIndex, row] of Array.from(table.querySelectorAll("tbody tr")).entries()) {
      const cells = row.querySelectorAll("td");
      if (cells.length !== 5) throw new Error("Expected five party cells");
      const name = cells[0]!.querySelector("button");
      const partyTexts = cells[0]!.querySelectorAll(
        "button > .mantine-Text-root, button + .mantine-Text-root",
      );
      if (!name || partyTexts.length !== 2) throw new Error("Missing party name/expectations");
      inside(name, cells[0]!, `row ${rowIndex} Party button`);
      for (const text of partyTexts) {
        inside(text, cells[0]!, `row ${rowIndex} Party text box`);
        const style = getComputedStyle(text);
        if (style.webkitLineClamp !== "1" || style.overflow !== "hidden") {
          failures.push(`row ${rowIndex} Party text: expected a clipped single line`);
        }
        if (text.getBoundingClientRect().height > parseFloat(style.lineHeight) + 1) {
          failures.push(`row ${rowIndex} Party text box: more than one line high`);
        }
      }
      for (let column = 1; column < 5; column++) {
        const cell = cells[column]!;
        const badge = cell.querySelector(".mantine-Badge-root");
        if (badge) {
          const label = badge.querySelector(".mantine-Badge-label");
          const glyph = badge.querySelector(".mantine-Badge-section span");
          if (!label || !glyph) throw new Error("Missing badge label/glyph");
          inside(badge, cell, `row ${rowIndex} column ${column} badge`);
          inside(label, cell, `row ${rowIndex} column ${column} label`);
          inside(glyph, cell, `row ${rowIndex} column ${column} glyph`);
          wholeText(label, `row ${rowIndex} column ${column} ${label.textContent}`);
          wholeText(glyph, `row ${rowIndex} column ${column} glyph`);
        } else {
          const text = cell.firstElementChild;
          if (!text) throw new Error("Missing compact cell text");
          inside(text, cell, `row ${rowIndex} column ${column} text box`);
          wholeText(text, `row ${rowIndex} column ${column} ${text.textContent}`);
        }
      }
    }
    return failures;
  });
  expect(violations, "party content must remain readable inside its own column").toEqual([]);
}

const PARTY_TYPES = Object.keys(PARTY_TYPE_SINGULAR) as InterestedParty["party_type"][];
const PARTY_INFLUENCES = Object.keys(INFLUENCE_LABEL) as NonNullable<
  InterestedParty["influence"]
>[];
const PARTY_STATUSES = Object.keys(STATUS_LABEL) as InterestedParty["status"][];
const inventoryRows: InterestedParty[] = PARTY_TYPES.map((party_type, index) => ({
  ...interestedPartyListFixture.data[0]!,
  id: `ee559000-0000-0000-0000-${String(index + 1).padStart(12, "0")}`,
  party_type,
  party_name:
    index === 1
      ? "Synthetic party ".repeat(267).slice(0, 4000)
      : index === 2
        ? "P".repeat(4000)
        : index === 0
          ? "Acme Manufacturing"
          : `Synthetic ${party_type} party`,
  needs_expectations:
    index === 1
      ? "R".repeat(4000)
      : index === 2
        ? "Synthetic requirement ".repeat(191).slice(0, 4000)
        : "Synthetic requirement.",
  influence:
    index % (PARTY_INFLUENCES.length + 1) === PARTY_INFLUENCES.length
      ? null
      : PARTY_INFLUENCES[index % (PARTY_INFLUENCES.length + 1)]!,
  status: PARTY_STATUSES[index % PARTY_STATUSES.length]!,
  last_reviewed_at: index % 2 === 0 ? "2026-06-01T00:00:00+00:00" : null,
}));

async function addPartyBadgeGap(page: Page) {
  const measurements = await page.getByRole("table").evaluate((table) => {
    const rootFontSize = parseFloat(getComputedStyle(document.documentElement).fontSize);
    const badges = Array.from(table.querySelectorAll("tbody .mantine-Badge-root"));
    const samples = badges.map((badge) => {
      const section = badge.querySelector('.mantine-Badge-section[data-position="left"]');
      if (!(section instanceof HTMLElement)) throw new Error("Missing left badge section");
      const read = () => {
        const box = badge.getBoundingClientRect();
        return {
          marginRight: parseFloat(getComputedStyle(section).marginRight),
          left: box.left,
          right: box.right,
          width: box.width,
        };
      };
      const before = read();
      // CI badges measured 2.6875px wider than this host. Exercise a larger 4px allowance
      // without assuming which font caused that difference or changing the label/glyph.
      // Apply the computed margin to the section itself: Mantine scopes its gap variable there.
      section.style.marginRight = `calc(${before.marginRight}px + 0.25rem)`;
      return { label: badge.getAttribute("aria-label"), before, after: read() };
    });
    return { rootFontSize, samples };
  });
  expect(measurements.rootFontSize).toBe(16);
  expect(measurements.samples.length).toBeGreaterThan(0);
  for (const { label, before, after } of measurements.samples) {
    expect(after.marginRight - before.marginRight, `${label}: added section gap`).toBeCloseTo(4, 2);
    expect(after.width - before.width, `${label}: added badge width`).toBeCloseTo(4, 2);
  }
  return measurements;
}

const PARTY_INVENTORY_CASES = [
  ...PARTY_FONT_CASES.map((scenario) => ({ ...scenario, extraBadgeGap: false })),
  { width: 320, rootFontSize: 16, extraBadgeGap: true },
  { width: 1280, rootFontSize: 16, extraBadgeGap: true },
];

for (const { width, rootFontSize, extraBadgeGap } of PARTY_INVENTORY_CASES) {
  const scenarioDescription = extraBadgeGap
    ? " with an extra badge gap"
    : rootFontSize === 20
      ? " with a 20px root"
      : "";
  test(`interested-parties keeps all current labels inside their columns at ${width}px${scenarioDescription}`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await installRegisterApi(page, { route: "interested-parties" });
    await page.route("**/api/v1/interested-parties", async (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      await route.fulfill({ json: { data: inventoryRows } });
    });
    await page.goto("/interested-parties");
    const table = page.getByRole("table");
    await expect(table.locator("tbody tr")).toHaveCount(inventoryRows.length);
    await testInfo.attach("party-font-measurements", {
      body: JSON.stringify(await applyPartyRootFontSize(page, rootFontSize), null, 2),
      contentType: "application/json",
    });
    if (extraBadgeGap) {
      await testInfo.attach("badge-gap-measurements", {
        body: JSON.stringify(await addPartyBadgeGap(page), null, 2),
        contentType: "application/json",
      });
    }
    expect(new Set(inventoryRows.map((row) => row.party_type))).toEqual(new Set(PARTY_TYPES));
    expect(new Set(inventoryRows.map((row) => row.influence))).toEqual(
      new Set([...PARTY_INFLUENCES, null]),
    );
    expect(new Set(inventoryRows.map((row) => row.status))).toEqual(new Set(PARTY_STATUSES));
    for (const row of inventoryRows) {
      const rendered = table.locator("tbody tr").filter({
        has: page.getByRole("button", { name: row.party_name, exact: true }),
      });
      await expect(rendered).toHaveCount(1);
      const cells = rendered.locator("td");
      const type = cells.nth(1).locator(".mantine-Badge-root");
      await expect(type).toHaveAttribute(
        "aria-label",
        `Party type: ${PARTY_TYPE_SINGULAR[row.party_type]}`,
      );
      await expect(type.locator(".mantine-Badge-label")).toHaveText(
        PARTY_TYPE_SINGULAR[row.party_type],
      );
      await expect(type.locator(".mantine-Badge-section")).toHaveText(
        TONE_GLYPH[PARTY_TYPE_TONE[row.party_type]],
      );
      if (row.influence) {
        const influence = cells.nth(2).locator(".mantine-Badge-root");
        await expect(influence).toHaveAttribute(
          "aria-label",
          `Influence: ${INFLUENCE_LABEL[row.influence]}`,
        );
        await expect(influence.locator(".mantine-Badge-label")).toHaveText(
          INFLUENCE_LABEL[row.influence],
        );
        await expect(influence.locator(".mantine-Badge-section")).toHaveText(
          INFLUENCE_GLYPH[row.influence],
        );
      } else {
        await expect(cells.nth(2)).toHaveText("Unspecified");
      }
      const status = cells.nth(3).locator(".mantine-Badge-root");
      await expect(status).toHaveAttribute("aria-label", `Status: ${STATUS_LABEL[row.status]}`);
      await expect(status.locator(".mantine-Badge-label")).toHaveText(STATUS_LABEL[row.status]);
      await expect(status.locator(".mantine-Badge-section")).toHaveText(
        TONE_GLYPH[STATUS_TONE[row.status]],
      );
      await expect(cells.nth(4)).toHaveText(row.last_reviewed_at ? "2026-06-01" : "Never");
    }
    // Record actual column allocations for final review, even if a mutation fails.
    await testInfo.attach("label-column-measurements", {
      body: JSON.stringify(await measurePartyColumns(page), null, 2),
      contentType: "application/json",
    });
    await testInfo.attach("complete-label-inventory", {
      body: await page.screenshot({ fullPage: true }),
      contentType: "image/png",
    });
    await expectPartyContentContained(page);

    const shortName = table.getByRole("button", { name: "Acme Manufacturing", exact: true });
    expect(
      await shortName.evaluate((button) => {
        const label = button.firstElementChild!;
        const range = document.createRange();
        range.selectNodeContents(label);
        return range.getBoundingClientRect().width <= label.getBoundingClientRect().width + 1;
      }),
      "short Party name remains whole at the table floor",
    ).toBe(true);

    const register = REGISTER_CASES.find((candidate) => candidate.key === "interested-parties")!;
    const geometry = await measureRegister(page, register);
    expect(geometry.documentScrollWidth - geometry.documentClientWidth).toBeLessThanOrEqual(1);
    expect(geometry.tableWidth).toBeGreaterThanOrEqual(rootFontSize === 20 ? 1099 : 879);
    expect(geometry.farEdgeInsideAfterScroll).toBe(true);
    if (width <= 1115) {
      expect(geometry.containerScrollWidth).toBeGreaterThan(geometry.containerClientWidth);
    }
    if (geometry.containerScrollWidth > geometry.containerClientWidth) {
      await expect
        .poll(() =>
          table.evaluate((element) => {
            const port = element.closest(".mantine-ScrollArea-viewport")!;
            const root = port.closest(".mantine-ScrollArea-root")!;
            const bar = root.querySelector(
              '.mantine-ScrollArea-scrollbar[data-orientation="horizontal"]',
            );
            return bar !== null && getComputedStyle(bar).display !== "none";
          }),
        )
        .toBe(true);
    }
    // The far edge is already scrolled into view by measureRegister. Capture the TABLE's viewport
    // itself so the board above cannot crowd every row out of the evidence screenshot.
    await testInfo.attach("table-final-column", {
      body: await table
        .locator("xpath=ancestor::*[contains(@class,'mantine-ScrollArea-root')][1]")
        .screenshot(),
      contentType: "image/png",
    });
  });
}
