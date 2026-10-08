/**
 * De driestandenschakelaar: licht, donker, of meebewegen met het systeem.
 *
 * Drie knopjes naast elkaar en geen uitklapmenu: het zijn er drie, ze passen,
 * en je ziet in één blik welke aanstaat. Een menu zou hier een klik extra
 * kosten om iets te doen wat je zelden maar wel snel wilt.
 */
import { useEffect, useState } from "react";
import { Monitor, Moon, Sun } from "lucide-react";

import { gekozenThema, pasToe, volgSysteem, zetThema } from "@/lib/thema";
import type { Thema } from "@/lib/thema";

const STANDEN: { waarde: Thema; label: string; Icoon: typeof Sun }[] = [
  { waarde: "licht", label: "Licht", Icoon: Sun },
  { waarde: "donker", label: "Donker", Icoon: Moon },
  { waarde: "auto", label: "Volg het systeem", Icoon: Monitor },
];

export function ThemaKiezer({ compact = false }: { compact?: boolean }) {
  const [thema, setThemaState] = useState<Thema>(() => gekozenThema());

  // Bij "auto" blijven luisteren: zet je Mac 's avonds om, dan gaat de app mee
  // zonder dat je hoeft te herladen.
  useEffect(() => {
    pasToe(thema);
    return volgSysteem(thema, () => pasToe(thema));
  }, [thema]);

  function kies(waarde: Thema) {
    zetThema(waarde);
    setThemaState(waarde);
  }

  return (
    <div className={`flex items-center gap-0.5 ${compact ? "justify-center" : ""}`}>
      {STANDEN.map(({ waarde, label, Icoon }) => (
        <button
          key={waarde}
          onClick={() => kies(waarde)}
          title={label}
          aria-label={label}
          aria-pressed={thema === waarde}
          className={`flex h-8 w-8 items-center justify-center rounded-md transition-colors ${
            thema === waarde
              ? "bg-sidebar-accent/20 text-sidebar-accent"
              : "text-sidebar-foreground/50 hover:bg-sidebar-accent/10 hover:text-sidebar-foreground"
          }`}
        >
          <Icoon size={15} />
        </button>
      ))}
    </div>
  );
}
