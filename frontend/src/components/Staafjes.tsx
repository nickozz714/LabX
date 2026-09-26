/**
 * components/Staafjes.tsx — een staafdiagram zonder grafiekbibliotheek.
 *
 * Het gaat hier om één ding: hoeveel was er per dag, week of maand. Daar is
 * een <div> met een hoogte genoeg voor, en dat scheelt een afhankelijkheid van
 * een paar honderd kilobyte die verder niets doet.
 *
 * Twee keuzes die het leesbaar houden:
 * - Een leeg vakje blijft staan (als streepje), want een dag zonder werk is
 *   informatie. Hem weglaten zou de grafiek over het tempo laten liegen.
 * - De schaal komt uit de hoogste waarde, en die staat er in cijfers bij.
 *   Een staafdiagram zonder getal is een plaatje.
 */
import { useState } from "react";

export type Staaf = {
  label: string;
  waarde: number;
  /** Het deel van de waarde dat misging; wordt in dezelfde staaf getekend. */
  fouten?: number;
  /** Wat er in de tooltip komt, bovenop het getal. */
  detail?: string;
};

export function Staafjes({ staven, eenheid = "", hoogte = 120 }: {
  staven: Staaf[];
  eenheid?: string;
  hoogte?: number;
}) {
  const [over, setOver] = useState<number | null>(null);
  const top = Math.max(1, ...staven.map((s) => s.waarde));

  return (
    <div>
      <div className="flex items-end gap-1" style={{ height: hoogte }}>
        {staven.map((s, i) => {
          const h = Math.round((s.waarde / top) * (hoogte - 18));
          const fout = Math.round(((s.fouten || 0) / top) * (hoogte - 18));
          return (
            <div key={i} className="flex flex-1 flex-col items-center justify-end"
                 onMouseEnter={() => setOver(i)} onMouseLeave={() => setOver(null)}>
              {over === i && (
                <div className="mb-1 whitespace-nowrap rounded bg-foreground px-1.5 py-0.5
                                text-[10px] text-background">
                  {s.waarde}{eenheid ? ` ${eenheid}` : ""}
                  {s.fouten ? ` · ${s.fouten} mis` : ""}
                  {s.detail ? ` · ${s.detail}` : ""}
                </div>
              )}
              {s.waarde === 0 ? (
                <div className="h-0.5 w-full rounded-sm bg-border" title="niets gebeurd" />
              ) : (
                <div className="flex w-full flex-col justify-end rounded-sm"
                     style={{ height: Math.max(3, h) }}>
                  {fout > 0 && (
                    <div className="w-full rounded-t-sm bg-destructive/70"
                         style={{ height: Math.max(2, fout) }} />
                  )}
                  <div className="w-full flex-1 rounded-b-sm bg-primary/70" />
                </div>
              )}
            </div>
          );
        })}
      </div>
      <div className="mt-1 flex gap-1">
        {staven.map((s, i) => (
          <div key={i} className="flex-1 truncate text-center text-[10px] text-muted-foreground">
            {s.label}
          </div>
        ))}
      </div>
    </div>
  );
}
