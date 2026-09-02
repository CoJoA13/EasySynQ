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

/** Every `<Container size="…">` literal in a file, with comments stripped so prose cannot match. */
function containerWidths(source: string): string[] {
  const code = source.replace(/\/\*[\s\S]*?\*\/|\/\/[^\n]*/g, "");
  return [...code.matchAll(/<Container[^>]*?\bsize="([a-z]+)"/gs)].map((m) => m[1]!);
}

/** A `size` that is an expression rather than a literal — the exact shape that caused the jump. */
function hasComputedWidth(source: string): boolean {
  const code = source.replace(/\/\*[\s\S]*?\*\/|\/\/[^\n]*/g, "");
  return /<Container[^>]*?\bsize=\{/s.test(code);
}

describe("tabbed section container width contract", () => {
  it.each(SECTIONS)("$name sizes its tab strip with a literal, never an expression", (section) => {
    // A computed size is what let the width follow the active tab. Forbidding the SHAPE is what
    // makes this a guard against the defect rather than against one value of it.
    expect(hasComputedWidth(sourceFor(section.layout))).toBe(false);
  });

  it.each(SECTIONS)("$name agrees on one container width across the strip and every face", (s) => {
    const widths = [sourceFor(s.layout), ...s.faces.map(sourceFor)].flatMap(containerWidths);
    // Every branch of every face counts, not just the loaded one: CapaBoardPage rendered `md` in
    // its forbidden, error and loading branches against `xl` when loaded, so the board jumped width
    // against ITSELF as well as against its siblings.
    expect(widths.length).toBeGreaterThanOrEqual(1 + s.faces.length);
    expect([...new Set(widths)]).toEqual([s.width]);
  });
});
