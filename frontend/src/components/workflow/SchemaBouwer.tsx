/**
 * components/workflow/SchemaBouwer.tsx — een uitvoerschema tekenen in plaats
 * van schrijven.
 *
 * Aanleiding: in een echte workflow stond 3074 tekens met de hand getypt
 * JSON-schema. Dat is niet alleen bewerkelijk, het is ook de bron van de
 * volgende fout: verderop moet je naar die velden verwijzen, en één typefout
 * daar levert geen foutmelding op maar een voorwaarde die altijd onwaar is.
 *
 * Hier zet je velden neer met een naam, een soort en een uitleg. Die uitleg is
 * geen franje — het model leest hem, en hij komt terug in de keuzelijst van de
 * `als`-voorwaarde.
 *
 * Wat niet in velden te vatten is (een schema met anyOf, patronen, $ref) laten
 * we met rust: dan blijft de JSON-weergave staan en zegt het scherm waarom.
 */
import { useEffect, useRef, useState } from "react";
import { Button, Input, Label, Select, TextArea } from "@/components/ui";

type Soort = "tekst" | "getal" | "waar/onwaar" | "lijst van tekst" | "object" | "lijst van objecten";

export type Veld = {
  naam: string;
  soort: Soort;
  omschrijving: string;
  verplicht: boolean;
  velden?: Veld[];        // bij object / lijst van objecten
};

const JSON_TYPE: Record<Soort, string> = {
  "tekst": "string", "getal": "number", "waar/onwaar": "boolean",
  "lijst van tekst": "array", "object": "object", "lijst van objecten": "array",
};

/** Velden → JSON-schema (wat de CLI met --json-schema krijgt). */
export function naarSchema(velden: Veld[]): Record<string, unknown> {
  const properties: Record<string, unknown> = {};
  const required: string[] = [];
  for (const v of velden) {
    if (!v.naam.trim()) continue;
    const basis: Record<string, unknown> = { type: JSON_TYPE[v.soort] };
    if (v.omschrijving.trim()) basis.description = v.omschrijving.trim();
    if (v.soort === "lijst van tekst") basis.items = { type: "string" };
    if (v.soort === "object") Object.assign(basis, naarSchema(v.velden || []));
    if (v.soort === "lijst van objecten") basis.items = { type: "object", ...naarSchema(v.velden || []) };
    properties[v.naam.trim()] = basis;
    if (v.verplicht) required.push(v.naam.trim());
  }
  const uit: Record<string, unknown> = { type: "object", properties };
  if (required.length) uit.required = required;
  return uit;
}

/** JSON-schema → velden. Geeft null als het niet in velden te vatten is. */
export function uitSchema(ruw: string | null | undefined): Veld[] | null {
  if (!ruw || !ruw.trim()) return [];
  let schema: any;
  try {
    schema = JSON.parse(ruw);
  } catch {
    return null;
  }
  const lees = (s: any): Veld[] | null => {
    if (!s || s.type !== "object" || !s.properties) return null;
    const verplicht: string[] = Array.isArray(s.required) ? s.required : [];
    const uit: Veld[] = [];
    for (const [naam, veld] of Object.entries<any>(s.properties)) {
      const basis = { naam, omschrijving: veld?.description || "", verplicht: verplicht.includes(naam) };
      if (veld?.type === "string") uit.push({ ...basis, soort: "tekst" });
      else if (veld?.type === "number" || veld?.type === "integer") uit.push({ ...basis, soort: "getal" });
      else if (veld?.type === "boolean") uit.push({ ...basis, soort: "waar/onwaar" });
      else if (veld?.type === "array" && veld.items?.type === "string")
        uit.push({ ...basis, soort: "lijst van tekst" });
      else if (veld?.type === "array" && veld.items?.type === "object") {
        const kinderen = lees(veld.items);
        if (kinderen === null) return null;
        uit.push({ ...basis, soort: "lijst van objecten", velden: kinderen });
      } else if (veld?.type === "object") {
        const kinderen = lees(veld);
        if (kinderen === null) return null;
        uit.push({ ...basis, soort: "object", velden: kinderen });
      } else return null;   // iets dat we niet kunnen tekenen
    }
    return uit;
  };
  return lees(schema);
}

