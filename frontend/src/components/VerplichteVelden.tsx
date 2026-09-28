/**
 * components/VerplichteVelden.tsx — vaste waarden voor velden die de bron
 * verplicht stelt.
 *
 * Aanleiding: het BICC-project van Swinkels eist bij een Task2 een
 * story-point-schatting en bij een Epic een eigen stream-veld. LabX kent die
 * velden niet, en zonder een plek om er een waarde voor te zetten is zo'n
 * issuetype dus onbruikbaar — een rare reden om geen ticket te kunnen
 * aanmaken.
 *
 * Bewust géén JSON-veld waar je `{"customfield_10052": 0}` in tikt: die
 * veld-id's zijn niet te onthouden en een typefout levert een 400 op waar
 * niets in staat. De velden komen uit de bron, met hun eigen naam ervoor.
 */
import { useMemo } from "react";
import { useItemSoorten } from "@/components/SoortKiezer";
import { Input, Label, Select } from "@/components/ui";

export function VerplichteVelden({ boardId, waarden, onChange }: {
  boardId: number | null;
  waarden: Record<string, unknown>;
  onChange: (w: Record<string, unknown>) => void;
}) {
  const { soorten } = useItemSoorten(boardId);

  // Eén regel per veld, ook als meerdere soorten hetzelfde veld eisen.
  const velden = useMemo(() => {
    const per: Record<string, { veld: string; naam: string; soort?: string;
                                keuzes?: string[]; bij: string[] }> = {};
    for (const s of soorten) {
      for (const v of s.verplicht || []) {
        const bestaand = per[v.veld];
        if (bestaand) bestaand.bij.push(s.naam);
        else per[v.veld] = { ...v, bij: [s.naam] };
      }
    }
    return Object.values(per);
  }, [soorten]);

  if (!velden.length) return null;

  return (
    <div className="space-y-2 rounded-md border border-border p-2">
      <Label>Vaste waarden voor verplichte velden</Label>
      <p className="text-[11px] text-muted-foreground">
        Deze velden stelt de bron verplicht, en LabX kent ze niet. Vul ze hier één keer in;
        ze gaan alleen mee bij het <strong>aanmaken</strong> van een nieuw item — bij een
        wijziging zou je er een waarde overheen zetten die iemand daar bewust veranderde.
      </p>
      {velden.map((v) => (
        <div key={v.veld}>
          <Label>
            {v.naam}
            <span className="ml-2 font-normal text-muted-foreground">
              bij {v.bij.join(", ")}
            </span>
          </Label>
          {v.keuzes?.length ? (
            <Select value={String(waarden[v.veld] ?? "")}
                    onChange={(e) => onChange({ ...waarden, [v.veld]: e.target.value })}>
              <option value="">— niet invullen —</option>
              {v.keuzes.map((k) => <option key={k} value={k}>{k}</option>)}
            </Select>
          ) : (
            <Input
              type={v.soort === "number" ? "number" : "text"}
              value={String(waarden[v.veld] ?? "")}
              placeholder={v.soort === "array" ? "meerdere? scheid met komma's" : ""}
              onChange={(e) => onChange({ ...waarden, [v.veld]: e.target.value })}
            />
          )}
          <p className="mt-0.5 text-[10px] text-muted-foreground">
            <code>{v.veld}</code>{v.soort ? ` · ${v.soort}` : ""}
          </p>
        </div>
      ))}
    </div>
  );
}
