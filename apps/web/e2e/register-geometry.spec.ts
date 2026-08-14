import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { installRegisterApi } from "./support/api";
import { measureRegister, REGISTER_CASES } from "./support/registers";
import type { RegisterCase } from "./support/registers";

const VIEWPORTS = [
  { name: "narrow", width: 320, height: 800 },
  { name: "desktop", width: 1280, height: 900 },
] as const;

async function expectFirstFilterReachable(
  page: Page,
  firstFilter: NonNullable<RegisterCase["firstFilter"]>,
): Promise<void> {
  const filterOptions = firstFilter.name ? { name: firstFilter.name, exact: true } : undefined;
  const filter = page.getByRole(firstFilter.role, filterOptions);
  await expect(filter).toBeVisible();

  if (firstFilter.role === "textbox") {
    await filter.click();
    const listbox = page.getByRole("listbox");
    await expect(listbox).toBeVisible();
    await expect(listbox.getByRole("option").first()).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(listbox).toBeHidden();
    return;
  }

  const firstRadio = filter.getByRole("radio", {
    name: firstFilter.firstOptionName,
    exact: true,
  });
  await filter.getByText(firstFilter.firstOptionName, { exact: true }).click();
  await expect(firstRadio).toBeChecked();
}

async function addSecondOverflowOwner(page: Page): Promise<void> {
  await page.locator("table:visible").evaluate((table) => {
    let owner = table.parentElement;
    while (owner) {
      const overflowX = getComputedStyle(owner).overflowX;
      if (overflowX === "auto" || overflowX === "scroll") break;
      owner = owner.parentElement;
    }
    if (!owner?.parentElement)
      throw new Error("Expected the register overflow owner to have a parent");
    const secondOwner = owner.parentElement;
    secondOwner.style.overflowX = "auto";
    const overflowMarker = document.createElement("div");
    overflowMarker.style.width = `${owner.scrollWidth + 100}px`;
    overflowMarker.style.height = "1px";
    secondOwner.append(overflowMarker);
  });
}

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
          await expectFirstFilterReachable(page, registerCase.firstFilter);
        }

        if (registerCase.key === "tasks") {
          await addSecondOverflowOwner(page);
          await expect(measureRegister(page, registerCase)).rejects.toThrow(
            "Expected exactly one localized horizontal overflow container",
          );
        }
      } else {
        expect(geometry.searchWidth).toBeGreaterThanOrEqual(259);
        expect(geometry.searchWidth).toBeLessThanOrEqual(261);
        await expect(page.getByRole("columnheader")).toHaveText(registerCase.headers);
      }
    });
  }
}
