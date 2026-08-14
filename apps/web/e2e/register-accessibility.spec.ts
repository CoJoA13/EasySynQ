import { expect, test } from "@playwright/test";
import { installRegisterApi } from "./support/api";
import {
  assertRegisterTableStructure,
  measureActiveElementWithinRegister,
  readActiveFocusStyles,
} from "./support/registers";

test("keeps keyboard focus visible on the far-edge DCR sort control", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await installRegisterApi(page, { route: "dcrs" });
  await page.goto("/dcrs");

  const stateSort = page.getByRole("button", { name: "Sort by State", exact: true });
  const createdSort = page.getByRole("button", { name: "Sort by Created", exact: true });
  await stateSort.focus();
  await page.keyboard.press("Tab");

  await expect(createdSort).toBeFocused();
  const focusStyles = await readActiveFocusStyles(page);
  expect(focusStyles.matchesFocusVisible).toBe(true);
  expect(focusStyles.boxShadow).not.toBe("none");
  const geometry = await measureActiveElementWithinRegister(page);
  expect(geometry.inside, JSON.stringify(geometry)).toBe(true);
});

test("keeps the far-edge DCR focus treatment visible in forced colors", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await page.emulateMedia({ forcedColors: "active" });
  await installRegisterApi(page, { route: "dcrs" });
  await page.goto("/dcrs");

  await page.getByRole("button", { name: "Sort by State", exact: true }).focus();
  await page.keyboard.press("Tab");
  const createdSort = page.getByRole("button", { name: "Sort by Created", exact: true });

  await expect(createdSort).toBeFocused();
  expect(await readActiveFocusStyles(page)).toEqual({
    matchesFocusVisible: true,
    outlineStyle: "solid",
    outlineWidth: "2px",
    outlineOffset: "2px",
    boxShadow: "none",
  });
});

test("preserves native Task links, row navigation, and table semantics", async ({ page }) => {
  await installRegisterApi(page, { route: "tasks" });
  await page.goto("/tasks");

  const table = page.getByRole("table", { name: "My tasks" });
  const subjectLinks = table.locator("tbody a[data-rownav]");
  await expect(table).toBeVisible();
  await assertRegisterTableStructure(page, 2);
  await expect(subjectLinks).toHaveCount(2);
  await expect(subjectLinks.first()).toHaveAttribute("href", /\/tasks\//);
  await expect(subjectLinks.nth(1)).toHaveAttribute("href", /\/tasks\//);
  expect(await subjectLinks.evaluateAll((links) => links.map((link) => link.tagName))).toEqual([
    "A",
    "A",
  ]);
  expect(await subjectLinks.evaluateAll((links) => links.map((link) => link.tabIndex))).toEqual([
    0, 0,
  ]);

  await table.getByRole("button", { name: "Sort by Due", exact: true }).focus();
  await page.keyboard.press("Tab");
  await expect(subjectLinks.first()).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(subjectLinks.nth(1)).toBeFocused();

  await subjectLinks.first().focus();
  await page.keyboard.press("ArrowDown");
  await expect(subjectLinks.nth(1)).toBeFocused();

  const rows = table.locator("tbody tr");
  await expect(rows).toHaveCount(2);
  expect(
    await rows.evaluateAll((elements) =>
      elements.map((row) => ({
        role: row.getAttribute("role"),
        tabIndex: row.getAttribute("tabindex"),
      })),
    ),
  ).toEqual([
    { role: null, tabIndex: null },
    { role: null, tabIndex: null },
  ]);

  await expect(table).toMatchAriaSnapshot(`
    - table "My tasks":
      - rowgroup:
        - row:
          - columnheader "Sort by Subject"
          - columnheader "Sort by Action"
          - columnheader "Sort by Stage"
          - columnheader "Sort by State"
          - columnheader "Sort by Due"
      - rowgroup:
        - row:
          - cell:
            - link "SOP-PUR-014":
              - /url: /tasks/task1111-1111-1111-1111-111111111111
        - row:
          - cell:
            - link "SOP-PRD-007":
              - /url: /tasks/task2222-2222-2222-2222-222222222222
  `);
});

test("exposes named Context controls and announces debounced filtered results", async ({
  page,
}) => {
  await installRegisterApi(page, { route: "context" });
  await page.goto("/context");

  const search = page.getByRole("textbox", { name: "Search", exact: true });
  await expect(search).toMatchAriaSnapshot(`- textbox "Search"`);
  await expect(page.getByRole("radiogroup", { name: "Filter by classification", exact: true }))
    .toMatchAriaSnapshot(`
    - radiogroup "Filter by classification":
      - radio "All" [checked]
      - radio "Internal"
      - radio "External"
  `);
  await expect(page.getByRole("radiogroup", { name: "Filter by category", exact: true }))
    .toMatchAriaSnapshot(`
    - radiogroup "Filter by category":
      - radio "All" [checked]
      - radio "Strength"
      - radio "Weakness"
      - radio "Opportunity"
      - radio "Threat"
      - radio "Uncategorized"
  `);
  await expect(page.getByRole("radiogroup", { name: "Filter by status", exact: true }))
    .toMatchAriaSnapshot(`
    - radiogroup "Filter by status":
      - radio "All" [checked]
      - radio "Active"
      - radio "Closed"
  `);

  await search.fill("legacy");
  const resultCount = page.getByText("1 issues", { exact: true });
  await expect(resultCount).toBeVisible();
  expect(await resultCount.evaluate((element) => getComputedStyle(element).display)).not.toBe(
    "none",
  );
  expect(await resultCount.getAttribute("aria-live")).toBe("polite");

  const table = page.getByRole("table");
  await assertRegisterTableStructure(page, 1);
  await expect(
    table.getByRole("button", { name: "Legacy on-disk mirror is hard to maintain" }),
  ).toHaveCount(1);
  await expect(table).toMatchAriaSnapshot(`
    - table:
      - rowgroup:
        - row:
          - columnheader "Issue"
          - columnheader "Sort by Classification"
          - columnheader "Sort by Category"
          - columnheader "Sort by Status"
          - columnheader "Sort by Last reviewed"
      - rowgroup:
        - row:
          - cell:
            - button "Legacy on-disk mirror is hard to maintain"
  `);
});
