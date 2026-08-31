import { expect, test } from "@playwright/test";
import { installRegisterApi } from "./support/api";

/**
 * The CAPA board's browser coverage, added by S-ui-6.
 *
 * `/capa` is deliberately not a `REGISTER_CASES` entry — see the `ScenarioRoute` note in
 * `support/api.ts` for why the table-shaped shared specs cannot measure this page — so it gets its
 * own spec, in the idiom of `risk-matrix-legend.spec.ts`.
 *
 * jsdom performs no layout, so every assertion here is unreachable from the vitest suite. Two of
 * them cover changes that shipped UNMEASURED on this route: `RegisterFilterBar`'s bottom margin
 * (S-ui-5b) and the theme's `ScrollArea type: "auto"` (S-ui-5c), which is what gives the board's
 * permanently-overflowing kanban a visible horizontal scrollbar.
 */

interface BoardGeometry {
  gridLeft: number;
  gridRight: number;
  gridWidth: number;
  selectCount: number;
  selectsShareOneRow: boolean;
  selectsLeft: number;
  selectsRight: number;
  filterBarMarginBottom: string;
  kanbanOverflows: boolean;
  tileCount: number;
  captionTops: number[];
  tileGaps: number[];
}

const read = async (page: import("@playwright/test").Page): Promise<BoardGeometry> =>
  page.evaluate(() => {
    const round = (n: number) => Math.round(n);

    // --- the three client-side facet selects ------------------------------------------------
    // Located by accessible name, not by position: a `clearable` Mantine Select renders a hidden
    // input beside its root, so the filter Group has SIX children for three controls, and an
    // index-based walk would silently measure the wrong node.
    const roots = ["Source", "Severity", "State"].map((name) => {
      const input = document.querySelector(`input[aria-label="${name}"]`);
      if (!input) throw new Error(`no ${name} filter select`);
      const root = input.closest(".mantine-Select-root");
      if (!(root instanceof HTMLElement)) throw new Error(`no Select root above ${name}`);
      return root;
    });
    const rects = roots.map((r) => r.getBoundingClientRect());
    const tops = rects.map((r) => round(r.top));

    // --- the summary grid the filter row is supposed to line up with ------------------------
    const caption = Array.from(document.querySelectorAll("p,div,span")).find(
      (el) => el.textContent?.trim() === "Open CAPAs",
    );
    if (!caption) throw new Error("no Open CAPAs caption");
    let grid: HTMLElement | null = caption as HTMLElement;
    while (grid && getComputedStyle(grid).display !== "grid") grid = grid.parentElement;
    if (!grid) throw new Error("no grid ancestor above the summary caption");
    if (grid.children.length !== 4)
      throw new Error(`summary grid has ${grid.children.length} tiles, expected 4`);
    const g = grid.getBoundingClientRect();

    // Located by TEXT, not by position. `firstElementChild` resolves to whatever wrapper a tile
    // happens to use, and a wrapper's top is the card's content top in every arrangement — so that
    // form of the measurement cannot see a caption pushed down INSIDE its wrapper, and a mutation
    // that reintroduces exactly that stagger reads as inert.
    const captionTops = ["Open CAPAs", "Overdue", "By severity", "By source"].map((text) => {
      const hits = Array.from(grid.querySelectorAll("p,div,span")).filter(
        (el) => el.textContent?.trim() === text && el.children.length === 0,
      );
      if (hits.length !== 1)
        throw new Error(`expected one ${JSON.stringify(text)} caption, found ${hits.length}`);
      return round(hits[0]!.getBoundingClientRect().top);
    });

    // --- the shared date window (S-ui-5b gave it a bottom margin) ---------------------------
    const createdFrom = Array.from(document.querySelectorAll("label")).find(
      (l) => l.textContent?.trim() === "Created from",
    );
    if (!createdFrom) throw new Error("no Created from label");
    let filterBar: HTMLElement | null = createdFrom;
    while (filterBar && !filterBar.className.includes("Group-root"))
      filterBar = filterBar.parentElement;
    if (!filterBar) throw new Error("no Group above the date window");

    // --- the kanban's scroll owner ----------------------------------------------------------
    // Qualified by aria-label, NOT by `[role="group"]`: the left rail's four quadrants carry that
    // role too, and are earlier in the document.
    const column = document.querySelector('[role="group"][aria-label="Open / NC"]');
    if (!column) throw new Error("no Open / NC kanban column");
    let scroller: HTMLElement | null = column.parentElement;
    while (scroller) {
      const overflowX = getComputedStyle(scroller).overflowX;
      if (overflowX === "auto" || overflowX === "scroll") break;
      scroller = scroller.parentElement;
    }
    if (!scroller) throw new Error("no horizontal scroll owner above the kanban");

    return {
      gridLeft: round(g.left),
      gridRight: round(g.right),
      gridWidth: round(g.width),
      selectCount: roots.length,
      selectsShareOneRow: tops.every((t) => t === tops[0]),
      selectsLeft: round(Math.min(...rects.map((r) => r.left))),
      selectsRight: round(Math.max(...rects.map((r) => r.right))),
      filterBarMarginBottom: getComputedStyle(filterBar).marginBottom,
      kanbanOverflows: scroller.scrollWidth > scroller.clientWidth + 1,
      tileCount: grid.children.length,
      captionTops,
      // Gaps between tiles that share a row (a wrapped tile's gap is meaningless).
      tileGaps: Array.from(grid.children)
        .slice(1)
        .map((tile, i) => ({ prev: grid.children[i]!, tile }))
        .filter(
          ({ prev, tile }) =>
            Math.abs(prev.getBoundingClientRect().top - tile.getBoundingClientRect().top) < 2,
        )
        .map(({ prev, tile }) =>
          round(tile.getBoundingClientRect().left - prev.getBoundingClientRect().right),
        ),
    };
  });

