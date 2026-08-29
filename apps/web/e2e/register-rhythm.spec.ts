import { expect, test } from "@playwright/test";
import { installRegisterApi } from "./support/api";

// Two defects the owner found in a live walkthrough that every automated gate had passed, both
// invisible to jsdom because it performs no layout.
//
// 1. RHYTHM. The register pages had NO vertical spacing between the scorecard band, the filter
//    row and the table — measured gap=0 at every seam — so the blocks read as one collided mass.
//    The page header additionally carried its `mb` on the title ROW, which pushed the freshness
//    stamp away from the title it describes and left nothing beneath it.
// 2. BADGE LEGIBILITY. Mantine caps a Badge at `max-width: 100%` and ellipsises its label, so in a
//    squeezed table cell a status rendered "ACTION REQUIR…" or "DRA…".
//
// Both are measured here at the width they actually fail, which is NOT the default desktop width:
// at 1280px nothing truncates, and the badge defect only appears once the table is squeezed but
// not yet scrolling. A guard at a comfortable viewport would have proved nothing.

const CASES = [
  { key: "objectives", path: "/objectives" },
  { key: "risks", path: "/risks" },
  { key: "context", path: "/context" },
  { key: "interested-parties", path: "/interested-parties" },
  { key: "dcrs", path: "/dcrs" },
] as const;

for (const { key, path } of CASES) {
  test(`${key} keeps a consistent vertical rhythm between its page blocks`, async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await installRegisterApi(page, { route: key });
    await page.goto(path);
    await expect(page.getByRole("textbox", { name: "Search" })).toBeVisible();

    const gaps = await page.evaluate(() => {
      const main = document.querySelector("#main-content") ?? document.body;
      const container = main.querySelector(".mantine-Container-root") ?? main;
      const boxes = Array.from(container.children).map((c) => c.getBoundingClientRect());
      return boxes.slice(1).map((b, i) => Math.round(b.top - boxes[i]!.bottom));
    });

    // Every seam between top-level page blocks is separated. Zero was the defect.
    expect(gaps.length).toBeGreaterThan(1);
    for (const gap of gaps) {
      expect(gap).toBeGreaterThanOrEqual(8);
      // And nothing is double-spaced by a margin applied in two places at once.
      expect(gap).toBeLessThanOrEqual(24);
    }
  });

  test(`${key} renders every status badge without clipping its label`, async ({ page }) => {
    // 1000px, not 1280: the table is squeezed but has not yet handed over to its scroll container,
    // which is the only band where the label is clipped.
    await page.setViewportSize({ width: 1000, height: 900 });
    await installRegisterApi(page, { route: key });
    await page.goto(path);
    await expect(page.getByRole("textbox", { name: "Search" })).toBeVisible();

    const truncated = await page.evaluate(() =>
      Array.from(document.querySelectorAll(".mantine-Badge-label"))
        .filter((el) => (el as HTMLElement).scrollWidth > (el as HTMLElement).clientWidth + 1)
        .map((el) => (el.textContent ?? "").trim()),
    );
    expect(truncated).toEqual([]);

    // Letting a badge size to its content must not push the PAGE into horizontal scroll — the
    // table's own scroll container is what absorbs the extra width.
    const doc = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    }));
    expect(doc.scrollWidth - doc.clientWidth).toBeLessThanOrEqual(1);
  });
}
