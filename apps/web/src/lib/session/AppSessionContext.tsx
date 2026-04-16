import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useAuth, useClerk, useOrganization, useUser } from "@clerk/clerk-react";
import { apiClient } from "@/lib/api/client";
import { getDisplayErrorMessage } from "@/lib/api/errors";
import type { AuthUser, Notebook } from "@/lib/api/types";

type AppSessionContextValue = {
  user: AuthUser | null;
  notebooks: Notebook[];
  selectedNotebookId: string | null;
  loading: boolean;
  isSignedIn: boolean;
  organizationId: string | null;
  organizationName: string | null;
  authError: string | null;
  logout: () => Promise<void>;
  refreshSession: () => Promise<void>;
  refreshNotebooks: () => Promise<void>;
  selectNotebook: (notebookId: string) => void;
};

const AppSessionContext = createContext<AppSessionContextValue | undefined>(undefined);

function resolveSelectedNotebook(notebooks: Notebook[], currentValue: string | null) {
  if (currentValue && notebooks.some((notebook) => notebook.id === currentValue)) {
    return currentValue;
  }
  return notebooks[0]?.id ?? null;
}

export function AppSessionProvider({ children }: { children: ReactNode }) {
  const { isLoaded, isSignedIn, getToken, orgId } = useAuth();
  const { organization } = useOrganization();
  const { user: clerkUser } = useUser();
  const clerk = useClerk();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [notebooks, setNotebooks] = useState<Notebook[]>([]);
  const [selectedNotebookId, setSelectedNotebookId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [authError, setAuthError] = useState<string | null>(null);

  const notebookStorageKey = useMemo(() => {
    if (!clerkUser?.id || !orgId) {
      return null;
    }
    return `eduground:selected-notebook:${clerkUser.id}:${orgId}`;
  }, [clerkUser?.id, orgId]);

  useEffect(() => {
    apiClient.setAuthContext({
      getToken: async () => {
        if (!isSignedIn) {
          return null;
        }
        return getToken();
      },
      getActiveOrganizationId: () => orgId ?? null,
    });
    return () => {
      apiClient.clearAuthContext();
    };
  }, [getToken, isSignedIn, orgId]);

  useEffect(() => {
    if (!notebookStorageKey) {
      setSelectedNotebookId(null);
      return;
    }
    setSelectedNotebookId(window.localStorage.getItem(notebookStorageKey));
  }, [notebookStorageKey]);

  async function refreshNotebooks() {
    if (!isSignedIn || !orgId) {
      setNotebooks([]);
      return;
    }
    const notebookList = await apiClient.listNotebooks();
    setNotebooks(notebookList);
    const nextSelectedNotebook = resolveSelectedNotebook(notebookList, selectedNotebookId);
    setSelectedNotebookId(nextSelectedNotebook);
    if (notebookStorageKey) {
      if (nextSelectedNotebook) {
        window.localStorage.setItem(notebookStorageKey, nextSelectedNotebook);
      } else {
        window.localStorage.removeItem(notebookStorageKey);
      }
    }
  }

  async function refreshSession() {
    if (!isLoaded) {
      return;
    }

    setLoading(true);
    setAuthError(null);

    if (!isSignedIn) {
      setUser(null);
      setNotebooks([]);
      setSelectedNotebookId(null);
      setLoading(false);
      return;
    }

    if (!orgId) {
      setUser(null);
      setNotebooks([]);
      setSelectedNotebookId(null);
      setLoading(false);
      return;
    }

    try {
      const currentUser = await apiClient.getCurrentUser();
      setUser(currentUser);
      await refreshNotebooks();
    } catch (error) {
      const nextError = getDisplayErrorMessage(
        error,
        "Unable to validate your Clerk session with the API.",
      );
      setAuthError(nextError);
      setUser(null);
      setNotebooks([]);
      setSelectedNotebookId(null);
      console.error(error);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refreshSession();
  }, [isLoaded, isSignedIn, orgId, notebookStorageKey]);

  async function logout() {
    await clerk.signOut({ redirectUrl: "/auth" });
  }

  function selectNotebook(notebookId: string) {
    setSelectedNotebookId(notebookId);
    if (notebookStorageKey) {
      window.localStorage.setItem(notebookStorageKey, notebookId);
    }
  }

  const value = {
    user,
    notebooks,
    selectedNotebookId,
    loading,
    isSignedIn: Boolean(isSignedIn),
    organizationId: orgId ?? null,
    organizationName: organization?.name ?? null,
    authError,
    logout,
    refreshSession,
    refreshNotebooks,
    selectNotebook,
  };

  return <AppSessionContext.Provider value={value}>{children}</AppSessionContext.Provider>;
}

export function useAppSession() {
  const context = useContext(AppSessionContext);
  if (!context) {
    throw new Error("useAppSession must be used inside AppSessionProvider");
  }
  return context;
}
