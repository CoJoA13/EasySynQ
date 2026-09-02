import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { installRegisterApi } from "./support/api";
import { REGISTER_CASES } from "./support/registers";

// The registers that render the shared RegisterPageHeader AND whose header action is gated on a
// single SYSTEM permission key, so the harness can render both the denied and the granted header.
// `tasks` and `records` are absent because they are not adopters: TasksInbox is not a register
// page, and RecordsPage was left un-adopted as a poor structural fit. `risks`, `context` and
// `interested-parties` are absent for a different reason — their action is gated on
// `headEditable && canManage`, where `headEditable` comes from the register head's lifecycle state,
// so a permission grant alone does not render it.
const HEADED = [
  { key: "audits", heading: "Internal audit", grant: "audit.create", action: "New audit" },
  {
    key: "objectives",
    heading: "Quality objectives",
    grant: "objective.manage",
    action: "New objective",
  },
  {
    key: "management-reviews",
    heading: "Management reviews",
    grant: "mgmtReview.create",
    // The longest action label in the cohort — the worst case for a 320px title/action collision.
    action: "New management review",
  },
  { key: "dcrs", heading: "Change requests", grant: "changeRequest.create", action: "Raise DCR" },
  {
    key: "improvement",
    heading: "Improvement",
    grant: "improvement.manage",
    action: "New initiative",
  },
] as const;

// A key typo or a rename in REGISTER_CASES would otherwise silently generate fewer tests and leave
// this file green — the guard neighbouring specs already use (register-geometry.spec.ts:24).
const CASE_KEYS = new Set(REGISTER_CASES.map((c) => c.key));
for (const { key } of HEADED) {
  if (!CASE_KEYS.has(key)) throw new Error(`Unknown register manifest key: ${key}`);
}

// jsdom performs no layout, so the vitest suite can be green while a header clips its own contents
// — the shape of the S-ui-3 defect, where four Home cards clipped their only "Open …" link behind
// 2230 passing tests. RegisterPageHeader now owns the title row on eleven pages at once.
//
// Reaching the two-child header needs a GRANTED caller: the harness fulfils
// /api/v1/me/permissions with `permissions: []` by default, so without the grant below no register
// ever renders its action and this file would only ever measure a lone heading. Each scenario
// grants the single key its header gates on.
//
// WHAT THIS FILE PROVES, stated exactly, because an over-claimed geometry test is worse than none.
// It proves the child count in both directions — a granted reader gets the action, a denied reader
// gets no phantom box around a falsy `actions` — and that the two children never overlap and never
// push the page into horizontal scroll. Both child-count assertions are mutation-killed (wrapping
// `{actions}` in a div, and an early fixture bug that emitted bare permission strings instead of
// `{ key, effect }` entries, each turned this file red).
//
// It does NOT prove clipping resistance, and the clipping assertions below are honest belt-and-
// braces rather than evidence: measured at 320px the row is 256px and these titles render at
// 116-237px, so with the Group's default wrap the title always has room. `wrap="nowrap"` and an
// explicit `text-overflow: ellipsis` on the Title were both applied as mutations and both stayed
// green — flexbox shrinks rather than overlaps, so neither is a defect this viewport can expose.

function headerGeometry(page: Page, heading: string) {
  return page
    .getByRole("heading", { level: 1, name: heading, exact: true })
    .evaluate((el: Element) => {
      const row = el.parentElement;
      if (!row) throw new Error("Expected the page heading to sit inside a header row");
      const rowBox = row.getBoundingClientRect();
      const boxes = Array.from(row.children).map((c) => {
        const b = c.getBoundingClientRect();
        return { left: b.left, right: b.right, top: b.top, bottom: b.bottom, width: b.width };
      });
      return {
        childCount: row.children.length,
        rowScrollWidth: row.scrollWidth,
        rowClientWidth: row.clientWidth,
        rowLeft: rowBox.left,
        rowRight: rowBox.right,
        boxes,
        titleScrollWidth: el.scrollWidth,
        titleClientWidth: el.clientWidth,
      };
    });
}

for (const { key, heading, grant, action } of HEADED) {
  const registerCase = REGISTER_CASES.find((c) => c.key === key);
  if (!registerCase) throw new Error(`Missing register manifest: ${key}`);

  test(`${key} keeps its granted header uncollided at 320px`, async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 800 });
    await installRegisterApi(page, { route: key, permissions: [grant] });
    await page.goto(registerCase.path);

    // Sentinel FIRST. Without it every assertion below would hold identically on the forbidden
    // branch — which also renders the heading inside a one-child Group — so a route that started
    // 403ing would keep this file green while it measured the wrong page. The search box exists
    // only once the register has loaded.
    await expect(page.getByRole("textbox", { name: "Search" })).toBeVisible();

    const actionButton = page.getByRole("button", { name: action, exact: true });
    await expect(actionButton).toBeVisible();

    const g = await headerGeometry(page, heading);

    // The grant landed and the row really holds both children — otherwise this test would quietly
    // degrade into the denied case it was written to stop testing.
    expect(g.childCount).toBe(2);

    // Neither child is clipped by the row, and the row does not clip itself.
    expect(g.rowScrollWidth).toBeLessThanOrEqual(g.rowClientWidth + 1);
    for (const b of g.boxes) {
      expect(b.width).toBeGreaterThan(0);
      expect(b.left).toBeGreaterThanOrEqual(g.rowLeft - 1);
      expect(b.right).toBeLessThanOrEqual(g.rowRight + 1);
    }

    // The title is not truncated by the action beside it.
    expect(g.titleScrollWidth).toBeLessThanOrEqual(g.titleClientWidth + 1);

    // Title and action never overlap — either they stack (a long title wraps the Group, e.g.
    // "Management reviews" beside "New management review") or they sit side by side (a short one
    // fits, e.g. "Internal audit"). Both are correct; overlap is not.
    const [first, second] = g.boxes;
    if (!first || !second) throw new Error("Expected exactly two header children");
    const stacked = second.top >= first.bottom - 1;
    const sideBySide = second.left >= first.right - 1;
    expect(stacked || sideBySide).toBe(true);

    // And the page as a whole still owns no horizontal scroll.
    const doc = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    }));
    expect(doc.scrollWidth - doc.clientWidth).toBeLessThanOrEqual(1);
  });

  test(`${key} gives a denied reader a header with no phantom action box`, async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 800 });
    await installRegisterApi(page, { route: key });
    await page.goto(registerCase.path);
    await expect(page.getByRole("textbox", { name: "Search" })).toBeVisible();
    await expect(page.getByRole("button", { name: action, exact: true })).toHaveCount(0);

    // `actions` arrives as `can(key) && <Button/>` — `false`, not undefined. A wrapper element
    // around it would give an ungranted reader an empty box where the gate should be invisible.
    const g = await headerGeometry(page, heading);
    expect(g.childCount).toBe(1);
  });
}
