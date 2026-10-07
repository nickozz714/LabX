/**
 * Geheugensteun.tsx — opzoeken terwijl je praat.
 *
 * Het probleem dat dit oplost: je weet dat het ticket over de zilveren
 * pipelines ging, maar niet meer hoe het heet, en dan val je stil midden in
 * een zin. Dit paneel laat je dat aflezen zonder de sessie te verlaten.
 *
 * Bewust alleen namen, sleutels en toestanden -- hetzelfde soort informatie
 * dat de leesacties ook teruggeven. Geen klantinhoud: dit is een spiekbriefje,
 * geen tweede kanaal om gegevens uit het systeem te halen.
 */
import { useEffect, useState } from "react";
import { Search, X } from "lucide-react";

import { spraakApi } from "@/lib/spraak";
import type { SpraakVondst } from "@/lib/spraak";

const SOORT_KLEUR: Record<string, string> = {
  ticket: "text-sky-300",
  bord: "text-violet-300",
  lab: "text-emerald-300",
  chat: "text-amber-300",
};

export function Geheugensteun({ open, sluit }: { open: boolean; sluit: () => void }) {
  const [term, setTerm] = useState("");
  const [vondsten, setVondsten] = useState<SpraakVondst[]>([]);
  const [bezig, setBezig] = useState(false);

  // Even wachten met zoeken: bij elke toetsaanslag een verzoek sturen levert
  // vooral antwoorden op die al niet meer kloppen als ze binnenkomen.
  useEffect(() => {
    if (!open) return;
    setBezig(true);
    const t = setTimeout(() => {
      spraakApi.opzoeken(term)
        .then(setVondsten)
        .catch(() => setVondsten([]))
        .finally(() => setBezig(false));
    }, 220);
    return () => clearTimeout(t);
  }, [term, open]);

  if (!open) return null;

  return (
    <>
      {/* Op een telefoon bedekt het paneel het scherm; dan moet je ernaast
          kunnen tikken om terug te gaan naar het gesprek. */}
      <button
        aria-label="Sluiten"
        onClick={sluit}
        className="fixed inset-0 z-30 bg-black/50 lg:hidden"
      />
      <aside className="fixed inset-y-0 right-0 z-40 flex w-full max-w-sm flex-col
                        border-l border-white/10 bg-[#0b1220] text-slate-200
                        shadow-2xl lg:static lg:z-auto lg:w-80 lg:shadow-none">
        <div className="flex items-center gap-2 border-b border-white/10 px-3 py-3">
          <Search size={15} className="shrink-0 text-slate-400" />
          <input
            autoFocus
            value={term}
            onChange={(e) => setTerm(e.target.value)}
            placeholder="Ticket, bord, lab of chat…"
            className="min-w-0 flex-1 bg-transparent text-sm text-slate-100
                       placeholder:text-slate-500 focus:outline-none"
          />
          <button onClick={sluit} aria-label="Sluiten"
                  className="rounded p-1 text-slate-400 hover:bg-white/10">
            <X size={15} />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {bezig && !vondsten.length && (
            <p className="p-3 text-xs text-slate-500">Zoeken…</p>
          )}
          {!bezig && !vondsten.length && (
            <p className="p-3 text-xs text-slate-500">
              Niets gevonden. Typ een deel van een naam — of laat het leeg voor
              de laatste items.
            </p>
          )}
          {vondsten.map((v, i) => (
            <div key={`${v.soort}-${v.id}-${i}`}
                 className="border-b border-white/5 px-3 py-2 last:border-0">
              <div className="flex items-baseline gap-2">
                <span className={`text-[10px] font-semibold uppercase tracking-wide ${
                  SOORT_KLEUR[v.soort] || "text-slate-400"}`}>
                  {v.soort}
                </span>
                {v.sleutel && (
                  <span className="font-mono text-xs text-slate-300">{v.sleutel}</span>
                )}
                {v.detail && (
                  <span className="ml-auto text-[10px] text-slate-500">{v.detail}</span>
                )}
              </div>
              <div className="mt-0.5 break-words text-sm text-slate-100">{v.naam}</div>
            </div>
          ))}
        </div>
      </aside>
    </>
  );
}
