/**
 * UrenPage — het scherm waar je je uren uit schrijft.
 *
 * De opzet volgt één overtuiging: een getal dat je niet kunt verdedigen is
 * erger dan geen getal. Daarom staat overal zichtbaar waar een uur vandaan
 * komt, en daarom is de overlap een eigen kolom in plaats van een correctie
 * die stilletjes is doorgevoerd.
 *
 * - **Eigen tijd** is wat je schrijft: schatting + wat de agent meldde + jouw
 *   correcties.
 * - **Agent** is machinetijd. Staat er als context bij ("wat speelde er"),
 *   telt nadrukkelijk niet mee in wat je schrijft.
 * - **Verdeeld** en **overlap** beantwoorden de vraag die je zelf stelde: liep
 *   dit tegelijk met iets anders, en hoeveel mag ik dus niet twee keer
 *   schrijven.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw, Clock, Layers, AlertTriangle } from "lucide-react";

import { Button, Badge, EmptyState, Label } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { urenApi, toonUren } from "@/lib/uren";
import type { ProjectRegel, UrenOverzicht } from "@/lib/uren";

/** Maand als YYYY-MM-DD-paar, want dat is de periode waarin je schrijft. */
function maandGrenzen(verschuiving = 0): { van: string; tot: string; label: string } {
  const nu = new Date();
  const begin = new Date(Date.UTC(nu.getUTCFullYear(), nu.getUTCMonth() + verschuiving, 1));
  const eind = new Date(Date.UTC(begin.getUTCFullYear(), begin.getUTCMonth() + 1, 0));
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  return {
    van: iso(begin),
    tot: iso(eind),
    label: begin.toLocaleDateString("nl-NL", { month: "long", year: "numeric", timeZone: "UTC" }),
  };
}

