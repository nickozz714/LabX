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
import { Versie } from "@/components/Versie";
import { Boxes, CalendarClock, KanbanSquare, KeyRound, LayoutDashboard, Lock, LogOut, MessageSquare, Settings, ShieldCheck, Workflow, Wrench, Clock, Menu, X} from "lucide-react";
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
  { to: "/uren", label: "Uren", icon: Clock },
  { to: "/schedules", label: "Scheduling", icon: CalendarClock },
  { to: "/azure-profiles", label: "Azure-profielen", icon: KeyRound },
  // Eigen tab en geen kaartje in de instellingen: dit pak je erbij terwijl je
  // een skill schrijft of een ticket opstelt.
  { to: "/kluis", label: "Kluis", icon: Lock },
  { to: "/guard", label: "Data-guard", icon: ShieldCheck },   // met het tabblad Audit
  { to: "/settings", label: "Instellingen", icon: Settings },
];

export function Shell() {
  const { username, logout } = useAuth();
  const [wizardDismissed, setWizardDismissed] = useState(() => localStorage.getItem(WIZARD_DISMISSED_KEY) === "1");
  const [checked, setChecked] = useState(false);
  const [needsWizard, setNeedsWizard] = useState(false);
  const [runningCount, setRunningCount] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);

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
    <div className="flex h-app w-full flex-col overflow-hidden bg-background text-foreground">
      <header className="veilig-boven flex shrink-0 items-center gap-1 border-b border-sidebar-border bg-sidebar px-3 text-sidebar-foreground">
        {/* Op een telefoon is een strip van twaalf tabs onwerkbaar: je scrolt
            blind langs labels die je niet kunt lezen. Daar wordt het een
            uitschuifmenu; vanaf tablet blijft de vertrouwde tabstrip. */}
        <button
          type="button"
          onClick={() => setMenuOpen((v) => !v)}
          aria-label={menuOpen ? "Menu sluiten" : "Menu openen"}
          aria-expanded={menuOpen}
          className="-ml-1 mr-1 flex h-11 w-11 items-center justify-center rounded-md text-sidebar-foreground/80 hover:bg-sidebar-accent/10 md:hidden"
        >
          {menuOpen ? <X size={20} /> : <Menu size={20} />}
        </button>
        <div className="mr-3 flex items-center gap-2 py-3 text-lg font-bold tracking-tight">
          <span className="inline-block h-2 w-2 rounded-full bg-sidebar-accent" />
          LabX
        </div>
        <nav className="hidden flex-1 items-stretch gap-1 overflow-x-auto md:flex">
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
        {/* Op mobiel duwt dit de accountregel naar rechts; op desktop doet de
            tabstrip dat al met flex-1. */}
        <div className="flex-1 md:hidden" />
        <div className="flex shrink-0 items-center gap-3 pl-3 text-xs text-sidebar-foreground/60">
          {/* Welke build je voor je hebt — en een waarschuwing als de server
              er al een nieuwere draait. */}
          {/* Versie en naam zijn naslag, geen bediening: op een telefoon
              kosten ze ruimte die de titelbalk niet heeft. Het uitlogicoon
              blijft wél staan, maar zonder woord ernaast. */}
          <span className="hidden sm:inline-flex"><Versie /></span>
          <span className="hidden sm:inline">{username}</span>
          <button onClick={logout} aria-label="Uitloggen"
                  className="flex h-11 items-center gap-1 px-1 hover:text-sidebar-foreground sm:h-auto sm:px-0">
            <LogOut size={16} /> <span className="hidden sm:inline">Uitloggen</span>
          </button>
        </div>
      </header>

      {/* Het uitschuifmenu. Alleen onder md, en het sluit zichzelf zodra je
          iets kiest — anders blijft het over je scherm liggen. */}
      {menuOpen && (
        <div className="md:hidden">
          <button
            type="button"
            aria-label="Menu sluiten"
            onClick={() => setMenuOpen(false)}
            className="fixed inset-0 z-30 bg-foreground/30"
          />
          <nav className="veilig-onder absolute inset-x-0 z-40 max-h-[70dvh] overflow-y-auto border-b border-sidebar-border bg-sidebar p-2 shadow-lg">
            {NAV.map(({ to, label, icon: Icon }) => (
              <NavLink
                key={to}
                to={to}
                onClick={() => setMenuOpen(false)}
                className={({ isActive }) =>
                  // min-h-11: onder ongeveer 44px wordt een knop op een
                  // touchscreen een gokje.
                  `flex min-h-11 items-center gap-3 rounded-md px-3 py-2.5 text-sm font-medium ${
                    isActive
                      ? "bg-sidebar-accent/10 text-sidebar-accent"
                      : "text-sidebar-foreground/80"
                  }`
                }
              >
                <Icon size={18} />
                {label}
                {to === "/chat" && runningCount > 0 && (
                  <span className="ml-auto inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-sidebar-accent px-1.5 text-[11px] font-bold text-white">
                    {runningCount}
                  </span>
                )}
              </NavLink>
            ))}
          </nav>
        </div>
      )}

      <main className="veilig-onder flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  );
}
