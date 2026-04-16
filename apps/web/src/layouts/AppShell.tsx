import { useEffect, useMemo, useRef, useState } from "react";
import { OrganizationSwitcher, useClerk, useUser } from "@clerk/clerk-react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { BrandMark } from "@/components/BrandMark";
import { getRoleExperience } from "@/lib/session/roleExperience";
import { useAppSession } from "@/lib/session/AppSessionContext";

function ShellIcon({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d={d} fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
    </svg>
  );
}

const navigation = [
  { to: "/notebooks", label: "Notebooks", icon: "M6 5.5h10a2 2 0 0 1 2 2v11H8a2 2 0 0 0-2 2Zm0 0a2 2 0 0 0-2 2v11h10" },
  { to: "/sources", label: "Sources", icon: "M7 6h10M7 12h10M7 18h7M4.5 6h.01M4.5 12h.01M4.5 18h.01" },
  { to: "/chat", label: "Tutor Chat", icon: "M6 7.5h12a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H11l-4 3v-3H6a2 2 0 0 1-2-2v-6a2 2 0 0 1 2-2Z" },
  { to: "/notes", label: "Notes", icon: "M7 4.5h7l4 4V19a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 6 19V6A1.5 1.5 0 0 1 7.5 4.5Zm6.5 0V9H18" },
  { to: "/quizzes", label: "Quizzes", icon: "M8 8h8M8 12h5M6.5 4.5h11A1.5 1.5 0 0 1 19 6v12a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 5 18V6a1.5 1.5 0 0 1 1.5-1.5Z" },
  { to: "/analytics", label: "Analytics", icon: "M6 18V9M12 18V5M18 18v-7" },
];

const routeMeta = [
  { match: (pathname: string) => pathname.startsWith("/notebooks"), label: "Notebooks", summary: "Manage study workspaces." },
  { match: (pathname: string) => pathname.startsWith("/sources"), label: "Sources", summary: "Review source readiness and ingestion." },
  { match: (pathname: string) => pathname.startsWith("/chat"), label: "Tutor", summary: "Ask grounded questions and inspect evidence." },
  { match: (pathname: string) => pathname.startsWith("/notes"), label: "Notes", summary: "Capture durable study memory." },
  { match: (pathname: string) => pathname.startsWith("/quizzes"), label: "Quizzes", summary: "Turn learning into practice." },
  { match: (pathname: string) => pathname.startsWith("/analytics"), label: "Analytics", summary: "Track notebook health and usage." },
];

function getAvatarLabel(nameOrEmail: string | null | undefined): string {
  if (!nameOrEmail) {
    return "U";
  }
  return nameOrEmail.trim().charAt(0).toUpperCase() || "U";
}