export function UrenPage() {
  const [maand, setMaand] = useState(0);
  const periode = useMemo(() => maandGrenzen(maand), [maand]);
  const [data, setData] = useState<UrenOverzicht | null>(null);
  const [busy, setBusy] = useState(false);
  const [melding, setMelding] = useState<string | null>(null);

  const laden = useCallback(async () => {
    const uit = await urenApi.overzicht({ van: periode.van, tot: periode.tot });
    setData(uit);
  }, [periode.van, periode.tot]);

  useEffect(() => {
    laden().catch((err) =>
      setMelding(err instanceof ApiError ? err.message : "Urenoverzicht laden mislukt"));
  }, [laden]);

  async function herbereken() {
    setBusy(true);
    setMelding(null);
    try {
      const uit = await urenApi.verzamel({});
      setMelding(
        `${uit.gemeten_nieuw} nieuwe gemeten regel(s), ${uit.schattingen} schatting(en) bijgewerkt.`);
      await laden();
    } catch (err) {
      setMelding(err instanceof ApiError ? err.message : "Herberekenen mislukt");
    } finally {
      setBusy(false);
    }
  }

  const t = data?.totaal;
  // Hoe scheef het beeld zou zijn als je bruto schreef. Dit is het hele punt
  // van dit scherm, dus het hoort bovenaan en niet in een voetnoot.
  const scheef = t && t.bruto > 0 ? (t.overlap / t.bruto) * 100 : 0;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-lg font-semibold">Uren</h1>
          <p className="text-xs text-muted-foreground">
            Wat je kunt schrijven, en wat daarvan tegelijk liep.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" className="text-xs" onClick={() => setMaand((m) => m - 1)}>
            ← vorige
          </Button>
          <span className="min-w-[9rem] text-center text-sm font-medium capitalize">
            {periode.label}
          </span>
          <Button variant="ghost" className="text-xs" disabled={maand >= 0}
                  onClick={() => setMaand((m) => Math.min(0, m + 1))}>
            volgende →
          </Button>
          <Button variant="secondary" className="text-xs" onClick={herbereken}
                  disabled={busy} busy={busy} busyLabel="Berekenen…">
            <RefreshCw size={13} className={busy ? "animate-spin" : ""} /> Opnieuw berekenen
          </Button>
        </div>
      </div>

      {melding && (
        <div className="rounded-md border border-border bg-muted/40 p-2 text-xs">{melding}</div>
      )}

      {t && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Kaart icoon={<Clock size={14} />} titel="Jouw tijd (schatting)"
                 waarde={toonUren(t.eigen_minuten)}
                 toelichting="Dit is het getal waar je uurregels uit komen." />
          <Kaart icoon={<Layers size={14} />} titel="Verdeeld over projecten"
                 waarde={toonUren(t.verdeeld)}
                 toelichting="De werkelijk verstreken klok, eerlijk verdeeld." />
          <Kaart icoon={<AlertTriangle size={14} />} titel="Overlap" nadruk={t.overlap > 0}
                 waarde={toonUren(t.overlap)}
                 toelichting={t.overlap > 0
                   ? `${scheef.toFixed(0)}% van bruto liep tegelijk met iets anders.`
                   : "Niets liep tegelijk."} />
          <Kaart icoon={<RefreshCw size={14} />} titel="Agent-tijd (context)"
                 waarde={toonUren(t.agent_minuten)}
                 toelichting="Machinetijd, inclusief wachten. Geen urenstaat." />
        </div>
      )}

      {t && t.overlap > 0 && (
        <div className="rounded-md border border-warning/40 bg-warning/10 p-3 text-xs">
          <strong>Let op bij het schrijven.</strong> De projecten tellen samen op tot{" "}
          {toonUren(t.bruto)} bruto, maar er is maar {toonUren(t.klok)} klok voorbijgegaan.
          Schrijf je de bruto-kolom over, dan schrijf je {toonUren(t.overlap)} die niet
          bestaan. De kolom <em>verdeeld</em> deelt gedeelde tijd gelijk over de projecten
          die op dat moment liepen — een redelijk uitgangspunt, geen waarheid. Weet je dat
          het ene project meer aandacht vroeg dan het andere, pas het dan met de hand aan.
        </div>
      )}

      {!data?.projecten.length ? (
        <EmptyState>
          Geen uren in deze maand. Druk op “Opnieuw berekenen” om de agent-runs en je eigen
          berichten alsnog uit te lezen.
        </EmptyState>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-xs text-muted-foreground">
              <tr>
                <th className="px-3 py-2 text-left font-medium">Klant / project</th>
                <th className="px-3 py-2 text-right font-medium">Jouw tijd</th>
                <th className="px-3 py-2 text-right font-medium">Bruto</th>
                <th className="px-3 py-2 text-right font-medium">Verdeeld</th>
                <th className="px-3 py-2 text-right font-medium">Overlap</th>
                <th className="px-3 py-2 text-right font-medium">Agent</th>
                <th className="px-3 py-2 text-left font-medium">Categorieën</th>
              </tr>
            </thead>
            <tbody>
              {data.projecten.map((p) => <Rij key={p.project} p={p} />)}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Kaart({ icoon, titel, waarde, toelichting, nadruk }: {
  icoon: React.ReactNode; titel: string; waarde: string;
  toelichting: string; nadruk?: boolean;
}) {
  return (
    <div className={`rounded-lg border p-3 ${nadruk
      ? "border-warning/50 bg-warning/5" : "border-border bg-card"}`}>
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
        {icoon} {titel}
      </div>
      <div className="mt-1 text-xl font-semibold">{waarde}</div>
      <div className="mt-0.5 text-[11px] leading-snug text-muted-foreground">{toelichting}</div>
    </div>
  );
}

function Rij({ p }: { p: ProjectRegel }) {
  return (
    <tr className="border-t border-border">
      <td className="px-3 py-2">
        <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{p.klant}</div>
        <div className="font-medium">
          {p.traject ?? <span className="text-muted-foreground">— nog geen project</span>}
        </div>
        <div className="text-[11px] text-muted-foreground">
          {p.tickets} ticket(s) · {p.regels} regel(s)
        </div>
      </td>
      <td className="px-3 py-2 text-right font-medium">{toonUren(p.eigen_minuten)}</td>
      <td className="px-3 py-2 text-right text-muted-foreground">{toonUren(p.bruto)}</td>
      <td className="px-3 py-2 text-right font-medium">{toonUren(p.verdeeld)}</td>
      <td className="px-3 py-2 text-right">
        {p.overlap > 0
          ? <Badge tone="yellow">{toonUren(p.overlap)}</Badge>
          : <span className="text-muted-foreground">—</span>}
      </td>
      <td className="px-3 py-2 text-right text-muted-foreground">{toonUren(p.agent_minuten)}</td>
      <td className="px-3 py-2">
        <div className="flex flex-wrap gap-1">
          {p.categorieen.length === 0
            ? <span className="text-[11px] text-muted-foreground">—</span>
            : p.categorieen.slice(0, 4).map((c) => (
                <Badge key={c.naam}>{c.naam} {toonUren(c.minuten)}</Badge>
              ))}
        </div>
      </td>
    </tr>
  );
}
