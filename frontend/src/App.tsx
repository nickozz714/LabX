import { Navigate, Route, BrowserRouter, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider, useAuth } from "@/contexts/AuthContext";
import { Shell } from "@/components/Shell";
import { LoginPage } from "@/pages/LoginPage";
import { LabsPage } from "@/pages/LabsPage";
import { ChatPage } from "@/pages/ChatPage";
import { SettingsPage } from "@/pages/SettingsPage";
import { SkillsPage } from "@/pages/SkillsPage";
import { WorkflowsPage } from "@/pages/WorkflowsPage";
import { SchedulesPage } from "@/pages/SchedulesPage";
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
        <Route path="labs" element={<LabsPage />} />
        <Route path="chat" element={<ChatPage />} />
        <Route path="skills" element={<SkillsPage />} />
        <Route path="workflows" element={<WorkflowsPage />} />
        <Route path="schedules" element={<SchedulesPage />} />
        <Route path="azure-profiles" element={<AzureProfilesPage />} />
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
          <AppRoutes />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
