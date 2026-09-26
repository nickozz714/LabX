/**
 * components/GeheimInvoegen.tsx — een geheim uit de kluis in een tekstveld
 * prikken.
 *
 * Aanleiding: in de workflow-editor kun je een verwijzing kiezen uit een
 * lijstje, en dat is precies waarom die verwijzingen ook gebruikt worden. Bij
 * een skill, een tool-instructie of een prompt moest je `{{secret:naam}}` uit
 * je hoofd typen — inclusief de naam, die je dan eerst in een ander tabblad
 * ging opzoeken. Daarmee is de kluis theoretisch overal bruikbaar en in de
 * praktijk nergens.
 *
 * Dit is bewust één klein ding: een keuzelijst die de verwijzing op de plek van
 * je cursor invoegt. De waarde komt er nooit in — die komt pas op weg naar
 * buiten, en dat is de hele afspraak (zie services/secrets/vault.py).
 */
import { useEffect, useRef, useState } from "react";
import { secretApi, type SecretDto } from "@/lib/secrets";
import { Label, Select, TextArea } from "@/components/ui";

/** De kluis is klein en verandert zelden; hem per veld opnieuw ophalen zou bij
 *  een scherm met tien velden tien verzoeken zijn. Eén gedeelde belofte. */
let _cache: Promise<SecretDto[]> | null = null;

export function useGeheimen(): SecretDto[] {
  const [rijen, setRijen] = useState<SecretDto[]>([]);
  useEffect(() => {
    if (!_cache) _cache = secretApi.list().catch(() => [] as SecretDto[]);
    let weg = false;
    _cache.then((r) => { if (!weg) setRijen(r); });
    return () => { weg = true; };
  }, []);
  return rijen;
}

/** Na het toevoegen of weghalen van een geheim klopt de gedeelde lijst niet
 *  meer. De kluispagina zegt het hier, zodat een openstaand scherm de nieuwe
 *  naam meteen kan kiezen. */
export function vergeetGeheimen(): void {
  _cache = null;
}

/** De keuzelijst zelf, voor als je hem naast een bestaand veld wilt hangen. */
export function GeheimKiezer({ onKies, className = "" }: {
  onKies: (verwijzing: string) => void;
  className?: string;
}) {
  const geheimen = useGeheimen();
  return (
    <Select
      value=""
      className={`h-7 w-56 py-0 text-[11px] ${className}`}
      title="Voegt de verwijzing in, niet de waarde"
      onChange={(e) => { if (e.target.value) onKies(e.target.value); e.target.value = ""; }}
    >
      <option value="">
        {geheimen.length ? "🔑 Geheim invoegen…" : "🔑 kluis is leeg"}
      </option>
      {geheimen.map((g) => (
        <option key={g.name} value={g.placeholder}>
          {g.name}{g.description ? ` — ${g.description}` : ""}
        </option>
      ))}
    </Select>
  );
}

/** Een tekstvak met de kiezer erboven. Dit is wat je op de meeste plekken
 *  wilt: een skill-instructie, een prompt, een tool-instructie. */
export function TekstMetGeheimen({
  label, waarde, onChange, rijen = 3, placeholder, hint, mono,
}: {
  label?: string;
  waarde: string;
  onChange: (tekst: string) => void;
  rijen?: number;
  placeholder?: string;
  hint?: string;
  mono?: boolean;
}) {
  const veld = useRef<HTMLTextAreaElement | null>(null);

  function voegIn(verwijzing: string) {
    const el = veld.current;
    // Geen cursor (het veld had geen focus): achteraan plakken is beter dan
    // de tekst stilletjes ergens anders neerzetten.
    const start = el?.selectionStart ?? waarde.length;
    const eind = el?.selectionEnd ?? waarde.length;
    onChange(waarde.slice(0, start) + verwijzing + waarde.slice(eind));
    requestAnimationFrame(() => {
      if (!el) return;
      el.focus();
      const pos = start + verwijzing.length;
      el.setSelectionRange(pos, pos);
    });
  }

  return (
    <div>
      <div className="mb-1 flex items-end justify-between gap-2">
        {label ? <Label>{label}</Label> : <span />}
        <GeheimKiezer onKies={voegIn} />
      </div>
      <TextArea ref={veld} rows={rijen} value={waarde} placeholder={placeholder}
                className={mono ? "font-mono text-xs" : undefined}
                onChange={(e) => onChange(e.target.value)} />
      {hint && <p className="mt-1 text-[11px] text-muted-foreground">{hint}</p>}
    </div>
  );
}
