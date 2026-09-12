/**
 * pages/GuardPage.tsx — de data-guard: instellen én controleren.
 *
 * Drie lagen, en het scherm laat ze als drie lagen zien omdat ze verschillende
 * dingen kunnen:
 *
 * 1. REGELS OP DE OPDRACHT houden een commando tegen vóór uitvoeren. Dit is de
 *    enige plek waar `SELECT MAX(bedrag)` te stoppen is: dat levert één getal
 *    op dat in geen enkele uitvoercontrole opvalt en tóch een echte magnitude
 *    is.
 * 2. REGELS OP DE UITVOER maskeren wat terugkomt. Standaard maskeren en niet
 *    blokkeren: de hele uitvoer weggooien omdat er één BSN in staat, kost je
 *    ook de exitcode, het pad en de foutmelding die je nodig had.
 * 3. HET LOKALE MODEL vangt wat patronen principieel niet zien: een afgeleid
 *    aggregaat ("PostNL 1142, DHL 738, gem. 19,4 kg") bevat geen enkel patroon
 *    en is wél klantdata. Daarom staat de status ervan bovenaan en niet
 *    weggestopt — deze laag was maandenlang stil kapot zonder dat iemand het
 *    kon zien.
 *
 * En het audit-spoor, waar je het origineel naast wat het model kreeg legt.
 * Dat is de enige manier om de vraag te beantwoorden of de guard het goed doet.
 */
import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Check, Eye, Plus, ShieldCheck, Trash2 } from "lucide-react";
import { ApiError } from "@/lib/api";
import {
  guardApi, type GuardAuditDetail, type GuardAuditRegel, type GuardDetector,
  type GuardDoel, type GuardRegel, type GuardStatus,
} from "@/lib/guard";
import { Badge, Button, Card, Input, Label, Modal, Select, TextArea, Toggle } from "@/components/ui";

const ACTIE_TOON: Record<string, "green" | "red" | "yellow" | "neutral" | "violet"> = {
  blokkeren: "red", maskeren: "violet", waarschuwen: "yellow", toelaten: "green",
};
const UITKOMST_TOON: Record<string, "green" | "red" | "yellow" | "neutral" | "violet"> = {
  doorgelaten: "green", gemaskeerd: "violet", geblokkeerd: "red", geweigerd: "red",
};

function tijd(w: string): string {
  return new Date(w).toLocaleString(undefined, {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function GuardPage() {
  const [tab, setTab] = useState<"regels" | "audit">("regels");
  const [status, setStatus] = useState<GuardStatus | null>(null);

  const laadStatus = useCallback(() => {
    guardApi.status().then(setStatus).catch(() => undefined);
  }, []);
  useEffect(() => { laadStatus(); }, [laadStatus]);

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-6">
      <h1 className="flex items-center gap-2 text-xl font-bold">
        <ShieldCheck size={18} /> Data-guard
      </h1>

      <StatusBalk status={status} />

      <div className="flex gap-1 border-b border-border text-sm">
        {(["regels", "audit"] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)}
                  className={`px-3 py-2 ${tab === t ? "border-b-2 border-primary font-medium" : "text-muted-foreground"}`}>
            {t === "regels" ? "Regels" : "Wat er gebeurd is"}
          </button>
        ))}
      </div>

      {tab === "regels" ? <Regels onGewijzigd={laadStatus} /> : <AuditLijst />}
    </div>
  );
}