function VeldRij({ veld, diepte, onChange, onWeg }: {
  veld: Veld; diepte: number;
  onChange: (v: Veld) => void;
  onWeg: () => void;
}) {
  const genest = veld.soort === "object" || veld.soort === "lijst van objecten";
  return (
    <div className={`space-y-1 ${diepte ? "ml-3 border-l border-border pl-2" : ""}`}>
      <div className="flex flex-wrap items-center gap-1">
        <Input value={veld.naam} placeholder="veldnaam" className="w-40"
               onChange={(e) => onChange({ ...veld, naam: e.target.value })} />
        <Select value={veld.soort} className="w-44"
                onChange={(e) => onChange({ ...veld, soort: e.target.value as Soort })}>
          {(Object.keys(JSON_TYPE) as Soort[]).map((s) => <option key={s} value={s}>{s}</option>)}
        </Select>
        <label className="flex items-center gap-1 text-[11px] text-muted-foreground">
          <input type="checkbox" checked={veld.verplicht}
                 onChange={(e) => onChange({ ...veld, verplicht: e.target.checked })} />
          verplicht
        </label>
        <button onClick={onWeg} className="px-1 text-xs text-muted-foreground hover:text-foreground">✕</button>
      </div>
      <Input value={veld.omschrijving} placeholder="uitleg — het model leest dit mee"
             className="text-xs"
             onChange={(e) => onChange({ ...veld, omschrijving: e.target.value })} />
      {genest && (
        <div className="space-y-1">
          {(veld.velden || []).map((k, i) => (
            <VeldRij key={i} veld={k} diepte={diepte + 1}
                     onChange={(nw) => onChange({
                       ...veld,
                       velden: (veld.velden || []).map((x, j) => (j === i ? nw : x)) })}
                     onWeg={() => onChange({
                       ...veld,
                       velden: (veld.velden || []).filter((_, j) => j !== i) })} />
          ))}
          <Button variant="ghost" className="text-[11px]"
                  onClick={() => onChange({
                    ...veld,
                    velden: [...(veld.velden || []),
                             { naam: "", soort: "tekst", omschrijving: "", verplicht: false }] })}>
            + veld in {veld.naam || "dit object"}
          </Button>
        </div>
      )}
    </div>
  );
}

export function SchemaBouwer({ waarde, onChange }: {
  waarde: string | null | undefined;
  onChange: (json: string) => void;
}) {
  // De velden staan HIER, en worden niet elke render opnieuw uit de JSON
  // gelezen. Dat laatste deed het eerst, en daardoor kon je niets toevoegen: een
  // veld zonder naam bestaat niet in JSON-schema, dus een verse rij was na de
  // heen-en-terugvertaling meteen weer weg. Je typte in een rij die er niet was.
  const [velden, setVelden] = useState<Veld[]>(() => uitSchema(waarde) || []);
  const [ruw, setRuw] = useState(uitSchema(waarde) === null);
  // De laatste JSON die we ZELF schreven, zodat het teruggekaatste schema de
  // rij waar je in staat te typen niet opnieuw opbouwt.
  const eigen = useRef<string>(waarde || "");

  useEffect(() => {
    if ((waarde || "") === eigen.current) return;
    eigen.current = waarde || "";
    const gelezen = uitSchema(waarde);
    setVelden(gelezen || []);
    setRuw(gelezen === null);
  }, [waarde]);

  function zet(nieuw: Veld[]) {
    setVelden(nieuw);
    const json = nieuw.length ? JSON.stringify(naarSchema(nieuw), null, 2) : "";
    eigen.current = json;
    onChange(json);
  }

  const naamloos = velden.some((v) => !v.naam.trim());

  if (ruw) {
    return (
      <div className="space-y-1">
        <TextArea rows={8} className="font-mono text-[11px]" value={waarde || ""}
                  onChange={(e) => onChange(e.target.value)} />
        <div className="flex items-center justify-between">
          <p className="text-[11px] text-muted-foreground">
            {uitSchema(waarde) === null
              ? "Dit schema gebruikt iets dat niet in velden te tekenen is — daarom de JSON zelf."
              : "JSON-weergave."}
          </p>
          {uitSchema(waarde) !== null && (
            <Button variant="ghost" className="text-[11px]"
                    onClick={() => { setVelden(uitSchema(waarde) || []); setRuw(false); }}>
              Terug naar velden
            </Button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {velden.map((v, i) => (
        <VeldRij key={i} veld={v} diepte={0}
                 onChange={(nw) => zet(velden.map((x, j) => (j === i ? nw : x)))}
                 onWeg={() => zet(velden.filter((_, j) => j !== i))} />
      ))}
      <div className="flex items-center justify-between">
        <Button variant="secondary" className="text-xs"
                onClick={() => zet([...velden,
                                    { naam: "", soort: "tekst", omschrijving: "", verplicht: false }])}>
          + Veld
        </Button>
        <Button variant="ghost" className="text-[11px]" onClick={() => setRuw(true)}>
          JSON tonen
        </Button>
      </div>
      {naamloos && (
        <p className="text-[11px] text-amber-600">
          Een veld zonder naam staat nog niet in het schema — geef het een naam.
        </p>
      )}
      <p className="text-[11px] text-muted-foreground">
        Deze velden komen terug als keuzelijst bij een voorwaarde en als lijst om met een lus
        langs te lopen — dus wat je hier neerzet, hoef je verderop niet meer over te typen.
      </p>
    </div>
  );
}
