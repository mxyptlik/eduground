import { expect, test } from "@playwright/test";
import { requireLiveE2EEnvironment, signIntoClerk } from "./helpers";

test.describe("Clerk onboarding", () => {
  test.skip(!requireLiveE2EEnvironment(), "Set PLAYWRIGHT_BASE_URL, PLAYWRIGHT_CLERK_EMAIL, and PLAYWRIGHT_CLERK_PASSWORD to run live onboarding.");

  test("signs in and reaches the workspace shell", async ({ page }) => {
    await signIntoClerk(page);

    await page.goto("/");
    await expect(page.getByText(/Grounded learning with clear citations/i)).toBeVisible();
    await expect(page.getByText(/Curriculum tutor workspace/i)).toBeVisible();
  });
});