/** De drie lagen in één oogopslag — vooral of de derde er echt is. */
function StatusBalk({ status }: { status: GuardStatus | null }) {
  if (!status) return null;
  const m = status.lokaal_model;
  const modelOk = m.state === "ready";
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
      <Card className="p-3">
        <div className="text-xs font-semibold">1. Op de opdracht</div>
        <p className="mt-1 text-xs text-muted-foreground">
          {status.regels.opdracht} regel(s) actief. Houdt een commando tegen vóór uitvoeren —
          de enige plek waar een aggregatie als <code>MAX(bedrag)</code> te stoppen is.
        </p>
      </Card>
      <Card className="p-3">
        <div className="text-xs font-semibold">2. Op de uitvoer</div>
        <p className="mt-1 text-xs text-muted-foreground">
          {status.regels.uitvoer} regel(s) actief. Maskeert wat terugkomt, zodat de exitcode
          en de foutmelding bruikbaar blijven.
          {status.presidio.beschikbaar
            ? " Presidio doet namen en plaatsen mee."
            : " Presidio staat uit."}
        </p>
        {!status.presidio.beschikbaar && status.presidio.reden && (
          <p className="mt-1 text-[11px] text-yellow-600">{status.presidio.reden}</p>
        )}
      </Card>
      <Card className="p-3">
        <div className="flex items-center gap-2 text-xs font-semibold">
          3. Lokaal model
          <Badge tone={modelOk ? "green" : "red"}>{modelOk ? "actief" : m.state}</Badge>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          Vangt wat patronen niet zien: een afgeleid aggregaat bevat geen enkel patroon en is
          wél klantdata. {m.model} op {m.url}.
        </p>
        {!modelOk && m.hint && (
          <p className="mt-1 text-[11px] text-destructive">{m.hint}</p>
        )}
      </Card>
    </div>
  );
}