export function AppShell() {
  const location = useLocation();
  const { signOut } = useClerk();
  const { user: clerkUser } = useUser();
  const { user, notebooks, selectedNotebookId, selectNotebook, organizationName } = useAppSession();
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [isProfileOpen, setIsProfileOpen] = useState(false);
  const sidebarRef = useRef<HTMLElement | null>(null);
  const profileRef = useRef<HTMLDivElement | null>(null);

  const selectedNotebook = useMemo(
    () => notebooks.find((notebook) => notebook.id === selectedNotebookId) ?? notebooks[0] ?? null,
    [notebooks, selectedNotebookId],
  );
  const activeRouteMeta = routeMeta.find((item) => item.match(location.pathname)) ?? routeMeta[0];
  const roleExperience = getRoleExperience(user?.role);

  useEffect(() => {
    setIsSidebarOpen(false);
    setIsProfileOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    function handlePointerDown(event: MouseEvent) {
      const target = event.target as Node;

      if (isSidebarOpen) {
        const clickedInsideSidebar = sidebarRef.current?.contains(target) ?? false;
        if (!clickedInsideSidebar) {
          setIsSidebarOpen(false);
        }
      }

      if (isProfileOpen) {
        const clickedInsideProfile = profileRef.current?.contains(target) ?? false;
        if (!clickedInsideProfile) {
          setIsProfileOpen(false);
        }
      }
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setIsSidebarOpen(false);
        setIsProfileOpen(false);
      }
    }

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [isProfileOpen, isSidebarOpen]);

  return (
    <div className={`app-shell app-shell--rail ${isSidebarOpen ? "app-shell--rail-open" : ""}`}>
      <button
        className={`shell-mobile-toggle ${isSidebarOpen ? "is-open" : ""}`}
        type="button"
        onClick={() => {
          setIsSidebarOpen((current) => !current);
          setIsProfileOpen(false);
        }}
        aria-label={isSidebarOpen ? "Close navigation" : "Open navigation"}
        aria-expanded={isSidebarOpen}
      >
        <BrandMark />
      </button>

      <div className={`shell-sidebar-backdrop ${isSidebarOpen ? "is-open" : ""}`} />

      <aside
        ref={sidebarRef}
        className={`shell-rail ${isSidebarOpen ? "is-open" : ""}`}
        aria-label="Primary navigation"
      >
        <div className="shell-rail__top">
          <button
            className="shell-rail__brand"
            type="button"
            onClick={() => {
              setIsSidebarOpen((current) => !current);
              setIsProfileOpen(false);
            }}
            aria-label={isSidebarOpen ? "Collapse sidebar" : "Expand sidebar"}
            aria-expanded={isSidebarOpen}
          >
            <BrandMark />
          </button>

          <nav className="shell-rail__nav">
            {navigation.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) => `shell-rail__link ${isActive ? "active" : ""}`}
                title={item.label}
                onClick={() => {
                  setIsSidebarOpen(false);
                  setIsProfileOpen(false);
                }}
              >
                <span className="shell-rail__icon">
                  <ShellIcon d={item.icon} />
                </span>
                <span className="shell-rail__label">{item.label}</span>
              </NavLink>
            ))}
          </nav>
        </div>

        <div ref={profileRef} className="shell-rail__foot">
          <button
            className={`shell-avatar-button ${isProfileOpen ? "is-open" : ""}`}
            type="button"
            onClick={() => {
              setIsProfileOpen((current) => !current);
              setIsSidebarOpen(false);
            }}
            aria-label="Open workspace menu"
            aria-expanded={isProfileOpen}
            aria-controls="app-profile-dropdown"
          >
            {clerkUser?.imageUrl ? (
              <img src={clerkUser.imageUrl} alt="Profile" />
            ) : (
              <span>{getAvatarLabel(user?.display_name ?? user?.email)}</span>
            )}
          </button>

          <div id="app-profile-dropdown" className={`shell-profile-dropdown ${isProfileOpen ? "is-open" : ""}`}>
            <div className="shell-profile-dropdown__identity">
              <strong>{user?.display_name ?? user?.email ?? "Workspace user"}</strong>
              <span>{user?.email ?? "No email"}</span>
            </div>

            <label className="shell-select shell-select--dropdown">
              <span className="shell-select__label">Active notebook</span>
              <select
                className="select-input"
                value={selectedNotebook?.id ?? ""}
                onChange={(event) => selectNotebook(event.target.value)}
                disabled={notebooks.length === 0}
              >
                {notebooks.length === 0 ? <option value="">No notebooks</option> : null}
                {notebooks.map((notebook) => (
                  <option key={notebook.id} value={notebook.id}>
                    {notebook.title}
                  </option>
                ))}
              </select>
            </label>

            <div className="shell-profile-dropdown__meta">
              <span className="status-pill">{roleExperience.label}</span>
              <span className="status-pill">{organizationName ?? "No workspace"}</span>
            </div>

            <div className="clerk-trigger-shell shell-trigger-shell--dropdown">
              <OrganizationSwitcher
                hidePersonal
                afterCreateOrganizationUrl="/chat"
                afterLeaveOrganizationUrl="/auth"
                afterSelectOrganizationUrl="/chat"
                appearance={{
                  elements: {
                    organizationSwitcherTrigger: "org-switcher-trigger",
                    organizationSwitcherPreviewButton: "org-switcher-preview",
                    organizationSwitcherTriggerIcon: "org-switcher-trigger-icon",
                    organizationSwitcherPopoverCard: "org-switcher-popover",
                    organizationSwitcherPopoverActionButton: "org-switcher-action",
                    organizationSwitcherPopoverFooter: "org-switcher-footer",
                  },
                }}
              />
            </div>

            <button
              className="shell-button shell-button--ghost shell-button--dropdown"
              type="button"
              onClick={() => {
                void signOut({ redirectUrl: "/auth" });
              }}
            >
              Sign out
            </button>
          </div>
        </div>
      </aside>

      <main className="content content--rail">
        <section className="page-frame">
          <section className={`page-surface ${location.pathname.startsWith("/chat") ? "page-surface--chat" : ""}`}>
            <Outlet />
          </section>
        </section>
      </main>
    </div>
  );
}
