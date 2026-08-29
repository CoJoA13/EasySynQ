// WCAG contrast regression gate for tokens.css (S-ui-1, plan §4).
//
// This exists because the muted-text defect was invisible to every other gate: eslint, tsc, the
// build and 271 test files were all green while `c="dimmed"` rendered below AA on every surface in
// both schemes. A palette has no compiler, so this file is its type-checker. It parses the token
// file itself — not a copy of the values — so a future "just nudge that grey" edit fails here.
//
// The matrix is the FULL cartesian product of foreground tokens x surface tokens. There is
// deliberately no exclusion list: an exclusion is where the next regression would hide, and the
// shipped palette clears the bar without needing one.
import { expect, describe, test } from "vitest";
import tokensCss from "./tokens.css?raw";

const AA_TEXT = 4.5; // WCAG 2.2 SC 1.4.3, normal-size text
const AA_NON_TEXT = 3.0; // SC 1.4.11, UI component boundaries

/** Return the declaration body of the rule introduced by `selector`, matching braces. */
function block(css: string, selector: string): string {
  const start = css.indexOf(selector);
  if (start === -1) throw new Error(`selector not found: ${selector}`);
  const open = css.indexOf("{", start);
  let depth = 0;
  for (let i = open; i < css.length; i += 1) {
    if (css[i] === "{") depth += 1;
    else if (css[i] === "}") {
      depth -= 1;
      if (depth === 0) return css.slice(open + 1, i);
    }
  }
  throw new Error(`unterminated block: ${selector}`);
}

function customProperties(body: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [, name, value] of body.matchAll(/(--es-[a-z0-9-]+)\s*:\s*([^;]+);/g)) {
    out[name!.trim()] = value!.trim();
  }
  return out;
}

const lightBody = block(tokensCss, ":root {");
const darkBody = block(tokensCss, ':root[data-mantine-color-scheme="dark"] {');
const light = customProperties(lightBody);
// Dark re-keys only some tokens; anything it does not restate is inherited from light.
const dark = { ...light, ...customProperties(darkBody) };

