import { describe, expect, it } from "vitest";

// A source-text label contract, in the idiom of ./registerHeaderAdoption.test.ts.
//
// Three files must agree on the name of a shell destination — the rail entry the reader clicks,
// the breadcrumb crumb for the same path, and the document-title map that names the tab — and
// nothing tied them together. S-ui-5a is the proof that this matters: it renamed the rail entry
// and the title map, its commit message stated that the breadcrumb moved with them, and the
// breadcrumb had in fact not been touched. The surface carried three different names for a whole
// slice with every suite green, because there is no behaviour to observe — each file is
// individually correct and only their DISAGREEMENT is the defect.
//
// The registers deliberately stay out of this. Their page headings run at several levels and are
// tracked separately (RES-REGISTER-HEADING-LEVELS); what is pinned here is only the rail →
// breadcrumb → title triple, which must be one string per destination.
const sources = import.meta.glob(
  ["../app/shell/LeftRail.tsx", "../app/shell/Breadcrumb.tsx", "./effectiveView.ts"],
  { eager: true, query: "?raw", import: "default" },
) as Record<string, string>;

// Path literals contain `/` and `-`; escape before embedding one in a RegExp.
const rx = (literal: string): string => literal.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

const src = (suffix: string): string => {
  const hit = Object.entries(sources).find(([path]) => path.endsWith(suffix));
  if (!hit) throw new Error(`no source loaded for ${suffix}`);
  return hit[1];
};

// path → the one label every shell surface must use for it. Extend when a destination is renamed.
const DESTINATIONS: ReadonlyArray<readonly [string, string, string]> = [
  // [route path, breadcrumb segment key, the agreed label]
  ["/reports/document-control", "document-control", "Master document list"],
];

describe("shell destinations carry one label across rail, breadcrumb and title map", () => {
  for (const [path, segment, label] of DESTINATIONS) {
    it(`${path} is "${label}" in all three`, () => {
      // The rail: the NAV entry whose `to` is this path must ITSELF carry this label. Extract that
      // one entry first — `[^{}]*` cannot cross a brace, so the match is bounded to a single object
      // literal — because two independent whole-file `toContain` calls would also pass when the
      // label had moved to a DIFFERENT rail entry, which is the drift this file exists to catch.
      const railEntry = new RegExp(`\\{[^{}]*to: "${rx(path)}",[^{}]*\\}`, "s").exec(
        src("LeftRail.tsx"),
      );
      expect(railEntry, `no rail NAV entry with to: "${path}"`).not.toBeNull();
      expect(railEntry![0]).toContain(`label: "${label}",`);

      // The breadcrumb: keyed by the last path segment, not the full path.
      expect(src("Breadcrumb.tsx")).toContain(`"${segment}": "${label}",`);

      // The document-title map: a [path, label] tuple.
      expect(src("effectiveView.ts")).toContain(`["${path}", "${label}"],`);
    });

    it(`${path} carries no superseded label`, () => {
      // The failure this file exists for is a PARTIAL rename, so also assert the old names are
      // gone from all three. Without this, adding the new label beside a stale one passes above.
      for (const stale of [
        "Document register",
        "Controlled register",
        "Controlled document register",
      ]) {
        expect(src("LeftRail.tsx")).not.toContain(`"${stale}"`);
        expect(src("Breadcrumb.tsx")).not.toContain(`"${stale}"`);
        expect(src("effectiveView.ts")).not.toContain(`"${stale}"`);
      }
    });
  }
});
