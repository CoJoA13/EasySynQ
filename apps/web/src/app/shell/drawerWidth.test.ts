import { expect, test } from "vitest";
import { clampDrawerWidth, DRAWER_MIN, DRAWER_MAX, DRAWER_DEFAULT } from "./drawerWidth";

test("clampDrawerWidth holds the 360–640 range", () => {
  expect(clampDrawerWidth(420)).toBe(420);
  expect(clampDrawerWidth(100)).toBe(DRAWER_MIN);
  expect(clampDrawerWidth(9999)).toBe(DRAWER_MAX);
  expect(DRAWER_DEFAULT).toBe(420);
});
