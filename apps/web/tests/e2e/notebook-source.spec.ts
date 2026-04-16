import { expect, test } from "@playwright/test";
import { requireLiveE2EEnvironment, signIntoClerk } from "./helpers";

test.describe("Notebook and source journeys", () => {
  test.skip(!requireLiveE2EEnvironment(), "Set live Clerk credentials to run notebook/source browser tests.");

  test("walks the notebook creation flow", async ({ page }) => {
    await signIntoClerk(page);

    await page.goto("/notebooks");
    await expect(page.getByText(/Create a new workspace/i)).toBeVisible();
    await expect(page.getByRole("button", { name: /add new notebook/i })).toBeVisible();
  });

  test("walks the source registration flow", async ({ page }) => {
    await signIntoClerk(page);

    await page.goto("/sources");
    await expect(page.getByText(/Add source/i)).toBeVisible();
    await expect(page.getByRole("button", { name: /add new source/i })).toBeVisible();
  });
});
