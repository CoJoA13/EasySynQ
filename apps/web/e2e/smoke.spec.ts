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
