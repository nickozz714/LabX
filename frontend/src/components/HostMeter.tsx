/**
 * components/HostMeter.tsx — hoe staat de machine ervoor?
 *
 * Staat op de Labs-pagina omdat het daar de beslissing raakt: een werker
 * erbij zetten kan alleen als de host het aankan. De aanleiding was concreet
 * — de server draaide met een load van 4.28 op vier kernen en de swap voor
 * 100% vol, terwijl er in LabX niets te zien was dat daar iets over zei.
 *
 * Twee getallen naast elkaar, en dat is met opzet: wat er NU op staat, en wat
 * de draaiende labs bij elkaar CLAIMEN. Dat tweede kan de machine ruim
 * overstijgen zonder dat er iets misgaat — tot alle labs tegelijk gaan werken.
 */
import { useEffect, useState } from "react";
import { labsApi, type HostMetrics } from "@/lib/labs";
import { Badge, Card } from "@/components/ui";

function gb(bytes: number): string {
  return `${(bytes / 1e9).toFixed(1)} GB`;
}

const TOON: Record<string, "red" | "yellow" | "neutral"> = {
  hoog: "red", midden: "yellow", laag: "neutral",
};

/** Een balk met een kleur die omslaat waar het knijpt. Dezelfde grenzen als
 *  de backend hanteert, zodat de kleur en de waarschuwing elkaar niet
 *  tegenspreken. */
function Balk({ deel, label, rechts }: { deel: number | null; label: string; rechts: string }) {
  const pct = Math.max(0, Math.min(1, deel ?? 0));
  const kleur = pct >= 0.95 ? "bg-destructive" : pct >= 0.85 ? "bg-yellow-500" : "bg-primary";
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between text-xs">
        <span className="font-medium">{label}</span>
        <span className="text-muted-foreground">{rechts}</span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-secondary">
        <div className={`h-full rounded-full ${kleur}`} style={{ width: `${pct * 100}%` }} />
      </div>
    </div>
  );
}

export function HostMeter() {
  const [m, setM] = useState<HostMetrics | null>(null);
  const [fout, setFout] = useState<string | null>(null);

  useEffect(() => {
    let gestopt = false;
    const haal = () =>
      labsApi.hostMetrics()
        .then((d) => !gestopt && (setM(d), setFout(null)))
        .catch((e) => !gestopt && setFout(String(e?.message || e)));
    haal();
    // Twintig seconden: vaak genoeg om een oplopende load te zien, zeldzaam
    // genoeg om zelf geen belasting te zijn op de machine die we meten.
    const t = setInterval(haal, 20000);
    return () => { gestopt = true; clearInterval(t); };
  }, []);

  if (fout) return <Card className="p-3 text-xs text-muted-foreground">Hostmeting niet beschikbaar: {fout}</Card>;
  if (!m) return null;

  const { geheugen: g, cpu, labs, schijf } = m;
  const swapDeel = g.swap_totaal ? g.swap_gebruikt / g.swap_totaal : 0;

  return (
    <Card className="space-y-3 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold">Machine</span>
        <span className="text-xs text-muted-foreground">
          {labs.labs_draaiend} lab{labs.labs_draaiend === 1 ? "" : "s"} draaiend · samen goed voor{" "}
          {gb(labs.geheugen)} en {labs.cpu.toFixed(1)} CPU toegezegd
        </span>
        {m.waarschuwingen.length === 0 && <Badge tone="green">ruimte zat</Badge>}
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Balk deel={g.deel_gebruikt} label="Geheugen"
              rechts={`${gb(g.gebruikt)} / ${gb(g.totaal)}`} />
        <Balk deel={cpu.load_per_kern} label="CPU (load per kern)"
              rechts={`${(cpu.load_per_kern ?? 0).toFixed(2)} · ${cpu.kernen} kernen`} />
        {g.swap_totaal > 0 && (
          <Balk deel={swapDeel} label="Swap" rechts={`${gb(g.swap_gebruikt)} / ${gb(g.swap_totaal)}`} />
        )}
        {schijf && (
          <Balk deel={schijf.gebruikt / schijf.totaal} label={`Schijf (${schijf.pad})`}
                rechts={`${gb(schijf.vrij)} vrij`} />
        )}
      </div>

      {m.gpu.length > 0 && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {m.gpu.map((kaart, i) => (
            <Balk key={i} deel={kaart.geheugen_gebruikt / kaart.geheugen_totaal}
                  label={`GPU — ${kaart.naam}`}
                  rechts={`${gb(kaart.geheugen_gebruikt)} / ${gb(kaart.geheugen_totaal)} · ${
                    (kaart.bezet * 100).toFixed(0)}% · ${kaart.temperatuur.toFixed(0)}°C`} />
          ))}
        </div>
      )}

      {m.waarschuwingen.map((w, i) => (
        <div key={i} className="flex items-start gap-2 rounded-md border border-border p-2 text-xs">
          <Badge tone={TOON[w.ernst] || "neutral"}>{w.onderwerp}</Badge>
          <span className="text-muted-foreground">{w.tekst}</span>
        </div>
      ))}

      {labs.per_lab.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-muted-foreground">Wat claimt welk lab?</summary>
          <div className="mt-2 space-y-1">
            {labs.per_lab.map((l) => (
              <div key={l.id} className="flex justify-between">
                <span>{l.naam} <span className="text-muted-foreground">
                  ({l.werkers} werker{l.werkers === 1 ? "" : "s"})</span></span>
                <span className="text-muted-foreground">{gb(l.geheugen)} · {l.cpu.toFixed(1)} CPU</span>
              </div>
            ))}
          </div>
        </details>
      )}
    </Card>
  );
}
