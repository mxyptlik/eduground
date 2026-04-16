import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useAppSession } from "@/lib/session/AppSessionContext";

export function RequireSession({ children }: { children: ReactNode }) {
  const { loading, isSignedIn, organizationId, authError, user } = useAppSession();

  if (loading) {
    return <div className="empty-state">Checking your session...</div>;
  }

  if (!isSignedIn || !user || !organizationId || authError) {
    return <Navigate to="/auth" replace />;
  }

  return <>{children}</>;
}
