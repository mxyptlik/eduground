import { MemoryRouter, Route, Routes } from "react-router-dom";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RequireSession } from "@/lib/session/RequireSession";

const sessionState = {
  loading: false,
  isSignedIn: true,
  organizationId: "org_123" as string | null,
  authError: null as string | null,
  user: { id: "user-1" },
};

vi.mock("@/lib/session/AppSessionContext", () => ({
  useAppSession: () => sessionState,
}));

describe("RequireSession", () => {
  beforeEach(() => {
    sessionState.loading = false;
    sessionState.isSignedIn = true;
    sessionState.organizationId = "org_123";
    sessionState.authError = null;
    sessionState.user = { id: "user-1" };
  });

  function renderGuard() {
    render(
      <MemoryRouter initialEntries={["/private"]}>
        <Routes>
          <Route
            path="/private"
            element={
              <RequireSession>
                <div>Protected content</div>
              </RequireSession>
            }
          />
          <Route path="/auth" element={<div>Auth screen</div>} />
        </Routes>
      </MemoryRouter>,
    );
  }

  it("shows a loading message while the session is being resolved", () => {
    sessionState.loading = true;

    renderGuard();

    expect(screen.getByText("Checking your session...")).toBeInTheDocument();
  });

  it("redirects to auth when the user is signed out", () => {
    sessionState.isSignedIn = false;

    renderGuard();

    expect(screen.getByText("Auth screen")).toBeInTheDocument();
  });

  it("redirects to auth when an auth error exists", () => {
    sessionState.authError = "Clerk session expired";

    renderGuard();

    expect(screen.getByText("Auth screen")).toBeInTheDocument();
  });

  it("redirects to auth when the Clerk organization context is missing", () => {
    sessionState.organizationId = null;

    renderGuard();

    expect(screen.getByText("Auth screen")).toBeInTheDocument();
  });

  it("renders the protected route when the session is valid", () => {
    renderGuard();

    expect(screen.getByText("Protected content")).toBeInTheDocument();
  });
});
