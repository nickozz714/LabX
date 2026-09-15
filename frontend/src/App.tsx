import { Navigate, Route, BrowserRouter, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider, useAuth } from "@/contexts/AuthContext";
import { Shell } from "@/components/Shell";
import { BevestigingProvider } from "@/components/Bevestiging";
import { MeldingProvider } from "@/components/Meldingen";
import { LoginPage } from "@/pages/LoginPage";
import { OverviewPage } from "@/pages/OverviewPage";
import { GuardPage } from "@/pages/GuardPage";
import { LabsPage } from "@/pages/LabsPage";
import { ChatPage } from "@/pages/ChatPage";
import { SettingsPage } from "@/pages/SettingsPage";
import { SkillsPage } from "@/pages/SkillsPage";
import { WorkflowsPage } from "@/pages/WorkflowsPage";
import { SchedulesPage } from "@/pages/SchedulesPage";
import { BoardsPage } from "@/pages/BoardsPage";
import { BoardPage } from "@/pages/BoardPage";
import { AzureProfilesPage } from "@/pages/AzureProfilesPage";

const queryClient = new QueryClient();

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated } = useAuth();
  if (!isAuthenticated) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/"
        element={
          <ProtectedRoute>
            <Shell />
          </ProtectedRoute>
        }
      >
        <Route index element={<Navigate to="/labs" replace />} />
        <Route path="overzicht" element={<OverviewPage />} />
        <Route path="labs" element={<LabsPage />} />
        <Route path="chat" element={<ChatPage />} />
        <Route path="skills" element={<SkillsPage />} />
        <Route path="boards" element={<BoardsPage />} />
        <Route path="boards/:boardId" element={<BoardPage />} />
        <Route path="workflows" element={<WorkflowsPage />} />
        <Route path="schedules" element={<SchedulesPage />} />
        <Route path="azure-profiles" element={<AzureProfilesPage />} />
        <Route path="guard" element={<GuardPage />} />
        <Route path="settings" element={<SettingsPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/labs" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          {/* Buiten de router, zodat een melding blijft staan als de actie je
              naar een ander scherm brengt. */}
          <MeldingProvider>
            {/* Bevestigen doen we zelf, niet met window.confirm(): een browser
                mag dat venster weigeren en geeft dan stil "nee" terug. */}
            <BevestigingProvider>
              <AppRoutes />
            </BevestigingProvider>
          </MeldingProvider>
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
