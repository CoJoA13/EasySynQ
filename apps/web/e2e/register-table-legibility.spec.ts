import { expect, test } from "@playwright/test";
import { installRegisterApi } from "./support/api";

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
