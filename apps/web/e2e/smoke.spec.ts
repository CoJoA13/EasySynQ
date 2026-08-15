import { expect, test } from "@playwright/test";
import { installRegisterApi } from "./support/api";

test("mounts the real routed shell with deterministic authenticated data", async ({ page }) => {
  await installRegisterApi(page, { route: "tasks" });
  await page.goto("/tasks");

  await expect(page).toHaveTitle(/Tasks/);
  await expect(page.getByRole("heading", { name: "Review and approve" })).toBeVisible();
  await expect(page.getByRole("table", { name: "My tasks" })).toHaveCount(1);
  await expect(page.getByRole("link", { name: /SOP-PUR-014/ })).toHaveCount(1);
});

test("mounts the Records register and populated detail through the real routed shell", async ({
  page,
}) => {
  await installRegisterApi(page, { route: "records" });
  await page.goto("/records");

  const recordLink = page.getByRole("link", { name: "Open record REC-000041" });
  await expect(page).toHaveTitle(/Records/);
  await expect(recordLink).toHaveCount(1);
  await recordLink.click();

  await expect(page).toHaveURL(/\/records\/re000001-0001-0001-0001-000000000001$/);
  await expect(
    page.getByRole("heading", { name: "Preventive-maintenance schedule" }),
  ).toBeVisible();
  await expect(page.getByRole("region", { name: "Provenance" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Lifecycle" })).toBeVisible();
});
