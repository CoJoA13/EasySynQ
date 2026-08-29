import { expect, test } from "@playwright/test";
import { installRegisterApi } from "./support/api";

const J = (body: unknown) => ({
  status: 200,
  contentType: "application/json",
  body: JSON.stringify(body),
});

// Home quadrant geometry, in a real browser.
//
// This exists because S-ui-3 shipped a defect that every other gate was blind to. Giving the
// quadrant card a tinted header band made the body a SIBLING of that band; the body still asked for
// height:100%, which on a grid-stretched Paper resolves to the FULL card height, so the content
// overflowed by exactly the band's height and the card's `overflow: hidden` clipped the result. What
// got clipped was the card's only navigation affordance — the "Open …" link — on all four tiles, at
// every breakpoint, on the landing page. Measured before the fix: 31px past the card bottom at
// 1280px and 75px at 320px.
//
// eslint, strict tsc and 2200+ vitest tests were all green throughout, because jsdom performs no
// layout: `getByRole("link")` finds the anchor whether or not a browser would ever paint it. Only a
// rendered measurement can see this class of defect, so it belongs here rather than in vitest.
//
// Routes registered AFTER installRegisterApi take precedence in Playwright, which is how the Home
// reads below are stubbed on top of the shared harness.
test.describe("Home quadrant geometry", () => {
  for (const width of [1280, 320]) {
    test(`each quadrant keeps its Open action inside the card at ${width}px`, async ({ page }) => {
      await installRegisterApi(page, { route: "tasks" });
      await page.route("**/api/v1/reports/compliance-checklist*", (r) =>
        r.fulfill(
          J({
            framework: "iso9001:2015",
            rollup: { total: 20, covered: 3, partial: 3, gap: 14, overdue_review: 2 },
            rows: [],
          }),
        ),
      );
      await page.route("**/api/v1/objectives/scorecard*", (r) =>
        r.fulfill(
          J({
            total: 3,
            on_target: 1,
            by_rag: { green: 1, amber: 1, red: 1, unmeasured: 0 },
            objectives: [],
          }),
        ),
      );
      await page.route("**/api/v1/risks/summary*", (r) =>
        r.fulfill(J({ published: true, total: 5, high_risk: 2, by_band: {} })),
      );
      await page.route("**/api/v1/context/summary*", (r) =>
        r.fulfill(J({ published: true, total: 4, active: 4, never_reviewed: 1 })),
      );
      await page.route("**/api/v1/interested-parties/summary*", (r) =>
        r.fulfill(J({ published: true, total: 3, active: 3, never_reviewed: 0 })),
      );
      await page.route("**/api/v1/capas*", (r) =>
        r.fulfill(J({ data: [{ id: "c1", close_state: "Open" }] })),
      );
      await page.route("**/api/v1/ncrs*", (r) =>
        r.fulfill(J({ data: [{ id: "n1", disposition: null }] })),
      );
      await page.route("**/api/v1/complaints*", (r) =>
        r.fulfill(J({ data: [{ id: "x1", spawned_capa_id: null }] })),
      );
      await page.route("**/api/v1/improvement-initiatives*", (r) =>
        r.fulfill(J({ data: [{ id: "i1", stage: "Open" }] })),
      );
      await page.route("**/api/v1/audits*", (r) =>
        r.fulfill(J({ data: [{ id: "a1", state: "Open" }] })),
      );
      await page.route("**/api/v1/management-reviews/next-due*", (r) =>
        r.fulfill(
          J({ owner_configured: true, next_review_due: "2026-06-01", review_state: "due_soon" }),
        ),
      );
      await page.route("**/api/v1/admin/drift/status*", (r) =>
        r.fulfill(
          J({
            scans: { MIRROR: { status: "CLEAN" }, BLOB_REHASH: { status: "CLEAN" } },
            blob_coverage: { failing: 0 },
            superseded_copies: { copies: 2 },
          }),
        ),
      );

      await page.route("**/api/v1/tasks*", (r) => r.fulfill(J([])));
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/");
      const cards = page.getByRole("group", { name: /quadrant$/ });
      await expect(cards.first()).toBeVisible();
      const n = await cards.count();
      expect(n).toBeGreaterThan(0);
      for (let i = 0; i < n; i++) {
        const card = cards.nth(i);
        const name = await card.getAttribute("aria-label");
        const link = card.getByRole("link").last();
        const cb = await card.boundingBox();
        const lb = await link.boundingBox();
        expect(cb, `${name}: no card box`).not.toBeNull();
        expect(lb, `${name}: no link box`).not.toBeNull();
        const overflow = lb!.y + lb!.height - (cb!.y + cb!.height);
        // The action must sit INSIDE the card, not past its clipped bottom edge.
        expect(
          overflow,
          `${name} @${width}px: Open action is ${overflow.toFixed(1)}px past the card bottom`,
        ).toBeLessThanOrEqual(0);
      }
    });
  }
});
