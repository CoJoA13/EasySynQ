import { expect } from "vitest";

/**
 * The heading outline of a rendered route, in document order.
 *
 * ⚠ This exists because NEITHER axe gate in the repository can see a heading defect, and both
 * failures are silent. `page-has-heading-one` carries the axe-core selector `html:not(html *)`, so
 * it matches only the `<html>` element and is reported INAPPLICABLE for every run scoped to a
 * container — which is every `axe(container)` call in the vitest suite and the container-scoped
 * `axe.run` in `e2e/register-accessibility.spec.ts`. `heading-order` does fire inside a container,
 * but its impact is `moderate`, and that e2e helper keeps only `serious`/`critical`. Both were
 * confirmed by running axe-core 4.12.1 against a fixture holding an h2 followed by an h4: scoped,
 * `page-has-heading-one` is inapplicable and `heading-order` is filtered away; unscoped, both fire
 * at `moderate`. So a page with no headings at all passes every gate the repository had.
 *
 * A direct DOM walk is used rather than re-enabling those rules because it cannot be defeated by a
 * rule tag, an impact filter or a context scope, and it names the offending heading in its failure
 * message — which is what makes a thirty-file re-levelling reviewable.
 */
export interface OutlineEntry {
  level: number;
  text: string;
}

const HEADING_SELECTOR = "h1, h2, h3, h4, h5, h6, [role='heading']";

/** Every heading under `root`, in document order. Mirrors axe's `heading-order` candidate set. */
export function readHeadingOutline(root: ParentNode = document.body): OutlineEntry[] {
  return (
    [...root.querySelectorAll<HTMLElement>(HEADING_SELECTOR)]
      // A heading hidden from the accessibility tree is not part of the outline a screen reader walks.
      // `hidden` is checked alongside `aria-hidden` so this agrees with axe's own `heading-order`,
      // which excludes both. jsdom resolves no cascade, so a `display: none` heading is beyond
      // either gate — that is a false-FAIL direction, so it errs safe.
      .filter(
        (node) =>
          node.closest("[aria-hidden='true']") === null && node.closest("[hidden]") === null,
      )
      .map((node) => {
        // An explicit aria-level wins over the tag, exactly as the accessibility tree resolves it.
        const ariaLevel = Number(node.getAttribute("aria-level"));
        const tagLevel = /^H[1-6]$/.test(node.tagName) ? Number(node.tagName[1]) : NaN;
        return {
          level: Number.isFinite(ariaLevel) && ariaLevel > 0 ? ariaLevel : tagLevel,
          text: (node.textContent ?? "").trim(),
        };
      })
      // A `[role=heading]` with neither a heading tag nor an aria-level has no level to check.
      .filter((entry) => Number.isFinite(entry.level))
  );
}

function render(outline: OutlineEntry[]): string {
  if (outline.length === 0) return "(no headings rendered)";
  return outline.map(({ level, text }) => `  h${level}  ${text || "(empty)"}`).join("\n");
}

/**
 * Assert the sound document outline: exactly one `h1`, and no level skipped on the way down.
 *
 * Descending by more than one level (h1 → h3) is the skip; ASCENDING any distance is not (a page
 * may close an h3 subsection and open the next h2). That asymmetry is the whole rule, and getting
 * it backwards would make every multi-section page fail.
 */
export function expectSoundHeadingOutline(root: ParentNode = document.body): OutlineEntry[] {
  const outline = readHeadingOutline(root);
  const shown = render(outline);

  const topLevel = outline.filter(({ level }) => level === 1);
  expect(topLevel.length, `expected exactly one h1, found ${topLevel.length}:\n${shown}`).toBe(1);

  for (const [index, entry] of outline.entries()) {
    if (index === 0) continue;
    const previous = outline[index - 1]!;
    expect(
      entry.level - previous.level,
      `heading level skipped: h${previous.level} "${previous.text}" is followed by ` +
        `h${entry.level} "${entry.text}":\n${shown}`,
    ).toBeLessThanOrEqual(1);
  }

  // The first heading must be the h1 — an h2 rendered above the page title is a skip that the
  // pairwise check above cannot see, because it only ever compares a heading to its predecessor.
  expect(outline[0]?.level, `the first heading is not the h1:\n${shown}`).toBe(1);

  return outline;
}