async function openBoard(page: import("@playwright/test").Page, width: number): Promise<void> {
  await installRegisterApi(page, { route: "capa" });
  await page.setViewportSize({ width, height: 1000 });
  await page.goto("/capa");
  await expect(page.getByRole("heading", { name: "Nonconformity and CAPA" })).toBeVisible();
}

// LOAD-BEARING. The three facet selects took their natural width — a fixed ~648px row — while the
// summary grid above them tracks the viewport, so the two blocks' right edges parted company by
// 44px at 1000 and 324px at 1280: the wider the window, the worse it read. The mismatch grows with
// the viewport, so 1280 is the honest width to pin, and both cases fail against the pre-fix tree.
for (const width of [1280, 1000]) {
  test(`the capa filter row ends flush with the summary grid at ${width}px`, async ({ page }) => {
    await openBoard(page, width);
    const m = await read(page);

    // Genuine preconditions: a wrapped filter row or a collapsed grid would satisfy the edge
    // comparison vacuously.
    expect(m.selectsShareOneRow).toBe(true);
    expect(m.gridWidth).toBeGreaterThan(600);
    // Belt-and-braces, NOT a measurement: `read()` locates the three selects by a hard-coded name
    // array and throws when one is missing, so this can never be anything but 3.
    expect(m.selectCount).toBe(3);

    expect(m.selectsLeft).toBe(m.gridLeft);
    expect(Math.abs(m.selectsRight - m.gridRight)).toBeLessThanOrEqual(1);
  });
}

// Pins S-ui-5b's `mb="md"` on the shared date window, which shipped to this uncovered route with no
// browser assertion. Browser-only: jsdom resolves no cascade, so the `--es-space-5` custom property
// behind Mantine's `md` never becomes a pixel value there.
test("the capa date window keeps its bottom margin", async ({ page }) => {
  await openBoard(page, 1280);
  expect((await read(page)).filterBarMarginBottom).toBe("16px");
});

