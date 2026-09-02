import { expect, type Page } from "@playwright/test";
import { resolve } from "node:path";

const axePath = resolve(
  import.meta.dirname,
  "../../node_modules/jest-axe/node_modules/axe-core/axe.min.js",
);

/**
 * The two axe rules that police the document outline, asserted in the ONE arrangement where they
 * can actually report.
 *
 * ⚠ Neither rule can fire through `expectNoSeriousOrCriticalViolations`, on two INDEPENDENT
 * grounds, and fixing either one alone leaves the gate inert. Measured against axe-core 4.12.1 with
 * a fixture holding an h2 followed by an h4:
 *
 *   scoped to `#main-content > .mantine-Container-root`
 *     page-has-heading-one -> INAPPLICABLE   (its selector is `html:not(html *)`, so it matches
 *                                             only the <html> element, which no container contains)
 *     heading-order        -> impact=moderate, dropped by the serious/critical filter
 *   run against `document`
 *     page-has-heading-one -> impact=moderate
 *     heading-order        -> impact=moderate
 *
 * So this helper must run UNSCOPED and must not filter by impact. It deliberately does not reuse
 * the other helper: that one's scope and severity floor are correct for what it checks, and
 * widening it would change the meaning of every existing assertion that calls it.
 *
 * ⚠ Those two rules are still not the whole contract, and the gap is not obvious: axe has NO rule
 * that objects to a SECOND h1, and none that objects to an h2 rendered ABOVE the h1.
 * `page-has-heading-one` only requires at least one, and `heading-order` permits ascending by any
 * distance. Both arrangements were confirmed green against a deliberately broken page before these
 * last two assertions were added. They are the same two checks `src/test/headingOutline.ts` makes,
 * restated here because a route reached only by the browser layer would otherwise have neither.
 */
export async function expectSoundHeadingOutline(page: Page): Promise<void> {
  await page.addScriptTag({ path: axePath });
  const { violations, outline } = await page.evaluate(async () => {
    const axe = (
      window as unknown as Window & {
        axe: {
          run: (
            context: Document,
            options: { runOnly: { type: "rule"; values: string[] } },
          ) => Promise<{ violations: Array<{ id: string; nodes: Array<{ target: string[] }> }> }>;
        };
      }
    ).axe;
    const results = await axe.run(document, {
      runOnly: { type: "rule", values: ["page-has-heading-one", "heading-order"] },
    });
    return {
      violations: results.violations.map(({ id, nodes }) => ({
        id,
        targets: nodes.flatMap(({ target }) => target),
      })),
      // Reported alongside the failure so a red run names the actual outline rather than only the
      // rule that objected — the difference between a five-minute fix and a bisect.
      outline: [...document.querySelectorAll("h1, h2, h3, h4, h5, h6")].map(
        (node) => `${node.tagName.toLowerCase()}  ${(node.textContent ?? "").trim()}`,
      ),
    };
  });
  const shown = `document outline was:\n${outline.join("\n")}`;
  expect(violations, shown).toEqual([]);
  expect(outline.filter((line) => line.startsWith("h1")), shown).toHaveLength(1);
  expect(outline[0]?.slice(0, 2), shown).toBe("h1");
}
