import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "@/layouts/AppShell";
import { AuthPage } from "@/features/auth/pages/AuthPage";
import { NotebooksPage } from "@/features/notebooks/pages/NotebooksPage";
import { SourcesPage } from "@/features/sources/pages/SourcesPage";
import { TutorChatPage } from "@/features/tutor-chat/pages/TutorChatPage";
import { NotesPage } from "@/features/notes/pages/NotesPage";
import { QuizzesPage } from "@/features/quizzes/pages/QuizzesPage";
import { AnalyticsPage } from "@/features/analytics/pages/AnalyticsPage";
import { RequireSession } from "@/lib/session/RequireSession";

export function AppRoutes() {
  return (
    <Routes>
      <Route
        element={
          <RequireSession>
            <AppShell />
          </RequireSession>
        }
      >
        <Route path="/" element={<Navigate to="/chat" replace />} />
        <Route path="/notebooks" element={<NotebooksPage />} />
        <Route path="/sources" element={<SourcesPage />} />
        <Route path="/chat" element={<TutorChatPage />} />
        <Route path="/notes" element={<NotesPage />} />
        <Route path="/quizzes" element={<QuizzesPage />} />
        <Route path="/analytics" element={<AnalyticsPage />} />
      </Route>
      <Route path="/auth" element={<AuthPage />} />
      <Route path="*" element={<Navigate to="/chat" replace />} />
    </Routes>
  );
}
