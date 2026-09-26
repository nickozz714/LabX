/**
 * pages/AuditPage.tsx — wat er in een lab gebeurd is.
 *
 * Het bestaande audit-scherm zit bij de data-guard en gaat over maskeren en
 * blokkeren. Dat is een scherp mes voor één vraag, en het antwoord op de
 * andere stond nergens: wat heeft dit lab eigenlijk gedaan? Die vraag stel je
 * per klant, hij gaat over beurten en niet over bytes, en je wilt hem zowel
 * over vandaag als over het kwartaal kunnen stellen.
 *
 * Daarom drie dingen boven elkaar, van grof naar fijn:
 * 1. De staafjes — hoeveel beurten en acties per dag, week of maand, met wat
 *    er misging in dezelfde staaf. Hier zie je het tempo.
 * 2. De acties — welke tools er gebruikt zijn, geteld. Hier zie je het soort
 *    werk.
 * 3. De beurten zelf — model, invoer, uitvoer, acties, duur en kosten. Hier
 *    zie je wat er precies gebeurde.
 */
import { useCallback, useEffect, useState } from "react";
import { Activity, AlertTriangle, ChevronDown, ChevronRight } from "lucide-react";
import { auditApi, type AuditAggregatie, type AuditGebeurtenis } from "@/lib/audit";
import { labsApi } from "@/lib/labs";
import type { Lab } from "@/lib/types";
import { Badge, Button, Card, EmptyState, Label, Select } from "@/components/ui";
import { Staafjes } from "@/components/Staafjes";

const PERIODES: { key: "dag" | "week" | "maand"; label: string; aantal: number }[] = [
  { key: "dag", label: "Per dag", aantal: 14 },
  { key: "week", label: "Per week", aantal: 12 },
  { key: "maand", label: "Per maand", aantal: 12 },
];

const BRON_TOON: Record<string, "neutral" | "violet" | "green"> = {
  chat: "neutral", taak: "violet", workflow: "green",
};

const STATUS_TOON: Record<string, "green" | "red" | "yellow" | "neutral"> = {
  completed: "green", ok: "green", failed: "red", error: "red", fout: "red",
  running: "yellow", queued: "yellow", cancelled: "neutral", overgeslagen: "neutral",
};

