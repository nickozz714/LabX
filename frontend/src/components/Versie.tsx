/**
 * components/Versie.tsx — welke build je voor je hebt.
 *
 * Aanleiding: na een uitrol stond de nieuwe interface op de server, maar liet
 * de browser nog de oude zien. Dat is van buitenaf niet te zien, en om erachter
 * te komen moest je in devtools naar de bestandsnaam van een bundel turen.
 *
 * De vergelijking is het punt, niet het nummer. De interface weet welke versie
 * in haar eigen bundel gebakken is; de server zegt welke híj draait. Lopen die
 * uiteen, dan kijk je naar een oude pagina — en dan zegt het scherm dat, met
 * een knop om hem op te halen.
 */
import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { api } from "@/lib/api";

/** Wat er bij het bouwen in de bundel is gebakken (zie frontend/Dockerfile). */
export const SCHERM_VERSIE = import.meta.env.VITE_LABX_VERSION || "dev";

export function Versie() {
  const [server, setServer] = useState<string | null>(null);

  useEffect(() => {
    api.get<{ versie: string }>("/system/version")
      .then((r) => setServer(r.versie))
      .catch(() => setServer(null));
    // Eén keer bij het laden is genoeg: een nieuwe uitrol zie je zodra je de
    // pagina opnieuw opent, en dat is precies het moment waarop dit ertoe doet.
  }, []);

  const verouderd = Boolean(server && server !== SCHERM_VERSIE
                            && server !== "dev" && SCHERM_VERSIE !== "dev");

  if (verouderd) {
    return (
      <button
        onClick={() => window.location.reload()}
        title={`Dit scherm is ${SCHERM_VERSIE}, de server draait ${server}`}
        className="flex items-center gap-1 rounded-md bg-warning/15 px-2 py-0.5 text-[11px]
                   text-warning hover:bg-warning/25"
      >
        <RefreshCw size={11} />
        {SCHERM_VERSIE} → {server}
      </button>
    );
  }

  return (
    <span className="text-[11px] text-sidebar-foreground/50"
          title={server ? `server: ${server}` : "server onbereikbaar"}>
      {SCHERM_VERSIE}
    </span>
  );
}
