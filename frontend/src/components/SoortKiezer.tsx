/**
 * components/SoortKiezer.tsx — het soort werkitem kiezen dat de bron kent.
 *
 * Waarom dit uit de bron komt en niet uit een vast lijstje: alleen Jira of
 * Azure DevOps weet welke soorten een project heeft, en hoe ze daar heten. Bij
 * Swinkels stond het bord op "Task" — een type dat in dat project helemaal niet
 * bestaat als gewone taak, maar wel als SUBTAAK ("Taak"). Elke push mislukte
 * daardoor met een melding die niet zei dat de instelling fout stond.
 *
 * Subtaken staan er wel in, maar uitgeschakeld: een subtaak kan niet zonder
 * bovenliggend issue. Ze weglaten zou de vraag oproepen waar ze gebleven zijn.
 */
import { useEffect, useState } from "react";
import { boardApi, type ItemSoort } from "@/lib/boards";
import { Label, Select } from "@/components/ui";
import { ApiError } from "@/lib/api";

/** De soorten van één bord, één keer opgehaald per bord. */
export function useItemSoorten(boardId: number | null) {
  const [soorten, setSoorten] = useState<ItemSoort[]>([]);
  const [melding, setMelding] = useState<string | null>(null);

  useEffect(() => {
    if (!boardId) return;
    let weg = false;
    boardApi.itemTypes(boardId)
      .then((r) => {
        if (weg) return;
        setSoorten(r.soorten || []);
        setMelding(r.melding || null);
      })
      // Stil falen betekende hier: geen lijst, dus geen keuzelijst, dus een
      // scherm waarop het vak gewoon niet bestaat. Zo bleef een kapot endpoint
      // dagen onopgemerkt — de fout hoort in beeld te staan.
      .catch((e) => {
        if (weg) return;
        setSoorten([]);
        setMelding(e instanceof ApiError
          ? `De soorten konden niet opgehaald worden: ${e.message}`
          : "De soorten konden niet opgehaald worden.");
      });
    return () => { weg = true; };
  }, [boardId]);

  return { soorten, melding };
}

export function SoortKiezer({ boardId, waarde, onChange, label = "Soort", hint }: {
  boardId: number | null;
  waarde: string;
  onChange: (soort: string) => void;
  label?: string;
  hint?: string;
}) {
  const { soorten, melding } = useItemSoorten(boardId);
  // Ook zonder lijst blijven staan: een veld dat er niet is, is niet te
  // onderscheiden van een veld dat er nooit was — en dan ga je zoeken naar
  // iets wat er hoort te zijn.
  const gekozen = soorten.find((s) => s.naam === waarde);

  return (
    <div>
      <Label>{label}</Label>
      <Select value={waarde} onChange={(e) => onChange(e.target.value)}
              disabled={!soorten.length}>
        <option value="">
          {soorten.length ? "— wat het bord gebruikt —" : "— geen soorten opgehaald —"}
        </option>
        {soorten.map((s) => (
          <option key={s.id} value={s.naam} disabled={s.subtaak}>
            {s.naam}{s.subtaak ? " (subtaak — kan niet zonder bovenliggend issue)" : ""}
          </option>
        ))}
      </Select>
      {gekozen?.verplicht?.length ? (
        <p className="mt-1 text-[11px] text-amber-600">
          De bron eist bij een <strong>{gekozen.naam}</strong> ook:{" "}
          {gekozen.verplicht.map((v) => v.naam || v.veld).join(", ")}. Zet er een vaste
          waarde voor bij de instellingen van dit bord, anders mislukt het doorzetten.
        </p>
      ) : null}
      {melding && <p className="mt-1 text-[11px] text-muted-foreground">{melding}</p>}
      {hint && !melding && <p className="mt-1 text-[11px] text-muted-foreground">{hint}</p>}
    </div>
  );
}
