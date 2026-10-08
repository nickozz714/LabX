/**
 * components/Shell.tsx — app shell met een menu aan de linkerkant.
 *
 * Was een horizontale tabstrip. Die liep vol: negen bestemmingen plus versie,
 * gebruikersnaam en uitloggen passen niet meer op één regel zonder dat je
 * gaat scannen in plaats van kijken. Verticaal is er ruimte zat, en een
 * lijstje lees je sneller dan een rij.
 *
 * Drie standen:
 * - Breed scherm: het menu staat er gewoon, met labels.
 * - Breed scherm, ingeklapt: alleen iconen. Dat scheelt 11rem, en dat merk je
 *   op de orchestrator en de workflow-tekenaar. De keuze blijft bewaard.
 * - Telefoon: het menu schuift over het scherm en sluit zichzelf zodra je
 *   iets kiest.
 */
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";
import { dockerStatus } from "@/lib/labs";
import { settingsApi } from "@/lib/settings";
import { FirstRunWizard } from "@/components/FirstRunWizard";
import { Versie } from "@/components/Versie";
import { ThemaKiezer } from "@/components/ThemaKiezer";
import {
  Boxes, ChevronLeft, ChevronRight, KanbanSquare, LayoutDashboard, Lock, LogOut,
  MessageSquare, Menu, Settings, ShieldCheck, Wrench, Clock, X,
} from "lucide-react";
import { chatApi } from "@/lib/chat";

const WIZARD_DISMISSED_KEY = "labx_wizard_dismissed";
const INGEKLAPT_KEY = "labx_menu_ingeklapt";

const NAV = [
  // Vooraan én het beginpunt van de app: wat draait er, wat wacht er, en hoe
  // liep het laatste af. Hier start je ook de orchestrator.
  { to: "/overzicht", label: "Overzicht", icon: LayoutDashboard, onder: "Wat draait er nu" },
  { to: "/labs", label: "Labs", icon: Boxes, onder: "Je omgevingen" },
  { to: "/chat", label: "Chat", icon: MessageSquare, onder: "Werken" },
  { to: "/boards", label: "Boards", icon: KanbanSquare, onder: "Tickets en agent-runs" },
  { to: "/workbench", label: "Workbench", icon: Wrench, onder: "Skills, workflows en planning" },
  { to: "/uren", label: "Uren", icon: Clock, onder: "Tijd per lab" },
  { to: "/kluis", label: "Kluis", icon: Lock, onder: "Geheimen en Azure-profielen" },
  { to: "/guard", label: "Data-guard", icon: ShieldCheck, onder: "Wat er naar buiten mag" },
  { to: "/settings", label: "Instellingen", icon: Settings, onder: "Deze installatie" },
];

/** Pagina's met een eigen kop die niet in het menu staan. De orchestrator
 *  bereik je vanaf het Overzicht; zonder deze regel zou de balk daar "Overzicht"
 *  blijven zeggen en dat is precies de verwarring die een kop moet wegnemen. */
const EXTRA_PAGINAS = [
  { to: "/orchestrator", label: "Orchestrator", onder: "Praten met je labs" },
  { to: "/workflows/", label: "Workflow", onder: "De tekenaar" },
];

/** Welke pagina hoort bij dit pad? Het langste passende begin wint, zodat
 *  /labs/abc onder Labs valt en /workbench/skills onder Workbench. */
function paginaVan(pad: string) {
  const alles = [...NAV, ...EXTRA_PAGINAS];
  let beste: { label: string; onder: string } | null = null;
  let langste = -1;
  for (const p of alles) {
    const basis = p.to.replace(/\/$/, "");
    if ((pad === basis || pad.startsWith(basis + "/")) && basis.length > langste) {
      langste = basis.length;
      beste = { label: p.label, onder: p.onder };
    }
  }
  return beste;
}

