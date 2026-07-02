// Guard the brand/static assets referenced by root-absolute URL in code + index.html.
// vite serves these from apps/web/public/ (copied verbatim into dist/ at build), but nothing
// else in the gate fails when a referenced file is missing or misplaced — an <img src> or
// favicon link 404s at runtime with eslint/tsc/build/tests all green. Keep this list in sync
// with every `/...` asset reference added to the app.
//
// import.meta.glob (vite-native, browser-typed via vite/client — the web tsconfig deliberately
// has no node types) enumerates public/ at transform time; a missing file is a missing key.
import { expect, test } from "vitest";

const PUBLIC_FILES = Object.keys(
  import.meta.glob("../../public/*", { query: "?url", import: "default" }),
).map((p) => p.replace("../../public/", ""));

const REFERENCED_ASSETS = [
  "favicon.svg", // index.html
  "favicon.ico", // index.html
  "apple-touch-icon.png", // index.html
  "easysynq-mark.svg", // App.tsx interstitial + SetupWizard.tsx
  "easysynq-mark-simple.svg", // TopBar.tsx (heavier strokes for the 22px size)
];

for (const asset of REFERENCED_ASSETS) {
  test(`public/${asset} exists (referenced by root-absolute URL)`, () => {
    expect(PUBLIC_FILES).toContain(asset);
  });
}
