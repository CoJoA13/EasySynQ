// Guard the brand/static assets referenced by root-absolute URL in code + index.html.
// vite serves these from apps/web/public/ (copied verbatim into dist/ at build), but nothing
// else in the gate fails when a referenced file is missing or misplaced — an <img src> or
// favicon link 404s at runtime with eslint/tsc/build/tests all green. Keep this list in sync
// with every `/...` asset reference added to the app.
//
// import.meta.glob (vite-native, browser-typed via vite/client — the web tsconfig deliberately
// has no node types) enumerates public/ at transform time; a missing file is a missing key.
//
// The pattern is `**`, not `*`. A single star does not cross a path separator, so while public/
// was flat the two behaved identically — and the moment an asset landed in a SUBDIRECTORY
// (public/fonts/), `*` silently stopped seeing it. The failure mode is the one this file exists
// to prevent: the asset is present, the glob returns nothing, and the guard reports a missing
// file that is really there (or, with the entry absent, guards nothing at all).
import { expect, test } from "vitest";

const PUBLIC_FILES = Object.keys(
  import.meta.glob("../../public/**", { query: "?url", import: "default" }),
).map((p) => p.replace("../../public/", ""));

const REFERENCED_ASSETS = [
  "favicon.svg", // index.html
  "favicon.ico", // index.html
  "apple-touch-icon.png", // index.html
  "easysynq-mark.svg", // App.tsx interstitial + SetupWizard.tsx
  "easysynq-mark-simple.svg", // TopBar.tsx (heavier strokes for the 22px size)
  // Referenced from CSS (@font-face src in src/theme/fonts.css) rather than from TSX. That is
  // exactly why they belong here: a CSS url() 404s silently with the whole gate green, and the
  // page then renders in the fallback stack looking almost — but not quite — right.
  "fonts/archivo-latin.woff2",
  "fonts/archivo-latin-ext.woff2",
  "fonts/OFL.txt", // the SIL OFL requires the licence to ship with the font
];

for (const asset of REFERENCED_ASSETS) {
  test(`public/${asset} exists (referenced by root-absolute URL)`, () => {
    expect(PUBLIC_FILES).toContain(asset);
  });
}
