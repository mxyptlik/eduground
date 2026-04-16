import { expect, type Page } from "@playwright/test";

export function requireLiveE2EEnvironment() {
  return Boolean(
    process.env.PLAYWRIGHT_BASE_URL &&
      process.env.PLAYWRIGHT_CLERK_EMAIL &&
      process.env.PLAYWRIGHT_CLERK_PASSWORD,
  );
}

export async function signIntoClerk(page: Page) {
  await page.goto("/auth");
  await expect(page.getByText(/Create account|Sign in/i)).toBeVisible();

  await page.getByRole("button", { name: /sign in/i }).click();
  await page.getByLabel(/email/i).fill(process.env.PLAYWRIGHT_CLERK_EMAIL ?? "");
  await page.getByLabel(/password/i).fill(process.env.PLAYWRIGHT_CLERK_PASSWORD ?? "");
  await page.getByRole("button", { name: /continue|sign in/i }).click();
}