// Pins S-ui-5c's theme-level `ScrollArea type: "auto"`, which also shipped to this route unmeasured.
// Six 260px columns always overflow this container, so the bar should always be there.
//
// POLLED, never snapshotted: `ScrollAreaScrollbarAuto` holds `useState(false)` and renders nothing
// until one of its two ResizeObservers fires, so a single synchronous read is a race that reddens
// CI on a correct tree (the S-ui-5c false-failure). Qualified by `data-orientation="horizontal"`
// because the ScrollArea root holds both bars and the vertical one is `display: none` here.
test("the capa kanban shows a horizontal scrollbar for its overflow", async ({ page }) => {
  await openBoard(page, 1280);

  // Precondition: the arrangement being measured actually overflows. Without this the absence of a
  // bar would be correct behaviour and the assertion below would be measuring nothing.
  expect((await read(page)).kanbanOverflows).toBe(true);

  await expect
    .poll(async () =>
      page.evaluate(() => {
        const column = document.querySelector('[role="group"][aria-label="Open / NC"]');
        const root = column?.closest(".mantine-ScrollArea-root");
        if (!root) throw new Error("no ScrollArea root above the kanban");
        const bars = Array.from(
          root.querySelectorAll('.mantine-ScrollArea-scrollbar[data-orientation="horizontal"]'),
        );
        return bars.some((bar) => getComputedStyle(bar).display !== "none");
      }),
    )
    .toBe(true);
});

// The summary row spent the full page width on one digit and three pills — two tiles of 477px at
// 1280 and 638px at 1600 with every mark in the leftmost ~60px. It now carries four aggregates, and
// their captions must sit on ONE line: the first draft put the Overdue glyph in a Group with that
// tile's caption, which centred a 13px label against a 16px glyph and dropped it ~2px below its
// three neighbours. A two-pixel stagger is invisible to review and to jsdom, and visible on screen.
test("the four capa summary tiles caption on one line", async ({ page }) => {
  await openBoard(page, 1280);
  const m = await read(page);

  // Belt-and-braces, NOT a measurement: `read()` already throws unless the grid holds exactly 4.
  expect(m.tileCount).toBe(4);
  // Genuine precondition: at a narrow viewport the tiles stack and equal caption tops would be
  // impossible rather than merely true, so the assertion below must run on the four-up arrangement.
  expect(m.gridWidth).toBeGreaterThan(600);
  expect(new Set(m.captionTops).size).toBe(1);
});

// Belt-and-braces, and labelled as such because it passes against the pre-slice tree too. The
// residual named an inter-card gap as its second defect; measurement found the seam to be the
// theme's `md` at every width, uniform with every other gap on the page, and the owner redirected
// the fix to filling the row instead. This pins the gutter as DELIBERATELY unchanged, so a later
// slice that alters it does so on purpose rather than by drift.
test("the capa summary gutter is the theme's md, unchanged by the four-up row", async ({ page }) => {
  await openBoard(page, 1280);
  const m = await read(page);
  expect(m.tileGaps.length).toBe(3);
  expect(new Set(m.tileGaps)).toEqual(new Set([16]));
});

// Capable of failing, and worth stating exactly HOW, because the first version of this comment
// claimed more than the test delivers. `preventGrowOverflow={false}` lets a select exceed its 1/3
// share, so the worry was that it pushes the row past a narrow viewport. Forcing `wrap="nowrap"`
// does NOT redden this — flex-shrink absorbs it — so the grow/wrap interaction is not what is
// guarded here. Pinning a select wider than the viewport DOES redden it (measured: 432 > 320). So
// what this pins is that no control in this row carries an intrinsic width past the viewport, which
// is the failure mode a `grow` row invites; the wrap behaviour itself is unmeasured.
test("the capa filter row never overflows the document at 320px", async ({ page }) => {
  await openBoard(page, 320);
  const overflow = await page.evaluate(() => {
    const root = document.documentElement;
    return { scrollWidth: root.scrollWidth, clientWidth: root.clientWidth };
  });
  expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.clientWidth);
});
