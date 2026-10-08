import { Navigate, Route, BrowserRouter, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider, useAuth } from "@/contexts/AuthContext";
import { Shell } from "@/components/Shell";
import { Foutvanger } from "@/components/Foutvanger";
import { BevestigingProvider } from "@/components/Bevestiging";
import { MeldingProvider } from "@/components/Meldingen";
import { LoginPage } from "@/pages/LoginPage";
import { OverviewPage } from "@/pages/OverviewPage";
import { GuardPage } from "@/pages/GuardPage";
import { KluisPage } from "@/pages/KluisPage";
import { LabsPage, LabDetailPage } from "@/pages/LabsPage";
import { ChatPage } from "@/pages/ChatPage";
import { SettingsPage } from "@/pages/SettingsPage";
import { SkillsPage } from "@/pages/SkillsPage";
import { WorkflowsPage } from "@/pages/WorkflowsPage";
import { WorkflowEditorPage } from "@/pages/WorkflowEditorPage";
import { SchedulesPage } from "@/pages/SchedulesPage";
import { BoardsPage } from "@/pages/BoardsPage";
import { BoardPage } from "@/pages/BoardPage";
import { UrenPage } from "@/pages/UrenPage";
import { OrchestratorPage } from "@/pages/OrchestratorPage";
import { AzureProfilesPage } from "@/pages/AzureProfilesPage";
import { WorkbenchPage } from "@/pages/WorkbenchPage";
import { KluisTabsPage } from "@/pages/KluisTabsPage";

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
            {/* Eén kapot veld in één scherm mag niet de hele app wit maken. */}
            <Foutvanger>
              <Shell />
            </Foutvanger>
          </ProtectedRoute>
        }
      >
        {/* Overzicht is het beginpunt: daar zie je wat er draait, wat wacht
            en hoe het laatste afliep -- en daar start je de orchestrator. */}
        <Route index element={<Navigate to="/overzicht" replace />} />
        <Route path="overzicht" element={<OverviewPage />} />
        <Route path="labs" element={<LabsPage />} />
        {/* Een lab open je niet even tussendoor; dat hoort een eigen
            adres te hebben dat je kunt delen. */}
        <Route path="labs/:labId" element={<LabDetailPage />} />
        <Route path="chat" element={<ChatPage />} />
        <Route path="boards" element={<BoardsPage />} />
        <Route path="boards/:boardId" element={<BoardPage />} />
        <Route path="uren" element={<UrenPage />} />

        {/* Wat de agent kan, en wanneer hij het doet. */}
        <Route path="workbench" element={<WorkbenchPage />}>
          <Route index element={<Navigate to="skills" replace />} />
          <Route path="skills" element={<SkillsPage sectie="skills" />} />
          <Route path="tools" element={<SkillsPage sectie="tools" />} />
          <Route path="mcp" element={<SkillsPage sectie="mcp" />} />
          <Route path="workflows" element={<WorkflowsPage />} />
          <Route path="scheduling" element={<SchedulesPage />} />
        </Route>
        {/* De editor is een volledig scherm en hoort niet onder de subtabs. */}
        <Route path="workflows/:id" element={<WorkflowEditorPage />} />

        {/* Inloggegevens die de agent mag gebruiken maar nooit mag zien. */}
        <Route path="kluis" element={<KluisTabsPage />}>
          <Route index element={<Navigate to="geheimen" replace />} />
          <Route path="geheimen" element={<KluisPage />} />
          <Route path="azure" element={<AzureProfilesPage />} />
        </Route>

        <Route path="orchestrator" element={<OrchestratorPage />} />

        {/* De oude adressen blijven werken: er staan bladwijzers en links in
            tickets naar deze paden. */}
        <Route path="skills" element={<Navigate to="/workbench/skills" replace />} />
        <Route path="workflows" element={<Navigate to="/workbench/workflows" replace />} />
        <Route path="schedules" element={<Navigate to="/workbench/scheduling" replace />} />
        <Route path="azure-profiles" element={<Navigate to="/kluis/azure" replace />} />
        <Route path="spraak" element={<Navigate to="/orchestrator" replace />} />
        {/* Audit is opgegaan in Data-guard; oude links blijven werken. */}
        <Route path="audit" element={<Navigate to="/guard" replace />} />
        <Route path="guard" element={<GuardPage />} />
        <Route path="settings" element={<SettingsPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/overzicht" replace />} />
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
