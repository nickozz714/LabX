/**
 * components/workflow/TekstMetVerwijzingen.tsx — een tekstveld waarin je de
 * uitvoer van eerdere stappen kunt prikken.
 *
 * Aanleiding: de opdracht van een activiteit was een kaal tekstvak. Wilde je
 * verderbouwen op wat een vorige stap opleverde, dan moest je
 * `{{ stap.analyse.json.incidentGroups }}` uit je hoofd typen — met precies de
 * fout tot gevolg die we bij de voorwaarde al hadden: een naam die niet
 * bestaat, die geen foutmelding geeft maar een lege waarde.
 *
 * Nu staat boven het veld een keuzelijst met alles wat er te verwijzen valt.
 * Kies je iets, dan wordt het op de plek van je cursor ingevoegd — niet aan het
 * eind, want je bent midden in een zin.
 *
 * In een lus staat `{{ item }}` bovenaan: dat is waar je in een ronde mee
 * verderwerkt, en dus wat je het vaakst nodig hebt.
 */
import { useRef } from "react";
import type { Verwijzing } from "@/lib/types";
import { Label, Select, TextArea } from "@/components/ui";
import { GeheimKiezer } from "@/components/GeheimInvoegen";

export function TekstMetVerwijzingen({
  label, waarde, onChange, opties, rijen = 6, placeholder, hint, mono,
}: {
  label: string;
  waarde: string;
  onChange: (tekst: string) => void;
  opties: Verwijzing[];
  rijen?: number;
  placeholder?: string;
  hint?: string;
  mono?: boolean;
}) {
  const veld = useRef<HTMLTextAreaElement | null>(null);

  function voegIn(pad: string) {
    if (!pad) return;
    prik(`{{ ${pad} }}`);
  }

  /** Een stukje tekst op de cursorpositie zetten. Gedeeld met de kluis-kiezer:
   *  een geheim voeg je op precies dezelfde manier in als een verwijzing. */
  function prik(fragment: string) {
    const el = veld.current;
    // Zonder cursorpositie (het veld had geen focus) plakken we achteraan —
    // beter dan de tekst stilletjes ergens anders neerzetten.
    const start = el?.selectionStart ?? waarde.length;
    const eind = el?.selectionEnd ?? waarde.length;
    const nieuw = waarde.slice(0, start) + fragment + waarde.slice(eind);
    onChange(nieuw);
    // Cursor achter wat je net invoegde, zodat je gewoon doortypt.
    requestAnimationFrame(() => {
      if (!el) return;
      el.focus();
      const pos = start + fragment.length;
      el.setSelectionRange(pos, pos);
    });
  }

  const inLus = opties.some((o) => o.pad === "item");

  return (
    <div>
      <div className="mb-1 flex items-end justify-between gap-2">
        <Label>{label}</Label>
        <GeheimKiezer onKies={prik} />
        <Select
          value=""
          className="h-7 w-52 py-0 text-[11px]"
          onChange={(e) => { voegIn(e.target.value); e.target.value = ""; }}
        >
          <option value="">
            {opties.length ? "+ Verwijzing invoegen…" : "nog niets om te verwijzen"}
          </option>
          {inLus && (
            <optgroup label="In deze lus">
              {opties.filter((o) => o.pad === "item" || o.pad === "iteratie"
                                    || o.pad.startsWith("item.")).map((o) => (
                <option key={o.pad} value={o.pad}>
                  {o.pad}{o.omschrijving ? ` — ${o.omschrijving}` : ""}
                </option>
              ))}
            </optgroup>
          )}
          <optgroup label="Uitvoer van eerdere activiteiten">
            {opties.filter((o) => !(o.pad === "item" || o.pad === "iteratie"
                                    || o.pad.startsWith("item."))).map((o) => (
              <option key={o.pad} value={o.pad}>
                {o.pad}{o.soort ? ` — ${o.soort}` : ""}
              </option>
            ))}
          </optgroup>
        </Select>
      </div>
      <TextArea
        ref={veld}
        rows={rijen}
        value={waarde}
        placeholder={placeholder}
        className={mono ? "font-mono text-xs" : undefined}
        onChange={(e) => onChange(e.target.value)}
      />
      {hint && <p className="mt-1 text-[11px] text-muted-foreground">{hint}</p>}
    </div>
  );
}