function channel(v: number): number {
  const c = v / 255;
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

function luminance(hex: string): number {
  const m = /^#([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) throw new Error(`not a 6-digit hex colour: ${hex}`);
  const n = parseInt(m[1]!, 16);
  return (
    0.2126 * channel((n >> 16) & 0xff) +
    0.7152 * channel((n >> 8) & 0xff) +
    0.0722 * channel(n & 0xff)
  );
}

function contrast(a: string, b: string): number {
  const [la, lb] = [luminance(a), luminance(b)];
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

/** Round down to 2dp so a reported figure never overstates the measured ratio. */
const floor2 = (n: number) => Math.floor(n * 100) / 100;

const SURFACES = ["--es-bg", "--es-surface", "--es-surface-2", "--es-surface-3", "--es-sidebar"];

const FOREGROUNDS = [
  "--es-text",
  "--es-text-2",
  "--es-text-muted",
  "--es-accent-text",
  "--es-success-text",
  "--es-warning-text",
  "--es-danger-text",
  "--es-info-text",
  "--es-plan-text",
  "--es-do-text",
  "--es-check-text",
  "--es-act-text",
];

/** Foreground token paired with the tint it was designed to sit on. */
const TINT_PAIRS: ReadonlyArray<readonly [string, string]> = [
  ["--es-accent-text", "--es-accent-soft"],
  ["--es-accent-text", "--es-accent-soft-2"],
  ["--es-success-text", "--es-success-soft"],
  ["--es-warning-text", "--es-warning-soft"],
  ["--es-danger-text", "--es-danger-soft"],
  ["--es-info-text", "--es-info-soft"],
  ["--es-plan-text", "--es-plan-soft"],
  ["--es-do-text", "--es-do-soft"],
  ["--es-check-text", "--es-check-soft"],
  ["--es-act-text", "--es-act-soft"],
  ["--es-add-text", "--es-add-bg"],
  ["--es-del-text", "--es-del-bg"],
];

/** Text/icon on a solid accent fill. */
const FILL_PAIRS: ReadonlyArray<readonly [string, string]> = [
  ["--es-on-accent", "--es-accent"],
  ["--es-on-accent", "--es-accent-hover"],
  ["--es-on-accent", "--es-accent-active"],
];

const SCHEMES = [
  ["light", light],
  ["dark", dark],
] as const;

describe.each(SCHEMES)("%s scheme", (_scheme, tokens) => {
  test.each(FOREGROUNDS)("%s clears AA on every surface", (fg) => {
    for (const surface of SURFACES) {
      const ratio = contrast(tokens[fg]!, tokens[surface]!);
      expect(
        floor2(ratio),
        `${fg} (${tokens[fg]}) on ${surface} (${tokens[surface]})`,
      ).toBeGreaterThanOrEqual(AA_TEXT);
    }
  });

  test.each(TINT_PAIRS)("%s clears AA on %s", (fg, bg) => {
    const ratio = contrast(tokens[fg]!, tokens[bg]!);
    expect(floor2(ratio), `${fg} (${tokens[fg]}) on ${bg} (${tokens[bg]})`).toBeGreaterThanOrEqual(
      AA_TEXT,
    );
  });

  test.each(FILL_PAIRS)("%s clears AA on %s", (fg, bg) => {
    const ratio = contrast(tokens[fg]!, tokens[bg]!);
    expect(floor2(ratio), `${fg} (${tokens[fg]}) on ${bg} (${tokens[bg]})`).toBeGreaterThanOrEqual(
      AA_TEXT,
    );
  });

  test("the PAINTED focus indicator clears 3:1 on the surfaces it is drawn against", () => {
    // Deliberately resolves --es-focus-ring, the value index.css actually paints as a box-shadow.
    // An earlier version of this test asserted --es-focus, which NOTHING reads — it passed while
    // the real indicator sat at 1.59:1, i.e. it certified the opposite of the truth. A translucent
    // ring is the trap: rgba(10,122,111,0.32) looks like a strong teal and composites to a pale
    // wash. So the alpha case is composited here rather than excluded.
    const ring = tokens["--es-focus-ring"]!;
    const varRef = /var\((--es-[a-z0-9-]+)\)/.exec(ring);
    const rgba = /rgba?\(\s*(\d+)[ ,]+(\d+)[ ,]+(\d+)\s*(?:[,/]\s*([\d.]+))?\s*\)/.exec(ring);

    for (const surface of ["--es-bg", "--es-surface"]) {
      const bg = tokens[surface]!;
      let painted: string;
      if (varRef) {
        painted = tokens[varRef[1]!]!;
      } else if (rgba) {
        const a = rgba[4] === undefined ? 1 : Number(rgba[4]);
        const over = [1, 2, 3].map((i) => {
          const fg = Number(rgba[i]);
          const b = parseInt(bg.slice(1 + (i - 1) * 2, 3 + (i - 1) * 2), 16);
          return Math.round(a * fg + (1 - a) * b);
        });
        painted = "#" + over.map((c) => c.toString(16).padStart(2, "0")).join("");
      } else {
        throw new Error(`--es-focus-ring has no resolvable colour: ${ring}`);
      }
      expect(
        floor2(contrast(painted, bg)),
        `painted focus indicator ${painted} (from ${ring}) on ${surface} (${bg})`,
      ).toBeGreaterThanOrEqual(AA_NON_TEXT);
    }
  });

  test("the recess ladder deepens monotonically and never collapses", () => {
    // Deliberately NOT a fixed luminance ordering: the two schemes order differently by design.
    // In light, --es-surface is white and each recess step darkens; in dark, --es-surface sits
    // ABOVE --es-bg and each recess step lightens. (--es-sidebar equals --es-surface in light,
    // so a blanket "all surfaces distinct" assertion would be wrong too.) The scheme-agnostic
    // invariant is the one that actually matters: recess steps move away from --es-surface, in a
    // consistent direction, by a growing amount.
    const base = luminance(tokens["--es-surface"]!);
    const step2 = luminance(tokens["--es-surface-2"]!) - base;
    const step3 = luminance(tokens["--es-surface-3"]!) - base;
    const label = `surface=${tokens["--es-surface"]} surface-2=${tokens["--es-surface-2"]} surface-3=${tokens["--es-surface-3"]}`;

    expect(Math.sign(step2), `surface-2 does not recede from surface — ${label}`).not.toBe(0);
    expect(Math.sign(step3), `surface-3 recedes the opposite way from surface-2 — ${label}`).toBe(
      Math.sign(step2),
    );
    expect(
      Math.abs(step3),
      `surface-3 is not a deeper recess than surface-2 — ${label}`,
    ).toBeGreaterThan(Math.abs(step2));
  });
});

describe("tint coverage", () => {
  test("every tint token in the file is actually exercised by a pair", () => {
    // The header above claims there is no exclusion list. That claim was untrue when written:
    // --es-accent-soft-2 had no pair, and it was sitting at 4.35:1 against --es-accent-text in
    // light (6.26:1 before this slice) — a regression the gate silently permitted. A prose promise
    // of completeness is worth nothing; this makes it mechanical, so a NEW tint token fails here
    // until someone states what foreground is allowed on it.
    const covered = new Set(TINT_PAIRS.map(([, bg]) => bg));
    const tintTokens = Object.keys(light).filter(
      (k) => /-soft(-2)?$/.test(k) || k === "--es-add-bg" || k === "--es-del-bg",
    );
    expect(tintTokens.length, "no tint tokens found — the parser probably broke").toBeGreaterThan(
      5,
    );
    for (const tint of tintTokens) {
      expect(
        covered,
        `${tint} is defined but no TINT_PAIRS entry puts a foreground on it`,
      ).toContain(tint);
    }
  });
});

describe("Mantine interop", () => {
  test("--mantine-color-dimmed is remapped onto the token in BOTH schemes", () => {
    // The whole point of the remap: `c="dimmed"` has ~331 call sites and --es-text-muted had 4,
    // so fixing the token without this line fixes almost nothing.
    const remap = block(tokensCss, ':root,\n:root[data-mantine-color-scheme="light"],');
    expect(remap).toContain("--mantine-color-dimmed: var(--es-text-muted)");
  });

  test("the remap matches Mantine's own specificity for each scheme", () => {
    // Mantine declares --mantine-color-dimmed under :root[data-mantine-color-scheme='light'] and
    // ...='dark', both (0,2,0). A bare :root override is (0,1,0) and LOSES to them, which would
    // leave every light-mode call site still failing. Pin the scheme-qualified selectors so that
    // subtlety cannot be "simplified" away.
    const selector = tokensCss.slice(
      tokensCss.indexOf(':root,\n:root[data-mantine-color-scheme="light"],'),
      tokensCss.indexOf("--mantine-color-dimmed"),
    );
    expect(selector).toContain(':root[data-mantine-color-scheme="light"]');
    expect(selector).toContain(':root[data-mantine-color-scheme="dark"]');
  });
});