function duur(ms: number | null): string {
  if (!ms) return "—";
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`;
}

export function AuditPage() {
  const [labs, setLabs] = useState<Lab[]>([]);
  const [labId, setLabId] = useState("");
  const [bron, setBron] = useState("");
  const [periode, setPeriode] = useState<"dag" | "week" | "maand">("dag");
  const [agg, setAgg] = useState<AuditAggregatie | null>(null);
  const [items, setItems] = useState<AuditGebeurtenis[]>([]);
  const [totaal, setTotaal] = useState(0);
  const [open, setOpen] = useState<string | null>(null);
  const [bezig, setBezig] = useState(true);

  useEffect(() => { labsApi.list().then(setLabs).catch(() => {}); }, []);

  const laad = useCallback(() => {
    setBezig(true);
    const p = PERIODES.find((x) => x.key === periode)!;
    Promise.all([
      auditApi.aggregatie({ labId: labId || undefined, periode, aantal: p.aantal }),
      auditApi.activiteit({ labId: labId || undefined, bron: bron || undefined, limit: 50 }),
    ]).then(([a, l]) => {
      setAgg(a);
      setItems(l.items);
      setTotaal(l.totaal);
    }).catch(() => {
      setAgg(null);
      setItems([]);
    }).finally(() => setBezig(false));
  }, [labId, bron, periode]);

  useEffect(() => { laad(); }, [laad]);

  return (
    <div className="space-y-4 p-4">
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-lg font-semibold">Audit</h1>
          <p className="text-xs text-muted-foreground">
            Wat er in een lab gebeurd is: welk model, wat erin ging, wat eruit kwam en welke
            acties er ondernomen zijn.
          </p>
        </div>
        <span className="flex-1" />
        <div>
          <Label>Lab</Label>
          <Select value={labId} onChange={(e) => setLabId(e.target.value)} className="w-56">
            <option value="">Alle labs</option>
            {labs.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
          </Select>
        </div>
        <div>
          <Label>Soort</Label>
          <Select value={bron} onChange={(e) => setBron(e.target.value)} className="w-40">
            <option value="">Alles</option>
            <option value="agent">Chat en taken</option>
            <option value="workflow">Workflows</option>
          </Select>
        </div>
        <Button variant="secondary" onClick={laad} disabled={bezig}>
          {bezig ? "Bezig…" : "Verversen"}
        </Button>
      </div>

      <Card className="space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-3">
          <Activity size={15} className="text-muted-foreground" />
          <div className="flex gap-1">
            {PERIODES.map((p) => (
              <Button key={p.key}
                      variant={periode === p.key ? "primary" : "ghost"}
                      className="text-xs"
                      onClick={() => setPeriode(p.key)}>{p.label}</Button>
            ))}
          </div>
          <span className="flex-1" />
          {agg && (
            <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
              <span><strong className="text-foreground">{agg.totaal_beurten}</strong> beurten</span>
              <span><strong className="text-foreground">{agg.totaal_acties}</strong> acties</span>
              <span><strong className="text-foreground">${agg.totaal_kosten.toFixed(2)}</strong></span>
            </div>
          )}
        </div>

        {agg && agg.emmers.length > 0 ? (
          <div className="grid gap-6 md:grid-cols-2">
            <div>
              <Label>Beurten</Label>
              <Staafjes eenheid="beurten"
                        staven={agg.emmers.map((e) => ({
                          label: e.label, waarde: e.beurten, fouten: e.fouten,
                          detail: e.cost_usd ? `$${e.cost_usd.toFixed(2)}` : undefined }))} />
              <p className="mt-1 text-[11px] text-muted-foreground">
                Het rode deel van een staaf is wat misging.
              </p>
            </div>
            <div>
              <Label>Acties (tool-aanroepen)</Label>
              <Staafjes eenheid="acties"
                        staven={agg.emmers.map((e) => ({ label: e.label, waarde: e.acties }))} />
              <p className="mt-1 text-[11px] text-muted-foreground">
                Eén actie is één tool-aanroep: een shell-commando, een MCP-tool, een zoekopdracht.
              </p>
            </div>
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">Nog niets te tonen voor deze selectie.</p>
        )}

        {agg && agg.top_acties.length > 0 && (
          <div>
            <Label>Meest gebruikte acties</Label>
            <div className="mt-1 flex flex-wrap gap-1">
              {agg.top_acties.map((a) => (
                <span key={a.naam}
                      className="rounded-md border border-border px-2 py-0.5 font-mono text-[11px]">
                  {a.naam}
                  <span className="ml-1 text-muted-foreground">{a.aantal}×</span>
                </span>
              ))}
            </div>
          </div>
        )}
      </Card>

      <div>
        <div className="mb-2 flex items-center gap-2">
          <Label>Beurten</Label>
          <span className="text-xs text-muted-foreground">
            {items.length} van {totaal} getoond
          </span>
        </div>
        {items.length === 0 ? (
          <EmptyState>Geen beurten gevonden voor deze selectie.</EmptyState>
        ) : (
          <div className="divide-y divide-border rounded-md border border-border">
            {items.map((g) => (
              <div key={g.id} className="text-sm">
                <button className="flex w-full flex-wrap items-center gap-2 p-2 text-left"
                        onClick={() => setOpen(open === g.id ? null : g.id)}>
                  {open === g.id ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  <span className="text-[11px] text-muted-foreground">
                    {new Date(g.ts).toLocaleString(undefined, {
                      day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}
                  </span>
                  <Badge tone={BRON_TOON[g.bron] || "neutral"}>{g.bron}</Badge>
                  <Badge tone={STATUS_TOON[g.status] || "neutral"}>{g.status}</Badge>
                  <span className="font-medium">{g.lab_naam}</span>
                  <span className="truncate text-muted-foreground">{g.titel}</span>
                  <span className="flex-1" />
                  {g.acties_totaal > 0 && (
                    <span className="text-[11px] text-muted-foreground">
                      {g.acties_totaal} actie(s)
                    </span>
                  )}
                  <span className="font-mono text-[11px] text-muted-foreground">{g.model}</span>
                  <span className="text-[11px] text-muted-foreground">{duur(g.duur_ms)}</span>
                  {g.cost_usd > 0 && (
                    <span className="text-[11px] text-muted-foreground">
                      ${g.cost_usd.toFixed(3)}
                    </span>
                  )}
                </button>

                {open === g.id && (
                  <div className="space-y-3 border-t border-border bg-secondary/20 p-3">
                    <div className="flex flex-wrap gap-4 text-[11px] text-muted-foreground">
                      <span>model <span className="font-mono text-foreground">{g.model}</span></span>
                      {g.werker != null && <span>werker {g.werker}</span>}
                      {g.iteratie != null && <span>ronde {g.iteratie}</span>}
                      <span>{g.input_tokens} in / {g.output_tokens} uit tokens</span>
                    </div>
                    <Blok titel="Wat erin ging" tekst={g.invoer} />
                    <Blok titel="Wat eruit kwam" tekst={g.uitvoer} />
                    {g.acties.length > 0 ? (
                      <div>
                        <div className="mb-1 text-[11px] font-semibold text-muted-foreground">
                          Ondernomen acties
                        </div>
                        <div className="flex flex-wrap gap-1">
                          {g.acties.map((a) => (
                            <span key={a.naam}
                                  className="rounded bg-background px-2 py-0.5 font-mono text-[11px]">
                              {a.naam}<span className="ml-1 text-muted-foreground">{a.aantal}×</span>
                            </span>
                          ))}
                        </div>
                      </div>
                    ) : (
                      <p className="flex items-center gap-1 text-[11px] text-muted-foreground">
                        <AlertTriangle size={11} /> Geen acties — deze beurt heeft alleen
                        geantwoord.
                      </p>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function Blok({ titel, tekst }: { titel: string; tekst: string | null }) {
  if (!tekst) return null;
  return (
    <div>
      <div className="mb-1 text-[11px] font-semibold text-muted-foreground">{titel}</div>
      <pre className="max-h-60 overflow-auto whitespace-pre-wrap rounded bg-background p-2
                      text-[11px] leading-relaxed">{tekst}</pre>
    </div>
  );
}
