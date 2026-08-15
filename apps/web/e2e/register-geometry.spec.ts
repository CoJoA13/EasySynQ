import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import {
  installRegisterApi,
  MAXIMUM_EVIDENCE_FILENAME,
  MAXIMUM_RECORD_SEARCH,
  MAXIMUM_RECORD_TITLE,
} from "./support/api";
import { measureRegister, REGISTER_CASES } from "./support/registers";
import type { RegisterCase } from "./support/registers";

const VIEWPORTS = [
  { name: "narrow", width: 320, height: 800 },
  { name: "desktop", width: 1280, height: 900 },
] as const;

const RECORD_ID = "re000001-0001-0001-0001-000000000001";
const RECORDS_CASE = REGISTER_CASES.find((registerCase) => registerCase.key === "records");

if (!RECORDS_CASE) throw new Error("Records register manifest is required");

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

test("records stacks its toolbar above one localized scroll owner at 320 pixels", async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await installRegisterApi(page, { route: "records" });
  await page.goto("/records");

  const controls = [
    page.getByRole("searchbox", { name: "Search records", exact: true }),
    page.getByRole("textbox", { name: "Record type", exact: true }),
    page.getByRole("textbox", { name: "Disposition", exact: true }),
    page.getByRole("textbox", { name: "Legal hold", exact: true }),
    page.getByRole("textbox", { name: "Source document", exact: true }),
    page.getByRole("textbox", { name: "Captured by", exact: true }),
  ];
  await expect(page.getByRole("link", { name: "Open record REC-000041" })).toBeVisible();
  const boxes = await Promise.all(
    controls.map(async (control) => {
      await expect(control).toBeVisible();
      return control.boundingBox();
    }),
  );
  expect(boxes.every((box) => box !== null)).toBe(true);

  const firstBox = boxes[0]!;
  for (const [index, box] of boxes.entries()) {
    expect(
      Math.abs(box!.x - firstBox.x),
      `control ${index} horizontal alignment`,
    ).toBeLessThanOrEqual(1);
    expect(Math.abs(box!.width - firstBox.width), `control ${index} width`).toBeLessThanOrEqual(1);
    if (index > 0) expect(box!.y).toBeGreaterThan(boxes[index - 1]!.y);
  }

  const geometry = await measureRegister(page, RECORDS_CASE);
  expect(geometry.documentScrollWidth - geometry.documentClientWidth).toBeLessThanOrEqual(1);
  expect(geometry.containerScrollWidth).toBeGreaterThan(geometry.containerClientWidth);
});

test("records bounds maximum dynamic labels and actions at 320 pixels", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await installRegisterApi(page, { route: "records", maxContent: true });
  await page.goto(`/records?q=${MAXIMUM_RECORD_SEARCH}`);

  const filterChip = page.getByRole("button", {
    name: `Remove filter Search: ${MAXIMUM_RECORD_SEARCH}`,
    exact: true,
  });
  const next = page.getByRole("button", { name: "Next records page", exact: true });
  await expect(filterChip).toBeVisible();
  await expect(next).toBeVisible();
  for (const control of [filterChip, next]) {
    const box = await control.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.height).toBeGreaterThanOrEqual(44);
    expect(box!.x).toBeGreaterThanOrEqual(-1);
    expect(box!.x + box!.width).toBeLessThanOrEqual(321);
  }
  expect(
    await filterChip
      .locator("span")
      .last()
      .evaluate((label) => {
        const styles = getComputedStyle(label);
        return {
          overflow: styles.overflow,
          textOverflow: styles.textOverflow,
          whiteSpace: styles.whiteSpace,
        };
      }),
  ).toEqual({ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    ),
  ).toBeLessThanOrEqual(1);

  await page.goto(`/records/${RECORD_ID}`);
  const title = page.getByRole("heading", { name: MAXIMUM_RECORD_TITLE, exact: true });
  await expect(title).toBeVisible();
  expect(await title.evaluate((heading) => getComputedStyle(heading).overflowWrap)).toBe(
    "anywhere",
  );

  const download = page.getByRole("button", {
    name: `Download ${MAXIMUM_EVIDENCE_FILENAME}`,
    exact: true,
  });
  await expect(download).toBeVisible();
  const downloadBox = await download.boundingBox();
  expect(downloadBox).not.toBeNull();
  expect(downloadBox!.height).toBeGreaterThanOrEqual(44);
  expect(downloadBox!.x).toBeGreaterThanOrEqual(-1);
  expect(downloadBox!.x + downloadBox!.width).toBeLessThanOrEqual(321);
  expect(
    await download.locator("span.mantine-Text-root").evaluate((label) => {
      const styles = getComputedStyle(label);
      return {
        overflow: styles.overflow,
        textOverflow: styles.textOverflow,
        whiteSpace: styles.whiteSpace,
      };
    }),
  ).toEqual({ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    ),
  ).toBeLessThanOrEqual(1);
});

for (const viewport of VIEWPORTS) {
  test(`records detail uses its ${viewport.name} section layout without document overflow`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    await installRegisterApi(page, { route: "records" });
    await page.goto(`/records/${RECORD_ID}`);

    await expect(
      page.getByRole("heading", { name: "Preventive-maintenance schedule" }),
    ).toBeVisible();
    const provenance = page.getByRole("region", { name: "Provenance" });
    const lifecycle = page.getByRole("region", { name: "Lifecycle" });
    const [provenanceBox, lifecycleBox, documentGeometry] = await Promise.all([
      provenance.boundingBox(),
      lifecycle.boundingBox(),
      page.evaluate(() => ({
        clientWidth: document.documentElement.clientWidth,
        scrollWidth: document.documentElement.scrollWidth,
      })),
    ]);
    expect(provenanceBox).not.toBeNull();
    expect(lifecycleBox).not.toBeNull();
    expect(documentGeometry.scrollWidth - documentGeometry.clientWidth).toBeLessThanOrEqual(1);

    if (viewport.name === "narrow") {
      expect(Math.abs(provenanceBox!.x - lifecycleBox!.x)).toBeLessThanOrEqual(1);
      expect(lifecycleBox!.y).toBeGreaterThanOrEqual(provenanceBox!.y + provenanceBox!.height - 1);
    } else {
      expect(Math.abs(provenanceBox!.y - lifecycleBox!.y)).toBeLessThanOrEqual(1);
      expect(lifecycleBox!.x).toBeGreaterThanOrEqual(provenanceBox!.x + provenanceBox!.width - 1);
    }
  });
}
