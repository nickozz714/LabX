/**
 * components/OrchestratorKnop.tsx — "de orchestrator luistert nog".
 *
 * Een lopende sessie die je niet ziet, is een lopende sessie die je vergeet:
 * hij kost geld, en bij een open microfoon neemt hij op terwijl je ergens
 * anders bent. Daarom staat hij in de balk zodra hij leeft, op elke pagina.
 *
 * Twee toestanden, met opzet verschillend van vorm en niet alleen van kleur:
 * een sessie die er IS (rustig, klikbaar om terug te gaan) en een sessie die
 * LUISTERT (pulserend, met stop binnen één klik). Dat onderscheid is de hele
 * reden dat dit knopje bestaat.
 */
import { useNavigate } from "react-router-dom";
import { Mic, MicOff, MessageCircle } from "lucide-react";
import { useOrchestrator } from "@/contexts/OrchestratorContext";

export function OrchestratorKnop() {
  const { sessie, luistert, stop } = useOrchestrator();
  const navigate = useNavigate();

  if (!sessie) return null;

  return (
    <div className="flex shrink-0 items-center gap-1">
      <button
        onClick={() => navigate("/orchestrator")}
        title={luistert
          ? "De orchestrator luistert mee — klik om terug te gaan"
          : "De orchestrator staat aan — klik om terug te gaan"}
        aria-label="Naar de orchestrator"
        className={`flex h-8 items-center gap-1.5 rounded-md px-2 text-xs font-medium transition
                    ${luistert
                      ? "bg-destructive/15 text-destructive"
                      : "text-sidebar-foreground/70 hover:bg-sidebar-accent/10 hover:text-sidebar-foreground"}`}
      >
        {luistert ? (
          <>
            {/* Pulseren, want "er wordt nu opgenomen" moet je in je ooghoek
                zien en niet hoeven lezen. */}
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-destructive/70" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-destructive" />
            </span>
            <Mic size={14} />
            <span className="hidden sm:inline">luistert</span>
          </>
        ) : (
          <>
            <MessageCircle size={15} />
            <span className="hidden sm:inline tabular-nums">
              ${sessie.kosten_usd.toFixed(2)}
            </span>
          </>
        )}
      </button>

      {luistert && (
        <button
          onClick={() => { void stop(); }}
          title="Sessie stoppen"
          aria-label="Sessie stoppen"
          className="flex h-8 w-8 items-center justify-center rounded-md
                     text-destructive/80 hover:bg-destructive/10 hover:text-destructive"
        >
          <MicOff size={15} />
        </button>
      )}
    </div>
  );
}