function Regels({ onGewijzigd }: { onGewijzigd: () => void }) {
  const [rijen, setRijen] = useState<GuardRegel[]>([]);
  const [detectors, setDetectors] = useState<GuardDetector[]>([]);
  const [nieuw, setNieuw] = useState<GuardDoel | null>(null);
  const [fout, setFout] = useState<string | null>(null);

  const laad = useCallback(async () => {
    setRijen(await guardApi.rules());
    onGewijzigd();
  }, [onGewijzigd]);

  useEffect(() => {
    guardApi.detectors().then(setDetectors).catch(() => undefined);
    laad().catch(() => undefined);
  }, [laad]);

  async function patch(r: GuardRegel, payload: Record<string, unknown>) {
    setFout(null);
    try {
      await guardApi.update(r.id, payload);
      await laad();
    } catch (e) {
      setFout(e instanceof ApiError ? e.message : "Opslaan mislukt");
    }
  }

  function blok(doel: GuardDoel, titel: string, uitleg: string) {
    const eigen = rijen.filter((r) => r.target === doel);
    return (
      <Card className="space-y-2 p-4">
        <h2 className="text-sm font-semibold">{titel}</h2>
        <p className="text-xs text-muted-foreground">{uitleg}</p>
        <div className="divide-y divide-border rounded-md border border-border">
          {eigen.map((r) => (
            <RegelRegel key={r.id} regel={r} onPatch={patch} onWeg={laad} />
          ))}
          {eigen.length === 0 && (
            <p className="p-3 text-xs text-muted-foreground">Geen regels.</p>
          )}
        </div>
        <Button variant="secondary" className="text-xs" onClick={() => setNieuw(doel)}>
          <Plus size={12} /> Regel toevoegen
        </Button>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      {fout && <p className="text-sm text-destructive">{fout}</p>}
      {blok("opdracht", "Regels op de opdracht",
            "Bekeken vóór uitvoeren. Hier hoort blokkeren thuis: een aggregatie levert één " +
            "getal op dat in geen enkele uitvoercontrole opvalt en tóch een echte magnitude is. " +
            "Een regel met actie 'toelaten' wint van de rest — zo maak je een uitzondering " +
            "zonder de onderliggende regel te wissen.")}
      {blok("uitvoer", "Regels op de uitvoer",
            "Bekeken vóór het naar het model gaat. Standaard maskeren: de hele uitvoer " +
            "weggooien omdat er één BSN in staat, kost je ook de exitcode en de foutmelding " +
            "die je nodig had. Zet een regel op blokkeren als je dat tóch wilt.")}
      {nieuw && (
        <RegelVenster doel={nieuw} detectors={detectors}
                      onClose={() => setNieuw(null)}
                      onKlaar={() => { setNieuw(null); laad(); }} />
      )}
    </div>
  );
}

function RegelRegel({ regel, onPatch, onWeg }: {
  regel: GuardRegel;
  onPatch: (r: GuardRegel, p: Record<string, unknown>) => void;
  onWeg: () => void;
}) {
  const acties: GuardRegel["action"][] = regel.target === "opdracht"
    ? ["blokkeren", "waarschuwen", "toelaten"]
    : ["maskeren", "blokkeren", "waarschuwen", "toelaten"];
  return (
    <div className="flex flex-wrap items-center gap-2 p-2 text-sm">
      <Toggle checked={regel.enabled} onChange={(v) => onPatch(regel, { enabled: v })} />
      <span className="font-medium">{regel.name}</span>
      <Badge tone="neutral">{regel.category}</Badge>
      {regel.kind === "ingebouwd" && <Badge tone="green">met checksum</Badge>}
      <Select className="w-36 text-xs" value={regel.action}
              onChange={(e) => onPatch(regel, { action: e.target.value })}>
        {acties.map((a) => <option key={a} value={a}>{a}</option>)}
      </Select>
      {!regel.builtin && (
        <Button variant="ghost" className="ml-auto text-xs text-destructive"
                onClick={() => {
                  if (confirm(`Regel '${regel.name}' verwijderen?`))
                    guardApi.remove(regel.id).then(onWeg);
                }}>
          <Trash2 size={12} />
        </Button>
      )}
      {regel.description && (
        <p className="w-full text-[11px] text-muted-foreground">{regel.description}</p>
      )}
    </div>
  );
}

/** Nieuwe regel, met een testveld. Dat veld is het hele punt: een reguliere
 *  expressie die alles of niets pakt, valt anders pas op als de guard al een
 *  week het verkeerde doet. */
function RegelVenster({ doel, detectors, onClose, onKlaar }: {
  doel: GuardDoel; detectors: GuardDetector[];
  onClose: () => void; onKlaar: () => void;
}) {
  const [naam, setNaam] = useState("");
  const [soort, setSoort] = useState<"ingebouwd" | "regex">(
    doel === "uitvoer" ? "ingebouwd" : "regex");
  const [detector, setDetector] = useState(detectors[0]?.key || "bsn");
  const [patroon, setPatroon] = useState("");
  const [categorie, setCategorie] = useState(
    doel === "uitvoer" ? "persoonsgegeven" : "klantgegevens");
  const [actie, setActie] = useState<GuardRegel["action"]>(
    doel === "uitvoer" ? "maskeren" : "blokkeren");
  const [voorbeeld, setVoorbeeld] = useState("");
  const [uitslag, setUitslag] = useState<any>(null);
  const [fout, setFout] = useState<string | null>(null);
  const [bezig, setBezig] = useState(false);

  async function test() {
    setUitslag(await guardApi.test({ kind: soort, pattern: patroon, detector, sample: voorbeeld }));
  }

  async function bewaar() {
    setBezig(true);
    setFout(null);
    try {
      await guardApi.create({
        target: doel, name: naam, kind: soort,
        pattern: soort === "regex" ? patroon : null,
        detector: soort === "ingebouwd" ? detector : null,
        category: categorie, action: actie,
      });
      onKlaar();
    } catch (e) {
      setFout(e instanceof ApiError ? e.message : "Opslaan mislukt");
    } finally {
      setBezig(false);
    }
  }

  return (
    <Modal open onClose={onClose} title={`Regel op de ${doel}`} wide>
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-2">
          <div>
            <Label>Naam</Label>
            <Input value={naam} onChange={(e) => setNaam(e.target.value)}
                   placeholder="Klantnummer" />
          </div>
          <div>
            <Label>Wat gebeurt er bij een treffer</Label>
            <Select value={actie} onChange={(e) => setActie(e.target.value as any)}>
              {(doel === "opdracht"
                ? ["blokkeren", "waarschuwen", "toelaten"]
                : ["maskeren", "blokkeren", "waarschuwen", "toelaten"]).map((a) => (
                <option key={a} value={a}>{a}</option>
              ))}
            </Select>
          </div>
        </div>

        <div>
          <Label>Hoe herken je het</Label>
          <Select value={soort} onChange={(e) => setSoort(e.target.value as any)}>
            <option value="ingebouwd">Ingebouwde detector (met checksum — aanbevolen)</option>
            <option value="regex">Eigen patroon</option>
          </Select>
        </div>

        {soort === "ingebouwd" ? (
          <div>
            <Select value={detector} onChange={(e) => setDetector(e.target.value)}>
              {detectors.map((d) => <option key={d.key} value={d.key}>{d.label}</option>)}
            </Select>
            <p className="mt-1 text-[11px] text-muted-foreground">
              {detectors.find((d) => d.key === detector)?.uitleg}
            </p>
          </div>
        ) : (
          <div>
            <Input className="font-mono text-xs" value={patroon}
                   onChange={(e) => setPatroon(e.target.value)}
                   placeholder="\\bKLANT-\\d{6}\\b" />
            <p className="mt-1 text-[11px] text-muted-foreground">
              Een reguliere expressie. Test hem hieronder voordat je hem aanzet — een patroon
              dat alles of niets pakt, merk je anders pas als de guard al een week het
              verkeerde doet.
            </p>
          </div>
        )}

        <div>
          <Label>Categorie (komt terug in de melding en de audit)</Label>
          <Input value={categorie} onChange={(e) => setCategorie(e.target.value)} />
        </div>

        <div className="rounded border border-border p-2">
          <Label>Uitproberen</Label>
          <TextArea rows={3} className="font-mono text-xs" value={voorbeeld}
                    onChange={(e) => setVoorbeeld(e.target.value)}
                    placeholder="Plak hier een stuk echte uitvoer of een commando." />
          <Button variant="secondary" className="mt-1 text-xs" onClick={test}>Testen</Button>
          {uitslag && (
            <div className="mt-2 space-y-1 text-xs">
              {!uitslag.ok ? (
                <p className="text-destructive">{uitslag.fout}</p>
              ) : (
                <>
                  <p>{uitslag.aantal} treffer(s){uitslag.treffers?.length
                    ? `: ${uitslag.treffers.join(", ")}` : ""}</p>
                  {uitslag.waarschuwing && (
                    <p className="flex items-start gap-1 text-yellow-600">
                      <AlertTriangle size={12} className="mt-0.5 shrink-0" />
                      {uitslag.waarschuwing}
                    </p>
                  )}
                  {doel === "uitvoer" && uitslag.aantal > 0 && (
                    <pre className="overflow-auto rounded bg-secondary p-2 whitespace-pre-wrap">
                      {uitslag.voorbeeld_gemaskeerd}
                    </pre>
                  )}
                </>
              )}
            </div>
          )}
        </div>

        {fout && <p className="text-sm text-destructive">{fout}</p>}
        <Button className="w-full" disabled={bezig || !naam.trim()} onClick={bewaar}>
          <Check size={13} /> Regel opslaan
        </Button>
      </div>
    </Modal>
  );
}

/** Het spoor. Hier beantwoord je de vraag of de guard het goed doet — en dat
 *  kon met het oude spoor niet, want dat bewaarde alleen een reden en een
 *  aantal bytes. */
function AuditLijst() {
  const [rijen, setRijen] = useState<GuardAuditRegel[]>([]);
  const [filter, setFilter] = useState("");
  const [open, setOpen] = useState<GuardAuditDetail | null>(null);

  const laad = useCallback(async () => {
    setRijen(await guardApi.audit({ outcome: filter || undefined, limit: 100 }));
  }, [filter]);

  useEffect(() => { laad().catch(() => undefined); }, [laad]);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Select className="w-52 text-xs" value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="">Alles</option>
          <option value="gemaskeerd">Gemaskeerd</option>
          <option value="geblokkeerd">Geblokkeerd</option>
          <option value="geweigerd">Opdracht geweigerd</option>
          <option value="doorgelaten">Doorgelaten</option>
        </Select>
        <span className="text-xs text-muted-foreground">
          Bewaard met de originele uitvoer, versleuteld, 14 dagen.
        </span>
      </div>

      <Card className="p-0">
        <div className="divide-y divide-border">
          {rijen.map((r) => (
            <div key={r.id} className="flex flex-wrap items-center gap-2 px-3 py-2 text-sm">
              <Badge tone={UITKOMST_TOON[r.outcome] || "neutral"}>{r.outcome}</Badge>
              {r.intent && (
                <span title={r.intent_mismatch ? r.intent_mismatch.uitleg
                                               : `De agent verklaarde: ${r.intent}`}>
                  <Badge tone={r.intent_mismatch ? "red" : "neutral"}>
                    {r.intent_mismatch ? `${r.intent} ✗` : r.intent}
                  </Badge>
                </span>
              )}
              <span className="text-xs text-muted-foreground">{tijd(r.ts)}</span>
              <code className="max-w-[28rem] truncate text-xs">{r.command}</code>
              {r.findings.length > 0 && (
                <span className="text-[11px] text-muted-foreground">
                  {r.findings.map((f) => `${f.regel} ×${f.aantal}`).join(" · ")}
                </span>
              )}
              <span className="ml-auto text-[11px] text-muted-foreground">
                {r.bytes_original} → {r.bytes_delivered} B
              </span>
              {r.heeft_tekst && (
                <Button variant="ghost" className="text-xs"
                        title="Origineel en wat het model kreeg, naast elkaar"
                        onClick={() => guardApi.auditDetail(r.id).then(setOpen)}>
                  <Eye size={12} />
                </Button>
              )}
            </div>
          ))}
          {rijen.length === 0 && (
            <p className="px-3 py-3 text-xs text-muted-foreground">Nog niets vastgelegd.</p>
          )}
        </div>
      </Card>

      {open && (
        <Modal open onClose={() => setOpen(null)} wide
               title={`Wat er gebeurde — ${open.outcome}`}>
          <div className="space-y-3 text-sm">
            <div>
              <Label>Commando</Label>
              <pre className="overflow-auto rounded bg-secondary p-2 text-xs whitespace-pre-wrap">
                {open.command}
              </pre>
            </div>
            {open.intent && (
              <div className={`rounded-md border p-2 text-xs ${
                open.intent_mismatch ? "border-destructive/50 bg-destructive/5"
                                     : "border-border bg-secondary/40"}`}>
                <span className="font-medium">Verklaard: {open.intent}</span>
                {open.intent_mismatch ? (
                  <p className="mt-1 text-muted-foreground">
                    Kwam niet uit: {open.intent_mismatch.uitleg}. Aangeslagen op{" "}
                    {open.intent_mismatch.regels.join(", ")}.{" "}
                    {open.intent_mismatch.blokkeren
                      ? "Daarom is de uitvoer alsnog tegengehouden."
                      : "De treffers zijn gemaskeerd; de rest is doorgegaan."}
                  </p>
                ) : (
                  <p className="mt-1 text-muted-foreground">
                    De uitvoer paste bij wat de agent zei op te halen.
                  </p>
                )}
              </div>
            )}
            {open.findings.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {open.findings.map((f, i) => (
                  <Badge key={i} tone={ACTIE_TOON[f.actie] || "neutral"}>
                    {f.regel} ×{f.aantal} → {f.actie}
                  </Badge>
                ))}
              </div>
            )}
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <div>
                <Label>Uit de container ({open.bytes_original} B)</Label>
                <pre className="max-h-80 overflow-auto rounded border border-destructive/40 bg-secondary p-2 text-xs whitespace-pre-wrap">
                  {open.origineel ?? "(niet bewaard)"}
                </pre>
              </div>
              <div>
                <Label>Naar het model ({open.bytes_delivered} B)</Label>
                <pre className="max-h-80 overflow-auto rounded border border-border bg-secondary p-2 text-xs whitespace-pre-wrap">
                  {open.geleverd ?? "(niets — tegengehouden)"}
                </pre>
              </div>
            </div>
            {open.llm_verdict && (
              <p className="text-xs text-muted-foreground">
                Lokaal model: {JSON.stringify(open.llm_verdict)}
              </p>
            )}
          </div>
        </Modal>
      )}
    </div>
  );
}
