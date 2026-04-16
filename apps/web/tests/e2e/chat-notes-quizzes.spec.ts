import { expect, test } from "@playwright/test";
import { requireLiveE2EEnvironment, signIntoClerk } from "./helpers";

test.describe("Tutor, notes, and quizzes journeys", () => {
  test.skip(!requireLiveE2EEnvironment(), "Set live Clerk credentials to run tutor/note/quiz browser tests.");

  test("opens the tutor chat workspace", async ({ page }) => {
    await signIntoClerk(page);

    await page.goto("/chat");
    await expect(page.getByText(/Grounded answers with visible evidence/i)).toBeVisible();
    await expect(page.getByRole("button", { name: /send question/i })).toBeVisible();
  });

  test("opens the notes workspace", async ({ page }) => {
    await signIntoClerk(page);

    await page.goto("/notes");
    await expect(page.getByText(/Saved answers and learner memory/i)).toBeVisible();
    await expect(page.getByRole("button", { name: /save note/i })).toBeVisible();
  });

  test("opens the quizzes workspace", async ({ page }) => {
    await signIntoClerk(page);

    await page.goto("/quizzes");
    await expect(page.getByText(/Practice built from cited evidence/i)).toBeVisible();
    await expect(page.getByRole("button", { name: /generate quiz/i })).toBeVisible();
  });
});
