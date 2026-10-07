/**
 * OrchestratorStart.tsx — het startpunt van de orchestrator, op Overzicht.
 *
 * Geen eigen tab meer: de orchestrator is geen plek waar je heen navigeert
 * maar iets wat je erbij pakt terwijl je kijkt wat er loopt. Vandaar hier, op
 * het scherm waarmee je de dag begint.
 *
 * Staat de functie uit of is er geen sleutel, dan is dit blok er niet --
 * net als de endpoints erachter. Geen uitgegrijsde knop die nergens toe leidt.
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { Orb } from "@/components/Orb";
import { Card } from "@/components/ui";
import { spraakApi } from "@/lib/spraak";
import type { SpraakStatus } from "@/lib/spraak";

export function OrchestratorStart() {
  const [status, setStatus] = useState<SpraakStatus | null>(null);
  const navigeer = useNavigate();

  useEffect(() => {
    spraakApi.status().then(setStatus).catch(() => setStatus({ aan: false }));
  }, []);

  if (!status?.aan) return null;

  return (
    <Card className="flex flex-col items-center gap-4 p-5 sm:flex-row sm:gap-6 sm:p-6">
      <Orb toestand="rust" maat={96} bijschrift={false}
           onClick={() => navigeer("/orchestrator")} />
      <div className="min-w-0 flex-1 text-center sm:text-left">
        <h2 className="text-base font-semibold">Orchestrator</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Praat of typ, en laat agents het werk doen. Vraag wat er loopt, hoe
          het met een ticket staat, of zet er een agent op — een schrijfactie
          vraagt altijd eerst je akkoord.
        </p>
        <div className="mt-3 flex flex-wrap items-center justify-center gap-2 text-xs text-muted-foreground sm:justify-start">
          <button
            onClick={() => navigeer("/orchestrator")}
            className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground"
          >
            Sessie starten
          </button>
          <span>
            {status.brein === "realtime" ? "realtime" : "pijplijn"}
            {status.microfoon === "open" ? " · open microfoon" : " · push-to-talk"}
            {status.vandaag_usd !== undefined
              && ` · $${status.vandaag_usd.toFixed(2)} vandaag`}
          </span>
        </div>
      </div>
    </Card>
  );
}
