/**
 * Gesprek.tsx — de volledige tijdlijn, als paneel.
 *
 * In het hoofdbeeld staan alleen je laatste zin en het antwoord daarop. Dat
 * is wat je tijdens het praten nodig hebt; de rest leidt af van het enige
 * element dat terugkoppelt dat er geluisterd wordt.
 *
 * Maar de hele tijdlijn moet wél te openen zijn: daar staat zwart op wit wat
 * er namens jou gebeurd is, inclusief de toolaanroepen en de bevestigingen.
 * Dat is geen luxe maar de verantwoording.
 */
import { X } from "lucide-react";

import type { SpraakGebeurtenis } from "@/lib/spraak";

export function Gesprek({
  open,
  sluit,
  tijdlijn,
}: {
  open: boolean;
  sluit: () => void;
  tijdlijn: SpraakGebeurtenis[];
}) {
  if (!open) return null;

  return (
    <>
      <button
        aria-label="Sluiten"
        onClick={sluit}
        className="fixed inset-0 z-30 bg-black/50 lg:hidden"
      />
      <aside className="fixed inset-y-0 right-0 z-40 flex w-full max-w-md flex-col
                        border-l border-white/10 bg-[#0b1220] text-slate-200
                        shadow-2xl lg:static lg:z-auto lg:w-96 lg:shadow-none">
        <div className="flex items-center gap-2 border-b border-white/10 px-3 py-3">
          <span className="text-sm font-semibold">Gesprek</span>
          <span className="text-xs text-slate-500">
            {tijdlijn.length} regel{tijdlijn.length === 1 ? "" : "s"}
          </span>
          <button onClick={sluit} aria-label="Sluiten"
                  className="ml-auto rounded p-1 text-slate-400 hover:bg-white/10">
            <X size={15} />
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
          {tijdlijn.map((e) => <Regel key={e.id} e={e} />)}
        </div>
      </aside>
    </>
  );
}

export function Regel({ e }: { e: SpraakGebeurtenis }) {
  const tijd = e.ts.slice(11, 19);

  if (e.soort === "gebruiker") {
    return (
      <div className="flex justify-end">
        <div className="min-w-0 max-w-[88%] break-words rounded-2xl rounded-br-md bg-sky-500/90 px-3 py-2 text-sm text-white">
          {e.tekst}
        </div>
      </div>
    );
  }
  if (e.soort === "assistent") {
    return (
      <div className="flex justify-start">
        <div className="min-w-0 max-w-[88%] break-words rounded-2xl rounded-bl-md border border-white/10 bg-white/[0.07] px-3 py-2 text-sm text-slate-100">
          {e.tekst}
        </div>
      </div>
    );
  }
  if (e.soort === "bevestiging") {
    return (
      <div className="rounded-md border border-amber-400/30 bg-amber-400/10 p-2 text-xs text-amber-100">
        <span className="font-medium">Gevraagd om bevestiging:</span> {e.tekst}
      </div>
    );
  }
  if (e.soort === "actie") {
    return (
      <div className="flex items-start gap-2 px-1 text-[11px] text-slate-500">
        <span className="tabular-nums">{tijd}</span>
        <span className="font-mono text-slate-400">{e.tool}</span>
        {e.resultaat && (
          <span className="min-w-0 flex-1 break-words">— {e.resultaat.slice(0, 160)}</span>
        )}
      </div>
    );
  }
  return (
    <div className="px-1 text-[11px] text-slate-500">
      <span className="tabular-nums">{tijd}</span> {e.tekst}
    </div>
  );
}
