import { expect, test } from "@playwright/test";
import { installRegisterApi } from "./support/api";

// R69's rail foot, measured in a real browser.
//
// The whole design decision this pins is POSITIONAL, and jsdom cannot see it. The foot is a second,
// non-growing `AppShell.Section` that sits OUTSIDE the nav's `grow ScrollArea`. Had it been placed
// inside — the obvious way to add something to the rail — it would scroll away with the nav list,
// and on a short viewport the theme control and the clock would be unreachable while every vitest
// assertion stayed green, because `getByRole` finds a control whether or not a browser would paint
// it where a person can reach it.
//
// A short viewport is the only arrangement in which the defect exists, so the precondition — that
// the nav genuinely overflows — is asserted BEFORE the conclusion. Without that check the spec
// would pass on a tall viewport for the trivial reason that nothing scrolls.
test.describe("Rail foot", () => {
  test("stays visible inside the viewport while the nav list scrolls", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 500 });
    await installRegisterApi(page, { route: "tasks" });
    await page.goto("/tasks");

    const control = page.getByLabel("Interface theme");
    await expect(control).toBeVisible();

    const scroller = page
      .locator("[data-scrollarea-viewport], .mantine-ScrollArea-viewport")
      .first();

    // PRECONDITION. If the nav does not overflow at this height the arrangement under test does not
    // occur, and everything below would pass against a foot placed inside the ScrollArea.
    const overflows = await scroller.evaluate((el) => el.scrollHeight > el.clientHeight + 1);
    expect(overflows, "nav must overflow at 500px for this spec to mean anything").toBe(true);

    const before = await control.boundingBox();
    await scroller.evaluate((el) => el.scrollTo(0, el.scrollHeight));
    await expect.poll(async () => scroller.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);

    const after = await control.boundingBox();
    expect(before).not.toBeNull();
    expect(after).not.toBeNull();
    // The foot does not move when the nav scrolls: it is not in the scrolling box.
    expect(Math.abs((after?.y ?? 0) - (before?.y ?? 0))).toBeLessThanOrEqual(1);

    // …and it is fully inside the viewport, not merely present in the DOM.
    const viewport = page.viewportSize();
    expect(after?.y ?? 0).toBeGreaterThanOrEqual(0);
    expect((after?.y ?? 0) + (after?.height ?? 0)).toBeLessThanOrEqual((viewport?.height ?? 0) + 1);
  });

  // The clock gained a six-digit date, and its row is `wrap="nowrap"` inside a rail fixed at 244px.
  // That combination fails in a browser and nowhere else: jsdom resolves no layout, so every vitest
  // assertion about the date passes whether it fits on the line, wraps, or overflows the rail
  // entirely. The width was first computed by hand — three fields plus two 6px gaps at 13px against
  // 244px minus padding — and a computation is exactly what this repository's own rule says is not
  // evidence for anything about size or clipping.
  test("the clock's date, time and zone sit on one line inside the rail", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await installRegisterApi(page, { route: "tasks" });
    await page.goto("/tasks");

    const date = page.getByLabel("Organization date");
    const time = page.getByLabel("Organization time");
    // PRECONDITION: the date must actually render, or every assertion below is vacuously true —
    // the clock returns null for an unusable zone and the component then renders nothing at all.
    await expect(date).toBeVisible();
    await expect(time).toBeVisible();

    const boxes = await page.evaluate(() => {
      const dateEl = document.querySelector('[aria-label="Organization date"]');
      const timeEl = document.querySelector('[aria-label="Organization time"]');
      const row = dateEl?.parentElement;
      // Guard the positional step: a parentElement is non-null for almost any node, so confirm the
      // element reached is really the row holding BOTH fields before measuring it.
      if (!dateEl || !timeEl || !row || !row.contains(timeEl)) return null;
      const r = (el: Element) => {
        const b = el.getBoundingClientRect();
        return { top: Math.round(b.top), right: b.right };
      };
      return {
        date: r(dateEl),
        time: r(timeEl),
        rowOverflow: row.scrollWidth - row.clientWidth,
        rowRight: row.getBoundingClientRect().right,
        railRight: (row.closest("nav") ?? document.body).getBoundingClientRect().right,
      };
    });

    expect(boxes, "expected one row holding both the date and the time").not.toBeNull();
    // One line: the two fields share a top edge. If the row wrapped, the date would sit above.
    expect(boxes?.date.top).toBe(boxes?.time.top);
    // And the row does not overflow itself — `nowrap` turns a too-narrow row into overflow, not a
    // second line, so the wrap check above cannot catch that on its own.
    expect(boxes?.rowOverflow ?? 999).toBeLessThanOrEqual(0);
    expect(boxes?.rowRight ?? 0).toBeLessThanOrEqual((boxes?.railRight ?? 0) + 1);
  });

  test("the theme control is operable and reports the chosen scheme to the document", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await installRegisterApi(page, { route: "tasks" });
    await page.goto("/tasks");

    const control = page.getByLabel("Interface theme");
    await expect(control).toBeVisible();

    // Click the LABEL, not the radio. Mantine's SegmentedControl visually hides the real <input>,
    // so Playwright refuses to click it — "element is not visible", 60 retries. jsdom has no
    // visibility model, so `userEvent.click` on the input succeeds in vitest and hides the fact
    // that a person cannot reach it that way. This is the arrangement a user actually operates.
    await control.getByText("Dark", { exact: true }).click();

    // The real proof that the control does something: Mantine stamps the resolved scheme on the
    // root element, which is what every `[data-mantine-color-scheme="dark"]` token block keys on.
    // Asserting the radio is checked would only prove the radio is a radio.
    await expect
      .poll(async () => page.evaluate(() => document.documentElement.dataset.mantineColorScheme))
      .toBe("dark");

    await control.getByText("Light", { exact: true }).click();
    await expect
      .poll(async () => page.evaluate(() => document.documentElement.dataset.mantineColorScheme))
      .toBe("light");
  });
});
