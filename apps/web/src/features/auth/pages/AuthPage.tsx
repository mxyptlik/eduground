import { useEffect, useMemo, useState } from "react";
import {
  CreateOrganization,
  SignIn,
  SignUp,
  useOrganizationList,
  useUser,
} from "@clerk/clerk-react";
import { Navigate, useNavigate } from "react-router-dom";
import { apiClient } from "@/lib/api/client";
import { useAppSession } from "@/lib/session/AppSessionContext";

export function AuthPage() {
  const navigate = useNavigate();
  const { user: clerkUser } = useUser();
  const {
    loading,
    user,
    isSignedIn,
    organizationId,
    authError,
    refreshSession,
  } = useAppSession();
  const { userMemberships, isLoaded: membershipsLoaded, setActive } = useOrganizationList({
    userMemberships: {
      infinite: true,
    },
  });
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const memberships = userMemberships.data ?? [];
  const clerkAppearance = {
    elements: {
      rootBox: "clerk-surface",
      card: "clerk-card-shell",
      headerTitle: "clerk-title",
      headerSubtitle: "clerk-subtitle",
      socialButtonsBlockButton: "clerk-secondary-action",
      formButtonPrimary: "clerk-primary-action",
      footerActionLink: "clerk-inline-link",
      formFieldInput: "clerk-input",
      formFieldLabel: "clerk-label",
      identityPreviewText: "clerk-subtitle",
      identityPreviewEditButton: "clerk-inline-link",
      formResendCodeLink: "clerk-inline-link",
      otpCodeFieldInput: "clerk-input",
      alert: "clerk-alert",
      alertText: "clerk-alert-text",
      formFieldWarningText: "clerk-alert-text",
      formFieldSuccessText: "clerk-success-text",
      footerActionText: "clerk-subtitle",
    },
  } as const;

  useEffect(() => {
    if (!loading && user && organizationId) {
      navigate("/chat", { replace: true });
    }
  }, [loading, navigate, organizationId, user]);

  useEffect(() => {
    if (!isSignedIn || organizationId || !membershipsLoaded) {
      return;
    }

    const firstMembership = memberships[0];
    if (firstMembership && setActive) {
      void setActive({ organization: firstMembership.organization.id });
    }
  }, [isSignedIn, memberships, membershipsLoaded, organizationId, setActive]);

  const organizationChoices = useMemo(
    () =>
      memberships.map((membership) => ({
        id: membership.organization.id,
        name: membership.organization.name,
        role: membership.role,
      })),
    [memberships],
  );

  const hasActiveOrganization = Boolean(organizationId);
  const needsOrganizationSelection = isSignedIn && !hasActiveOrganization;
  const backendValidationFailed = isSignedIn && hasActiveOrganization && Boolean(authError);

  if (!loading && user && organizationId) {
    return <Navigate to="/chat" replace />;
  }

  return (
    <section className="auth-page">
      <div className="auth-panel">
        <p className="eyebrow">
          {!isSignedIn
            ? mode === "signup"
              ? "Create account"
              : "Sign in"
            : needsOrganizationSelection
              ? "Organization scope"
              : "Workspace access"}
        </p>
        <h2>
          {!isSignedIn
            ? mode === "signup"
              ? "Create your EduGround account"
              : "Sign in to continue learning"
            : needsOrganizationSelection
              ? "Choose a workspace"
              : "Finish loading your workspace"}
        </h2>
        <p className="page-description">
          {!isSignedIn
            ? "People sign in first. Workspaces are the shared environments they belong to, and roles like student, instructor, or admin apply inside those workspaces."
            : needsOrganizationSelection
              ? "You are already signed in as a person. Now choose one of your organizations or create a workspace so your notebooks, notes, and tutor history load in the right scope."
              : "Your user sign-in is valid, but the app could not finish validating the selected workspace with the backend yet."}
        </p>
        <p className="auth-meta">
          API target: <strong>{apiClient.getBaseUrl()}</strong>
        </p>

        {clerkUser && isSignedIn ? (
          <p className="auth-meta">
            Signed in as <strong>{clerkUser.primaryEmailAddress?.emailAddress ?? clerkUser.username ?? clerkUser.id}</strong>
          </p>
        ) : null}

        {loading ? <p className="empty-state">Checking Clerk session state...</p> : null}
        {authError ? (
          <div className="error-banner" role="alert">
            {authError}
          </div>
        ) : null}

        {!isSignedIn ? (
          <>
            <div className="auth-toggle" role="tablist" aria-label="Authentication mode">
              <button
                type="button"
                className={mode === "signup" ? "toggle-pill active" : "toggle-pill"}
                onClick={() => setMode("signup")}
              >
                Create account
              </button>
              <button
                type="button"
                className={mode === "signin" ? "toggle-pill active" : "toggle-pill"}
                onClick={() => setMode("signin")}
              >
                Sign in
              </button>
            </div>
            <div className="auth-clerk-card">
              {mode === "signup" ? (
                <SignUp
                  routing="virtual"
                  signInUrl="/auth"
                  fallbackRedirectUrl="/chat"
                  appearance={clerkAppearance}
                />
              ) : (
                <SignIn
                  routing="virtual"
                  signUpUrl="/auth"
                  fallbackRedirectUrl="/chat"
                  appearance={clerkAppearance}
                />
              )}
            </div>
          </>
        ) : needsOrganizationSelection ? (
          <div className="auth-onboarding-stack">
            <div className="auth-context-card">
              <span className="card-eyebrow">How access works</span>
              <strong>Users are primary. Workspaces scope the study environment.</strong>
              <p>
                Once a workspace is active, notebooks, sources, notes, quizzes, and grounded chat
                all resolve inside that membership context.
              </p>
            </div>
            {organizationChoices.length > 0 ? (
              <div className="stack">
                <p className="auth-meta">
                  Choose an organization you already belong to, or create a new one below.
                </p>
                <div className="auth-organization-list">
                  {organizationChoices.map((choice) => (
                    <button
                      key={choice.id}
                      type="button"
                      className="toggle-pill"
                      onClick={() => {
                        if (setActive) {
                          void setActive({ organization: choice.id });
                        }
                      }}
                    >
                      {choice.name} ({String(choice.role).toLowerCase()})
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <p className="empty-state">
                No memberships were found for this user yet. Create the first organization workspace below.
              </p>
            )}
            <div className="auth-clerk-card">
              <CreateOrganization
                routing="virtual"
                afterCreateOrganizationUrl="/chat"
                skipInvitationScreen={false}
              />
            </div>
          </div>
        ) : (
          <div className="auth-onboarding-stack">
            <div className="auth-context-card">
              <span className="card-eyebrow">Validation state</span>
              <strong>The workspace exists, but backend validation still needs to complete.</strong>
              <p>
                This is not a sign-in problem. Retry the workspace validation after the backend is
                ready.
              </p>
            </div>
            <p className="empty-state">
              Active organization detected. The remaining issue is backend validation, not user sign-in or membership selection.
            </p>
            <button
              type="button"
              className="cta-button"
              onClick={() => {
                void refreshSession();
              }}
            >
              Retry workspace validation
            </button>
          </div>
        )}
      </div>
    </section>
  );
}
