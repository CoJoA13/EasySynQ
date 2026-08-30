import { expect, test } from "@playwright/test";
import { installRegisterApi } from "./support/api";

// The risk matrix's legend overflowed the grid it keys, and jsdom cannot see that.
//
// `RiskMatrix` caps its SVG at VIEW_W (306px = M.left + GRID + M.right) but the legend beneath it
// is an ordinary flex Group, so before the fix the SHARED parent Stack took the legend's
// max-content — measured 396px — and the band-tone key ran 90px wider than the grid it describes.
//
// The second case is the one worth explaining. The matrix and the page's scorecard band sit in one
// `Group wrap="wrap"`, so the matrix column's width decides when the band drops beneath it. The
// fix was held back on the belief that capping the column would push the band below; measurement
// showed the opposite. Uncapped the band wraps at <=1230px, capped at <=1140px, so the cap buys 90px
// MORE side-by-side width. Asserting "beside at 1200" therefore fails against the pre-fix tree,
// which is what makes it evidence rather than decoration.
const read = async (page: import("@playwright/test").Page) =>
  page.evaluate(() => {
    // Walked positionally, but every hop is then CHECKED, because `parentElement` and
    // `lastElementChild` are non-null for almost any rendered tree — an unchecked walk would keep
    // measuring confidently after a refactor moved the legend, and report a passing comparison
    // between two elements that are no longer the grid and its key.
    const svg = document.querySelector("svg[role='img']");
    if (!svg) throw new Error("no risk matrix svg");
    const stack = svg.parentElement;
    const group = stack?.parentElement;
    const legend = stack?.lastElementChild;
    if (!stack || !group || !legend) throw new Error("unexpected matrix structure");
    if (legend === svg) throw new Error("legend resolved to the svg — the Stack has one child");
    if (!legend.textContent?.trim()) throw new Error("legend resolved to an element with no text");
    // The band key renders one badge per risk band, so anything with fewer is not the legend.
    if (legend.children.length < 2)
      throw new Error("legend has too few children to be the band key");
    const kids = Array.from(group.children);
    if (kids.length < 2) throw new Error("matrix has no sibling band to measure against");
    const round = (n: number) => Math.round(n);
    return {
      svgW: round(svg.getBoundingClientRect().width),
      legendW: round(legend.getBoundingClientRect().width),
      bandBesideMatrix:
        Math.abs(kids[0]!.getBoundingClientRect().top - kids[1]!.getBoundingClientRect().top) < 4,
    };
  });

test("the risk-matrix legend never runs wider than the grid it keys", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 1000 });
  await installRegisterApi(page, { route: "risks" });
  await page.goto("/risks");
  await expect(page.getByRole("textbox", { name: "Search" })).toBeVisible();

  const m = await read(page);
  // Guard the fixture: a matrix that rendered at zero width would satisfy the comparison vacuously.
  expect(m.svgW).toBeGreaterThan(200);
  expect(m.legendW).toBeLessThanOrEqual(m.svgW);
});

test("capping the matrix keeps the scorecard band beside it down to 1200px", async ({ page }) => {
  await installRegisterApi(page, { route: "risks" });
  await page.setViewportSize({ width: 1200, height: 1000 });
  await page.goto("/risks");
  await expect(page.getByRole("textbox", { name: "Search" })).toBeVisible();

  // Load-bearing: the pre-fix column is 396px wide and the band has already wrapped by 1200px.
  expect((await read(page)).bandBesideMatrix).toBe(true);

  // Belt-and-braces, not evidence: the band wraps below on a genuinely narrow viewport in BOTH
  // the pre-fix and post-fix trees. It is here so the pin above cannot be read as "never wraps".
  await page.setViewportSize({ width: 1000, height: 1000 });
  await expect.poll(async () => (await read(page)).bandBesideMatrix).toBe(false);
});
