import { expect, test } from "vitest";
import { theme } from "./mantine";

test("theme uses the mockup's system font tokens + indigo primary", () => {
  expect(theme.fontFamily).toBe("var(--es-font-sans)");
  expect(theme.fontFamilyMonospace).toBe("var(--es-font-mono)");
  expect(theme.primaryColor).toBe("indigo");
});
