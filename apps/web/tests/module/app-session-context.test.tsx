import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiClient } from "@/lib/api/client";
import { AppSessionProvider, useAppSession } from "@/lib/session/AppSessionContext";

const clerkState = {
  auth: {
    isLoaded: true,
    isSignedIn: true,
    getToken: vi.fn(async () => "token-123"),
    orgId: "org_123" as string | null,
  },
  organization: {
    organization: { name: "Example University" } as { name: string } | null,
  },
  user: {
    user: { id: "clerk_user_123" } as { id: string } | null,
  },
  clerk: {
    signOut: vi.fn(async () => undefined),
  },
};

vi.mock("@clerk/clerk-react", () => ({
  useAuth: () => clerkState.auth,
  useOrganization: () => clerkState.organization,
  useUser: () => clerkState.user,
  useClerk: () => clerkState.clerk,
}));

function SessionProbe() {
  const session = useAppSession();

  return (
    <div>
      <div data-testid="loading">{String(session.loading)}</div>
      <div data-testid="auth-error">{session.authError ?? ""}</div>
      <div data-testid="selected-notebook">{session.selectedNotebookId ?? ""}</div>
      <div data-testid="notebook-count">{session.notebooks.length}</div>
      <div data-testid="organization-name">{session.organizationName ?? ""}</div>
      <div data-testid="user-email">{session.user?.email ?? ""}</div>
    </div>
  );
}

describe("AppSessionProvider", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    clerkState.auth.isLoaded = true;
    clerkState.auth.isSignedIn = true;
    clerkState.auth.orgId = "org_123";
    clerkState.organization.organization = { name: "Example University" };
    clerkState.user.user = { id: "clerk_user_123" };
    clerkState.auth.getToken.mockResolvedValue("token-123");
    clerkState.clerk.signOut.mockResolvedValue(undefined);
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("hydrates the Clerk-backed session and notebooks", async () => {
    vi.spyOn(apiClient, "getCurrentUser").mockResolvedValue({
      id: "user-1",
      email: "instructor@example.edu",
      display_name: "Test Instructor",
      institution_id: "institution-1",
      role: "instructor",
      external_subject_id: "user_123",
      active_organization_id: "org_123",
      active_organization_slug: "example-university",
      memberships: [],
    });
    vi.spyOn(apiClient, "listNotebooks").mockResolvedValue([
      {
        id: "notebook-1",
        institution_id: "institution-1",
        title: "Biology 101",
        description: "Intro notebook",
        owner_user_id: "user-1",
        visibility: "private",
        policy_mode: "teaching",
        course_offering_id: null,
        source_count: 2,
        learner_count: 8,
      },
    ]);

    render(
      <AppSessionProvider>
        <SessionProbe />
      </AppSessionProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));

    expect(screen.getByTestId("user-email")).toHaveTextContent("instructor@example.edu");
    expect(screen.getByTestId("notebook-count")).toHaveTextContent("1");
    expect(screen.getByTestId("selected-notebook")).toHaveTextContent("notebook-1");
    expect(screen.getByTestId("organization-name")).toHaveTextContent("Example University");
  });

  it("surfaces standardized API errors from the backend session check", async () => {
    vi.spyOn(apiClient, "getCurrentUser").mockRejectedValue(
      new ApiError("Clerk organization sync is still running.", 503, {
        code: "organization_sync_pending",
        provider: "clerk",
        retryable: true,
      }),
    );
    vi.spyOn(apiClient, "listNotebooks").mockResolvedValue([]);

    render(
      <AppSessionProvider>
        <SessionProbe />
      </AppSessionProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));

    expect(screen.getByTestId("auth-error")).toHaveTextContent(
      "Clerk organization sync is still running. Provider: clerk. Code: organization_sync_pending. This request can be retried.",
    );
    expect(screen.getByTestId("notebook-count")).toHaveTextContent("0");
  });

  it("does not call the API until an active organization exists", async () => {
    clerkState.auth.orgId = null;
    const getCurrentUserSpy = vi.spyOn(apiClient, "getCurrentUser").mockResolvedValue({
      id: "user-1",
      email: "instructor@example.edu",
      display_name: "Test Instructor",
      institution_id: "institution-1",
      role: "instructor",
      memberships: [],
    });

    render(
      <AppSessionProvider>
        <SessionProbe />
      </AppSessionProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));

    expect(getCurrentUserSpy).not.toHaveBeenCalled();
    expect(screen.getByTestId("user-email")).toHaveTextContent("");
    expect(screen.getByTestId("notebook-count")).toHaveTextContent("0");
  });
});
