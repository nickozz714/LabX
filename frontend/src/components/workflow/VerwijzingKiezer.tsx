/**
 * components/workflow/VerwijzingKiezer.tsx — een verwijzing kiezen in plaats
 * van typen.
 *
 * Aanleiding: in een echte workflow stond als voorwaarde
 * `stap.stap_2.json.rijen` terwijl het veld `incidentGroups` heet. Zo'n
 * typefout geeft geen foutmelding maar een voorwaarde die altijd onwaar is —
 * je merkt het pas als de verkeerde tak loopt. LabX kent de schema's van de
 * activiteiten, dus dit hoort een lijst te zijn.
 *
 * Zelf typen blijft kunnen: er zijn verwijzingen die LabX niet kan kennen
 * (een veld in vrije uitvoer zonder schema). De lijst helpt; hij sluit niets
 * uit.
 */
import type { Verwijzing } from "@/lib/types";
import { Input, Select } from "@/components/ui";

export function VerwijzingKiezer({ waarde, opties, onChange, placeholder }: {
  waarde: string;
  opties: Verwijzing[];
  onChange: (pad: string) => void;
  placeholder?: string;
}) {
  const bekend = opties.some((o) => o.pad === waarde);
  return (
    <div className="space-y-1">
      <Select value={bekend ? waarde : ""} onChange={(e) => e.target.value && onChange(e.target.value)}>
        <option value="">{opties.length ? "Kies een waarde…" : "Nog niets om uit te kiezen"}</option>
        {opties.map((o) => (
          <option key={o.pad} value={o.pad}>
            {o.pad}{o.soort ? ` — ${o.soort}` : ""}{o.omschrijving ? ` · ${o.omschrijving}` : ""}
          </option>
        ))}
      </Select>
      <Input value={waarde} placeholder={placeholder || "of typ zelf een verwijzing"}
             className="font-mono text-[11px]"
             onChange={(e) => onChange(e.target.value)} />
      {waarde && !bekend && opties.length > 0 && (
        <p className="text-[11px] text-yellow-600">
          Deze verwijzing staat niet in de lijst. Dat kan kloppen, maar een typefout levert
          hier geen foutmelding op — alleen een voorwaarde die altijd onwaar is.
        </p>
      )}
    </div>
  );
}
