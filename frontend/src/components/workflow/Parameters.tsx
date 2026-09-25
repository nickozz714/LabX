/**
 * components/workflow/Parameters.tsx — de invoer die een workflow verwacht.
 *
 * Aanleiding: een workflow die "de incidenten van Swinkels" ophaalt, had die
 * klantnaam in de opdracht van elke activiteit staan. Voor de volgende klant
 * kopieerde je hem, en daarna liepen er twee uit elkaar.
 *
 * Twee kanten, allebei hier:
 * - `ParameterBouwer` — wát een workflow verwacht (naam, soort, standaard).
 * - `ParameterInvuller` — de waarden voor één run of één schedule.
 *
 * Een parameter is bewust iets kleins: een naam, wat het is, en een
 * standaardwaarde. Geen expressies en geen afleidingen; dat is precies het
 * soort veld waar je later niet meer uitkomt.
 */
import { Button, Input, Label, Select } from "@/components/ui";
import type { WorkflowParameter } from "@/lib/types";

const SOORTEN: WorkflowParameter["soort"][] = ["tekst", "getal", "waar/onwaar", "keuze"];

const LEEG: WorkflowParameter = {
  naam: "", soort: "tekst", omschrijving: "", standaard: null, verplicht: false, opties: [],
};

/** Dezelfde eis als de server: hier zichtbaar, zodat je niet pas na het
 *  opslaan merkt dat je rij verdwenen is. */
function naamFout(naam: string): string | null {
  if (!naam.trim()) return "Zonder naam kan er niet naar verwezen worden.";
  if (!/^[a-z][a-z0-9_]{0,63}$/.test(naam))
    return "Alleen kleine letters, cijfers en _ , beginnend met een letter.";
  return null;
}

export function ParameterBouwer({ waarde, onChange }: {
  waarde: WorkflowParameter[];
  onChange: (p: WorkflowParameter[]) => void;
}) {
  function zet(i: number, bij: Partial<WorkflowParameter>) {
    onChange(waarde.map((p, j) => (j === i ? { ...p, ...bij } : p)));
  }

  return (
    <div className="space-y-2">
      {waarde.length === 0 && (
        <p className="text-[11px] text-muted-foreground">
          Nog geen invoer. Voeg er een toe en gebruik hem in elke activiteit als{" "}
          <code>{"{{ invoer.<naam> }}"}</code> — dan hoef je de workflow niet te kopiëren
          voor de volgende klant.
        </p>
      )}
      {waarde.map((p, i) => {
        const fout = naamFout(p.naam);
        return (
          <div key={i} className="space-y-1 rounded-md border border-border p-2">
            <div className="flex flex-wrap items-center gap-1">
              <Input value={p.naam} placeholder="klant" className="w-36 font-mono"
                     onChange={(e) => zet(i, { naam: e.target.value.toLowerCase() })} />
              <Select value={p.soort} className="w-32"
                      onChange={(e) => zet(i, {
                        soort: e.target.value as WorkflowParameter["soort"] })}>
                {SOORTEN.map((s) => <option key={s} value={s}>{s}</option>)}
              </Select>
              <label className="flex items-center gap-1 text-[11px] text-muted-foreground">
                <input type="checkbox" checked={p.verplicht}
                       onChange={(e) => zet(i, { verplicht: e.target.checked })} />
                verplicht
              </label>
              <span className="flex-1" />
              <button onClick={() => onChange(waarde.filter((_, j) => j !== i))}
                      className="px-1 text-xs text-muted-foreground hover:text-foreground">✕</button>
            </div>
            <Input value={p.omschrijving} className="text-xs"
                   placeholder="waar dit voor is — je leest dit terug bij het starten"
                   onChange={(e) => zet(i, { omschrijving: e.target.value })} />
            {p.soort === "keuze" ? (
              <Input value={(p.opties || []).join(", ")} className="text-xs"
                     placeholder="dev, acc, prd"
                     onChange={(e) => zet(i, {
                       opties: e.target.value.split(",").map((o) => o.trim()).filter(Boolean) })} />
            ) : (
              <Input value={p.standaard === null ? "" : String(p.standaard)} className="text-xs"
                     placeholder="standaardwaarde (mag leeg)"
                     onChange={(e) => zet(i, { standaard: e.target.value })} />
            )}
            {p.soort === "keuze" && (
              <Select value={p.standaard === null ? "" : String(p.standaard)} className="text-xs"
                      onChange={(e) => zet(i, { standaard: e.target.value || null })}>
                <option value="">— geen standaard —</option>
                {(p.opties || []).map((o) => <option key={o} value={o}>{o}</option>)}
              </Select>
            )}
            {fout && <p className="text-[11px] text-amber-600">{fout}</p>}
            {!fout && (
              <p className="text-[11px] text-muted-foreground">
                Te gebruiken als <code>{`{{ invoer.${p.naam} }}`}</code>
              </p>
            )}
          </div>
        );
      })}
      <Button variant="secondary" className="text-xs"
              onClick={() => onChange([...waarde, { ...LEEG }])}>+ Invoer</Button>
    </div>
  );
}

/** De waarden voor één run of één schedule. Leeg laten mag: dan geldt de
 *  standaardwaarde, en dat staat er ook bij. */
export function ParameterInvuller({ parameters, waarden, onChange }: {
  parameters: WorkflowParameter[];
  waarden: Record<string, string>;
  onChange: (w: Record<string, string>) => void;
}) {
  if (!parameters.length) return null;
  return (
    <div className="space-y-2">
      {parameters.map((p) => {
        const waarde = waarden[p.naam] ?? "";
        const standaard = p.standaard === null ? "" : String(p.standaard);
        return (
          <div key={p.naam}>
            <Label>
              {p.naam}{p.verplicht && <span className="ml-1 text-amber-600">*</span>}
            </Label>
            {p.soort === "keuze" ? (
              <Select value={waarde} onChange={(e) => onChange({ ...waarden, [p.naam]: e.target.value })}>
                <option value="">{standaard ? `standaard: ${standaard}` : "— kies —"}</option>
                {(p.opties || []).map((o) => <option key={o} value={o}>{o}</option>)}
              </Select>
            ) : p.soort === "waar/onwaar" ? (
              <Select value={waarde} onChange={(e) => onChange({ ...waarden, [p.naam]: e.target.value })}>
                <option value="">{standaard ? `standaard: ${standaard}` : "— kies —"}</option>
                <option value="ja">ja</option>
                <option value="nee">nee</option>
              </Select>
            ) : (
              <Input value={waarde} type={p.soort === "getal" ? "number" : "text"}
                     placeholder={standaard ? `standaard: ${standaard}` : ""}
                     onChange={(e) => onChange({ ...waarden, [p.naam]: e.target.value })} />
            )}
            {p.omschrijving && (
              <p className="mt-1 text-[11px] text-muted-foreground">{p.omschrijving}</p>
            )}
          </div>
        );
      })}
    </div>
  );
}
