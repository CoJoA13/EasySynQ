import { expect, test } from "@playwright/test";
import { installRegisterApi } from "./support/api";
import { REGISTER_CASES } from "./support/registers";

// The eight registers that render the shared RegisterPageHeader. `tasks` and `records` are
// deliberately absent: TasksInbox is not a register page, and RecordsPage was left un-adopted as a
// poor structural fit (no forbidden branch, and its own pre-empted invalid-cursor state).
const HEADED = new Map<string, string>([
  ["audits", "Internal audit"],
  ["objectives", "Quality objectives"],
  ["management-reviews", "Management reviews"],
  ["dcrs", "Change requests"],
  ["improvement", "Improvement"],
  ["risks", "Risk & opportunity register"],
  ["context", "Context of the organization"],
  ["interested-parties", "Interested parties"],
]);

// jsdom performs no layout, so the vitest suite can be green while a header clips its own contents
// — the shape of the S-ui-3 defect, where four Home cards clipped their only "Open …" link behind
// 2230 passing tests. RegisterPageHeader now owns the title row on twelve pages at once, so it is
// worth measuring in a real browser.
//
// SCOPE LIMIT, stated because it bounds what these tests prove: the browser harness fulfils
// `/api/v1/me/permissions` with `permissions: []` (e2e/support/api.ts), so every caller here is an
// UNGRANTED reader and no register renders its header action. These tests therefore exercise the
// one-child header only — they cannot catch a title/action collision, and a `wrap="nowrap"`
// mutation is provably inert against this fixture. The two-child arrangement is covered in jsdom by
// src/lib/RegisterPageHeader.test.tsx. What this file uniquely proves is that the denied reader's
// header is laid out correctly by a real engine and carries no phantom action box.
for (const registerCase of REGISTER_CASES) {
  const heading = HEADED.get(registerCase.key);
  if (!heading) continue;

  test(`${registerCase.key} lays out its shared header for a denied reader at 320px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: 320, height: 800 });
    await installRegisterApi(page, { route: registerCase.key });
    await page.goto(registerCase.path);

    const title = page.getByRole("heading", { level: 2, name: heading, exact: true });
    await expect(title).toHaveCount(1);
    await expect(title).toBeVisible();

    const geometry = await title.evaluate((el) => {
      const row = el.parentElement;
      if (!row) throw new Error("Expected the page heading to sit inside a header row");
      const rowBox = row.getBoundingClientRect();
      const titleBox = el.getBoundingClientRect();
      return {
        childCount: row.children.length,
        rowScrollWidth: row.scrollWidth,
        rowClientWidth: row.clientWidth,
        rowLeft: rowBox.left,
        rowRight: rowBox.right,
        titleLeft: titleBox.left,
        titleRight: titleBox.right,
        titleHeight: titleBox.height,
        titleScrollWidth: el.scrollWidth,
        titleClientWidth: el.clientWidth,
      };
    });

    // The gate is false for this caller, so the row holds the title and NOTHING else. A wrapper
    // element around a falsy `actions` would make this 2 — an empty box sitting where the reader
    // has no affordance. This is the browser-side counterpart of the unit test's child-count pin.
    expect(geometry.childCount).toBe(1);

    // The header row does not clip its own contents.
    expect(geometry.rowScrollWidth).toBeLessThanOrEqual(geometry.rowClientWidth + 1);

    // The title is painted, inside the row, and not itself truncated.
    expect(geometry.titleHeight).toBeGreaterThan(0);
    expect(geometry.titleLeft).toBeGreaterThanOrEqual(geometry.rowLeft - 1);
    expect(geometry.titleRight).toBeLessThanOrEqual(geometry.rowRight + 1);
    expect(geometry.titleScrollWidth).toBeLessThanOrEqual(geometry.titleClientWidth + 1);

    // And the page as a whole still owns no horizontal scroll.
    const doc = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    }));
    expect(doc.scrollWidth - doc.clientWidth).toBeLessThanOrEqual(1);
  });
}
