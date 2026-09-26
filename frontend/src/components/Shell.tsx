/**
 * components/Shell.tsx — app shell with a top tabbed nav bar. One tab strip,
 * one content pane; no per-project switcher (LabX is single-tenant).
 */
import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";
import { dockerStatus } from "@/lib/labs";
import { settingsApi } from "@/lib/settings";
import { FirstRunWizard } from "@/components/FirstRunWizard";
import { Boxes, CalendarClock, KanbanSquare, KeyRound, LayoutDashboard, LineChart, Lock, LogOut, MessageSquare, Settings, ShieldCheck, Workflow, Wrench } from "lucide-react";
import { chatApi } from "@/lib/chat";

const WIZARD_DISMISSED_KEY = "labx_wizard_dismissed";

const NAV = [
  // Vooraan: dit is het scherm waarmee je de dag begint — wat draait er, wat
  // wacht er, en hoe liep het laatste af.
  { to: "/overzicht", label: "Overzicht", icon: LayoutDashboard },
  { to: "/labs", label: "Labs", icon: Boxes },
  { to: "/chat", label: "Chat", icon: MessageSquare },
  { to: "/boards", label: "Boards", icon: KanbanSquare },
  { to: "/skills", label: "Skills & Tools", icon: Wrench },
  { to: "/workflows", label: "Workflows", icon: Workflow },
  { to: "/schedules", label: "Scheduling", icon: CalendarClock },
  { to: "/azure-profiles", label: "Azure-profielen", icon: KeyRound },
  // Eigen tab en geen kaartje in de instellingen: dit pak je erbij terwijl je
  // een skill schrijft of een ticket opstelt.
  { to: "/kluis", label: "Kluis", icon: Lock },
  { to: "/audit", label: "Audit", icon: LineChart },
  { to: "/guard", label: "Data-guard", icon: ShieldCheck },
  { to: "/settings", label: "Instellingen", icon: Settings },
];

export function Shell() {
  const { username, logout } = useAuth();
  const [wizardDismissed, setWizardDismissed] = useState(() => localStorage.getItem(WIZARD_DISMISSED_KEY) === "1");
  const [checked, setChecked] = useState(false);
  const [needsWizard, setNeedsWizard] = useState(false);
  const [runningCount, setRunningCount] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const poll = () =>
      chatApi.listBackgroundRuns({ status: "running", mode: "background" })
        .then((r) => !cancelled && setRunningCount(r.length))
        .catch(() => {});
    poll();
    const t = setInterval(poll, 8000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, []);

  useEffect(() => {
    if (wizardDismissed) {
      setChecked(true);
      return;
    }
    Promise.all([dockerStatus(), settingsApi.get()])
      .then(([d, s]) => setNeedsWizard(!d.daemon_up || !s.oauth_token_configured))
      .catch(() => {})
      .finally(() => setChecked(true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (checked && needsWizard && !wizardDismissed) {
    return (
      <FirstRunWizard
        onDone={() => {
          localStorage.setItem(WIZARD_DISMISSED_KEY, "1");
          setWizardDismissed(true);
        }}
      />
    );
  }

  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden bg-background text-foreground">
      <header className="flex shrink-0 items-center gap-1 border-b border-sidebar-border bg-sidebar px-3 text-sidebar-foreground">
        <div className="mr-3 flex items-center gap-2 py-3 text-lg font-bold tracking-tight">
          <span className="inline-block h-2 w-2 rounded-full bg-sidebar-accent" />
          LabX
        </div>
        <nav className="flex flex-1 items-stretch gap-1 overflow-x-auto">
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `flex items-center gap-2 whitespace-nowrap border-b-2 px-3 py-3 text-sm font-medium transition ${
                  isActive
                    ? "border-sidebar-accent text-sidebar-accent"
                    : "border-transparent text-sidebar-foreground/70 hover:border-sidebar-accent/30 hover:text-sidebar-foreground"
                }`
              }
            >
              <Icon size={16} />
              {label}
              {to === "/chat" && runningCount > 0 && (
                <span
                  className="ml-1 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-sidebar-accent px-1 text-[10px] font-bold text-white"
                  title={`${runningCount} lopende achtergrondtaak/-taken`}
                >
                  {runningCount}
                </span>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="flex shrink-0 items-center gap-3 pl-3 text-xs text-sidebar-foreground/60">
          <span>{username}</span>
          <button onClick={logout} className="flex items-center gap-1 hover:text-sidebar-foreground">
            <LogOut size={14} /> Uitloggen
          </button>
        </div>
      </header>
      <main className="flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  );
}