export function Shell() {
  const { username, logout } = useAuth();
  const { pathname } = useLocation();
  const pagina = paginaVan(pathname);
  const [wizardDismissed, setWizardDismissed] = useState(
    () => localStorage.getItem(WIZARD_DISMISSED_KEY) === "1");
  const [checked, setChecked] = useState(false);
  const [needsWizard, setNeedsWizard] = useState(false);
  const [runningCount, setRunningCount] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);
  const [ingeklapt, setIngeklapt] = useState(
    () => localStorage.getItem(INGEKLAPT_KEY) === "1");

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

  function zetIngeklapt(waarde: boolean) {
    setIngeklapt(waarde);
    try {
      localStorage.setItem(INGEKLAPT_KEY, waarde ? "1" : "0");
    } catch {
      /* privémodus: dan onthoudt hij het deze sessie */
    }
  }

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

  const menu = (opTelefoon: boolean) => (
    <nav className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
      {NAV.map(({ to, label, icon: Icon }) => (
        <NavLink
          key={to}
          to={to}
          onClick={opTelefoon ? () => setMenuOpen(false) : undefined}
          title={ingeklapt && !opTelefoon ? label : undefined}
          className={({ isActive }) =>
            // min-h-11: onder ongeveer 44px wordt een knop op een touchscreen
            // een gokje.
            `mb-0.5 flex min-h-11 items-center gap-3 rounded-md px-3 text-sm font-medium transition-colors ${
              ingeklapt && !opTelefoon ? "justify-center px-0" : ""
            } ${
              isActive
                ? "bg-sidebar-accent/15 text-sidebar-accent"
                : "text-sidebar-foreground/75 hover:bg-sidebar-accent/10 hover:text-sidebar-foreground"
            }`
          }
        >
          <Icon size={18} className="shrink-0" />
          {(!ingeklapt || opTelefoon) && <span className="truncate">{label}</span>}
          {to === "/chat" && runningCount > 0 && (
            // Ingeklapt is er geen ruimte voor een getal, en een absoluut
            // geplaatste badge zonder gepositioneerde ouder landt op een
            // onvoorspelbare plek. Een stip zegt genoeg: er loopt iets.
            ingeklapt && !opTelefoon ? (
              <span className="h-2 w-2 shrink-0 rounded-full bg-sidebar-accent"
                    title={`${runningCount} lopende achtergrondtaak/-taken`} />
            ) : (
              <span
                className="ml-auto inline-flex h-5 min-w-5 items-center justify-center
                           rounded-full bg-sidebar-accent px-1.5 text-[11px] font-bold text-white"
                title={`${runningCount} lopende achtergrondtaak/-taken`}
              >
                {runningCount}
              </span>
            )
          )}
        </NavLink>
      ))}
    </nav>
  );

  // Thema en uitloggen stonden hier; die zitten nu rechts in de bovenbalk, waar
  // ze op élke pagina op dezelfde plek staan. Wat overblijft is wie je bent en
  // welke versie je draait -- dat hoort bij het menu en niet bij de handelingen.
  const onderkant = (opTelefoon: boolean) =>
    !ingeklapt || opTelefoon ? (
      <div className="flex shrink-0 items-center justify-between gap-2 border-t border-sidebar-border
                      px-3 py-2 text-xs text-sidebar-foreground/60">
        <span className="truncate">{username}</span>
        <Versie />
      </div>
    ) : null;

  return (
    <div className="flex h-app w-full overflow-hidden bg-background text-foreground">
      {/* ── Het menu op een breed scherm ──────────────────────────────────── */}
      <aside className={`veilig-boven hidden shrink-0 flex-col border-r border-sidebar-border
                         bg-sidebar text-sidebar-foreground transition-[width] duration-150
                         lg:flex ${ingeklapt ? "w-16" : "w-60"}`}>
        <div className={`flex shrink-0 items-center gap-2 px-3 py-4 text-lg font-bold tracking-tight ${
          ingeklapt ? "justify-center px-0" : ""}`}>
          <span className="inline-block h-2 w-2 shrink-0 rounded-full bg-sidebar-accent" />
          {!ingeklapt && "LabX"}
        </div>
        {menu(false)}
        <button
          onClick={() => zetIngeklapt(!ingeklapt)}
          aria-label={ingeklapt ? "Menu uitklappen" : "Menu inklappen"}
          title={ingeklapt ? "Uitklappen" : "Inklappen"}
          className="flex h-9 shrink-0 items-center justify-center border-t border-sidebar-border
                     text-sidebar-foreground/50 hover:bg-sidebar-accent/10 hover:text-sidebar-foreground"
        >
          {ingeklapt ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
        </button>
        {onderkant(false)}
      </aside>

      {/* ── Inhoud, met overal dezelfde balk erboven ──────────────────────── */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* De balk beantwoordt links één vraag -- waar ben ik? -- en houdt
            rechts de handelingen die niets met de pagina te maken hebben: hoe
            het eruitziet, en eruit. Op elke pagina op dezelfde plek, want een
            knop die verspringt moet je elke keer opnieuw zoeken. */}
        <header className="veilig-boven flex shrink-0 items-center gap-2 border-b border-sidebar-border
                           bg-sidebar px-2 text-sidebar-foreground sm:px-3">
          <button
            type="button"
            onClick={() => setMenuOpen(true)}
            aria-label="Menu openen"
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-md hover:bg-sidebar-accent/10 lg:hidden"
          >
            <Menu size={20} />
          </button>

          <div className="flex min-w-0 flex-1 flex-col justify-center py-2">
            <span className="flex min-w-0 items-center gap-2">
              <span className="truncate text-sm font-semibold leading-tight">
                {pagina?.label || "LabX"}
              </span>
              {runningCount > 0 && (
                <span className="inline-flex h-4 min-w-4 shrink-0 items-center justify-center rounded-full
                                 bg-sidebar-accent px-1 text-[10px] font-bold text-white"
                      title={`${runningCount} lopende achtergrondtaak/-taken`}>
                  {runningCount}
                </span>
              )}
            </span>
            {pagina && (
              <span className="truncate text-[11px] leading-tight text-sidebar-foreground/55">
                {pagina.onder}
              </span>
            )}
          </div>

          <ThemaKiezer />
          <button
            onClick={logout}
            title="Uitloggen"
            aria-label="Uitloggen"
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md
                       text-sidebar-foreground/50 hover:bg-sidebar-accent/10 hover:text-sidebar-foreground"
          >
            <LogOut size={15} />
          </button>
        </header>

        <main className="min-h-0 min-w-0 flex-1 overflow-x-hidden overflow-y-auto">
          <Outlet />
        </main>
      </div>

      {/* ── Hetzelfde menu, uitgeschoven op een telefoon ──────────────────── */}
      {menuOpen && (
        <div className="lg:hidden">
          <button
            type="button"
            aria-label="Menu sluiten"
            onClick={() => setMenuOpen(false)}
            className="fixed inset-0 z-40 bg-foreground/40"
          />
          <aside className="veilig-boven veilig-onder fixed inset-y-0 left-0 z-50 flex w-72 max-w-[85vw]
                            flex-col border-r border-sidebar-border bg-sidebar
                            text-sidebar-foreground shadow-2xl">
            <div className="flex shrink-0 items-center gap-2 px-3 py-4 text-lg font-bold tracking-tight">
              <span className="inline-block h-2 w-2 rounded-full bg-sidebar-accent" />
              LabX
              <button
                onClick={() => setMenuOpen(false)}
                aria-label="Menu sluiten"
                className="ml-auto flex h-11 w-11 items-center justify-center rounded-md
                           text-sidebar-foreground/70 hover:bg-sidebar-accent/10"
              >
                <X size={20} />
              </button>
            </div>
            {menu(true)}
            {onderkant(true)}
          </aside>
        </div>
      )}
    </div>
  );
}
