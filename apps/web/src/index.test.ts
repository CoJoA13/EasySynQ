import { expect, test } from "vitest";
import css from "./index.css?raw";

test("forced-colors mode restores a system-colour focus outline", () => {
  const start = css.indexOf("@media (forced-colors: active)");
  const end = css.indexOf("@media (prefers-reduced-motion: reduce)");
  expect(start).toBeGreaterThanOrEqual(0);
  expect(end).toBeGreaterThan(start);

  const forcedColors = css.slice(start, end);
  expect(forcedColors).toContain(":focus-visible");
  expect(forcedColors).toContain("outline: 2px solid Highlight");
  expect(forcedColors).toContain("outline-offset: 2px");
  expect(forcedColors).toContain("box-shadow: none");
});
