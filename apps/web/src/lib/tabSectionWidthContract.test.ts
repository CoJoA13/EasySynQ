import { describe, expect, it } from "vitest";

// A source-text contract over the three tabbed sections, in the idiom of ./registerHeaderAdoption.test.ts.
//
// It exists because the defect it guards had a passing test. `CapaLayout` sized its tab strip
// `tab === "board" ? "xl" : "lg"`, which kept the strip aligned with each face's own content — and
// `CapaLayout.test.tsx` asserted exactly that, one face at a time. What no per-face assertion could
// see is that a Mantine Container is CENTRED, so `lg` and `xl` differing by 180px moved the strip
// 90px sideways every time the user changed tab. It was reported from the running application, not
// by a gate, after living in three green tests.
//
// The invariant is therefore about the SET, not any one member: within a tabbed section, the layout
// and every face it can show must agree on one container width. `AuditsLayout` (all `xl`) and
// `DriftLayout` (all `lg`) already did; CAPA was the outlier, and the two conforming sections are
// what this pins so they cannot drift into the same shape.
const sources = import.meta.glob(
  [
    "../features/capa/CapaLayout.tsx",
    "../features/capa/CapaBoardPage.tsx",
    "../features/capa/ComplaintsPage.tsx",
    "../features/capa/NcrsPage.tsx",
    "../features/audits/AuditsLayout.tsx",
    "../features/audits/AuditsListPage.tsx",
    "../features/audits/ProgramPage.tsx",
    "../features/drift/DriftLayout.tsx",
    "../features/drift/DriftStatusPage.tsx",
    "../features/drift/SupersededCopiesPage.tsx",
  ],
  { eager: true, query: "?raw", import: "default" },
) as Record<string, string>;

const SECTIONS = [
  {
    name: "/capa",
    width: "xl",
    layout: "features/capa/CapaLayout.tsx",
    faces: [
      "features/capa/CapaBoardPage.tsx",
      "features/capa/ComplaintsPage.tsx",
      "features/capa/NcrsPage.tsx",
    ],
  },
  {
    name: "/audits",
    width: "xl",
    layout: "features/audits/AuditsLayout.tsx",
    faces: ["features/audits/AuditsListPage.tsx", "features/audits/ProgramPage.tsx"],
  },
  {
    name: "/drift",
    width: "lg",
    layout: "features/drift/DriftLayout.tsx",
    faces: ["features/drift/DriftStatusPage.tsx", "features/drift/SupersededCopiesPage.tsx"],
  },
] as const;

function sourceFor(path: string): string {
  const hit = Object.entries(sources).find(([key]) => key.endsWith(path))?.[1];
  if (typeof hit !== "string") throw new Error(`Missing source: ${path}`);
  return hit;
}

/**
 * Every `<Container …>` opening tag in a file, brace-aware, with comments stripped.
 *
 * Brace-aware because `[^>]*?` terminates at the first `>` in the source, which inside a prop like
 * `onClick={() => …}` is the arrow rather than the end of the tag — the tag then appears to carry
 * no `size` and drops silently out of every check below.
 */
function containerTags(source: string): string[] {
  const code = source.replace(/\/\*[\s\S]*?\*\/|\/\/[^\n]*/g, "");
  const tags: string[] = [];
  for (let i = code.indexOf("<Container"); i !== -1; i = code.indexOf("<Container", i + 1)) {
    const next = code[i + "<Container".length];
    if (next !== undefined && !/[\s/>]/.test(next)) continue;
    let depth = 0;
    let j = i + "<Container".length;
    for (; j < code.length; j += 1) {
      const c = code[j];
      if (c === "{") depth += 1;
      else if (c === "}") depth -= 1;
      else if (c === ">" && depth === 0) break;
    }
    tags.push(code.slice(i, j + 1));
  }
  return tags;
}

/** The literal widths a file declares. */
function containerWidths(source: string): string[] {
  return containerTags(source)
    .map((tag) => /\bsize="([a-z]+)"/.exec(tag)?.[1])
    .filter((w): w is string => w !== undefined);
}

/**
 * A `<Container>` this contract cannot read: `size` as an expression, or NO `size` at all.
 *
 * Both were live holes. The computed check was applied only to the layout, so reintroducing the
 * exact defect expression on a FACE passed — which matters because the branch discrepancy this
 * slice closed was on a face, not a layout. And a `<Container>` with no `size` silently takes
 * Mantine's `md` default (960px), so a face could drop 360px below its siblings while every
 * assertion here stayed green, because an unreadable tag contributed nothing to compare. Both
 * mutations were run against the previous version and both passed.
 */
function unreadableContainers(source: string): string[] {
  return containerTags(source).filter((tag) => !/\bsize="[a-z]+"/.test(tag));
}

describe("tabbed section container width contract", () => {
  it.each(SECTIONS)(
    "$name sizes every container with a literal, never an expression",
    (section) => {
      // Applied to the FACES as well as the layout. A computed size is what let the width follow the
      // active tab, and an absent one is worse — it takes Mantine's md default without saying so.
      const files = [section.layout, ...section.faces];
      const offenders = files.flatMap((f) =>
        unreadableContainers(sourceFor(f)).map((tag) => `${f}: ${tag.replace(/\s+/g, " ").trim()}`),
      );
      expect(offenders).toEqual([]);
    },
  );

  it.each(SECTIONS)("$name agrees on one container width across the strip and every face", (s) => {
    const widths = [sourceFor(s.layout), ...s.faces.map(sourceFor)].flatMap(containerWidths);
    // Every branch of every face counts, not just the loaded one: CapaBoardPage rendered `md` in
    // its forbidden, error and loading branches against `xl` when loaded, so the board jumped width
    // against ITSELF as well as against its siblings.
    expect(widths.length).toBeGreaterThanOrEqual(1 + s.faces.length);
    expect([...new Set(widths)]).toEqual([s.width]);
  });
});
