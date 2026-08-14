import { expect, test } from "@playwright/test";
import { installRegisterApi } from "./support/api";
import { measureRegister, REGISTER_CASES } from "./support/registers";
import type { RegisterCase } from "./support/registers";

const VIEWPORTS = [
  { name: "narrow", width: 320, height: 800 },
  { name: "desktop", width: 1280, height: 900 },
] as const;

for (const registerCase of REGISTER_CASES as readonly RegisterCase[]) {
  for (const viewport of VIEWPORTS) {
    test(`${registerCase.key} keeps register geometry localized at ${viewport.name}`, async ({
      page,
    }) => {
      await page.setViewportSize(viewport);
      await installRegisterApi(page, { route: registerCase.key });
      await page.goto(registerCase.path);

      const action = page.getByRole(registerCase.primaryAction.role, {
        name: registerCase.primaryAction.name,
        exact: true,
      });
      await expect(action).toHaveCount(1);

      const geometry = await measureRegister(page, registerCase);
      expect(geometry.documentScrollWidth - geometry.documentClientWidth).toBeLessThanOrEqual(1);

      if (viewport.name === "narrow") {
        expect(geometry.containerClientWidth).toBeLessThanOrEqual(geometry.documentClientWidth + 1);
        expect(geometry.containerScrollWidth).toBeGreaterThan(geometry.containerClientWidth);
        expect(geometry.containerScrollWidth).toBeGreaterThanOrEqual(registerCase.floor - 1);
        expect(geometry.tableWidth).toBeGreaterThanOrEqual(registerCase.floor - 1);
        expect(geometry.farEdgeInsideAfterScroll).toBe(true);
        expect(geometry.searchWidth).toBeGreaterThanOrEqual(geometry.containerClientWidth - 1);
        expect(geometry.searchWidth).toBeLessThanOrEqual(geometry.documentClientWidth + 1);

        await expect(
          page.getByRole("columnheader", { name: registerCase.finalHeader }),
        ).toHaveCount(1);
        if (registerCase.firstFilter) {
          const filterOptions = registerCase.firstFilter.name
            ? { name: registerCase.firstFilter.name, exact: true }
            : undefined;
          await expect(page.getByRole(registerCase.firstFilter.role, filterOptions)).toBeVisible();
        }
      } else {
        expect(geometry.searchWidth).toBeGreaterThanOrEqual(259);
        expect(geometry.searchWidth).toBeLessThanOrEqual(261);
        await expect(page.getByRole("columnheader")).toHaveText(registerCase.headers);
      }
    });
  }
}
