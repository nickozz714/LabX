/**
 * components/Meldingen.tsx — korte terugkoppeling rechtsonder in beeld.
 *
 * Aanleiding: "vaak klik ik op een knop en dan lijkt het alsof er niets
 * gebeurt, maar naderhand verandert er wel wat." Dat klopte. Een knop die een
 * verzoek naar de server stuurt gaf geen enkel teken van leven — geen spinner
 * tijdens, geen bevestiging erna — en bij het synchroniseren van MCP-tools duurt
 * dat verzoek tientallen seconden. Wie niets ziet gebeuren, klikt nog eens.
 *
 * Twee helften, en ze horen bij elkaar:
 *  - de knop zelf toont dát hij bezig is (zie ui.tsx: Button herkent een
 *    onClick die een Promise teruggeeft en zet zichzelf op bezig);
 *  - dit bestand toont wat het RESULTAAT was, ook als dat scherm inmiddels
 *    ergens anders staat.
 *
 * Een fout verdwijnt NIET vanzelf. Een bevestiging wel — die heb je gezien of
 * niet, en dan is hij niet meer interessant. Een foutmelding is het enige
 * spoor van iets dat niet gebeurd is; die blijft staan tot je hem wegklikt.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { CheckCircle2, Info, X, XCircle } from "lucide-react";

export type MeldingSoort = "ok" | "fout" | "info";

type Melding = { id: number; soort: MeldingSoort; tekst: string; detail?: string };

type MeldingApi = {
  ok: (tekst: string, detail?: string) => void;
  fout: (tekst: string, detail?: string) => void;
  info: (tekst: string, detail?: string) => void;
};

const Ctx = createContext<MeldingApi | null>(null);

/** Buiten React ook bruikbaar: de Button in ui.tsx meldt een mislukte actie
 *  zonder een hook te kunnen aanroepen. Wordt door de provider gevuld. */
let extern: MeldingApi | null = null;
export function meldExtern(soort: MeldingSoort, tekst: string, detail?: string) {
  extern?.[soort](tekst, detail);
}

export function useMelding(): MeldingApi {
  const api = useContext(Ctx);
  // Geen provider (een losse test, een stuk UI buiten de app): dan is stil
  // blijven beter dan omvallen.
  return api ?? { ok: () => {}, fout: () => {}, info: () => {} };
}

const KLEUREN: Record<MeldingSoort, string> = {
  ok: "border-emerald-500/40 bg-emerald-500/10",
  fout: "border-destructive/50 bg-destructive/10",
  info: "border-border bg-card",
};

function Icoon({ soort }: { soort: MeldingSoort }) {
  if (soort === "ok") return <CheckCircle2 size={15} className="mt-0.5 shrink-0 text-emerald-500" />;
  if (soort === "fout") return <XCircle size={15} className="mt-0.5 shrink-0 text-destructive" />;
  return <Info size={15} className="mt-0.5 shrink-0 text-muted-foreground" />;
}

export function MeldingProvider({ children }: { children: ReactNode }) {
  const [meldingen, setMeldingen] = useState<Melding[]>([]);

  const voegToe = useCallback((soort: MeldingSoort, tekst: string, detail?: string) => {
    const id = Date.now() + Math.random();
    setMeldingen((prev) => [...prev.slice(-4), { id, soort, tekst, detail }]);
    if (soort !== "fout") {
      window.setTimeout(() => setMeldingen((p) => p.filter((m) => m.id !== id)), 4000);
    }
  }, []);

  const api = useMemo<MeldingApi>(() => ({
    ok: (t, d) => voegToe("ok", t, d),
    fout: (t, d) => voegToe("fout", t, d),
    info: (t, d) => voegToe("info", t, d),
  }), [voegToe]);

  useEffect(() => {
    extern = api;
    return () => { extern = null; };
  }, [api]);

  return (
    <Ctx.Provider value={api}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2">
        {meldingen.map((m) => (
          <div key={m.id}
               className={`pointer-events-auto flex items-start gap-2 rounded-md border p-2.5 text-sm shadow-lg ${KLEUREN[m.soort]}`}>
            <Icoon soort={m.soort} />
            <div className="min-w-0 flex-1">
              <div className="font-medium">{m.tekst}</div>
              {m.detail && (
                <div className="mt-0.5 break-words text-xs text-muted-foreground">{m.detail}</div>
              )}
            </div>
            <button onClick={() => setMeldingen((p) => p.filter((x) => x.id !== m.id))}
                    className="text-muted-foreground hover:text-foreground" title="Sluiten">
              <X size={13} />
            </button>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}
