/**
 * pages/ClusterPlanPage.tsx — een voorstel nakijken en goedkeuren.
 *
 * Dit is het scherm waar de hele opzet op staat of valt. De verkenner heeft
 * een repo gelezen en stelt een cluster voor; hier besluit JIJ wat daarvan
 * waar wordt. Tot je op goedkeuren drukt is er niets aangemaakt en draait er
 * niets.
 *
 * Drie dingen zijn met opzet zo en niet anders:
 *
 * - **Elk onderdeel toont zijn vindplaats.** Bestand en regelnummer, uit de
 *   repo zelf. Zonder die regel is een plan een mening, en een mening kun je
 *   niet nakijken — dan klik je op goedkeuren omdat het er plausibel uitziet.
 *
 * - **Onbevestigd staat UIT.** Het model is leidend in de verkenning, maar
 *   wat de detectoren niet konden terugvinden in de repo gaat niet vanzelf
 *   mee. Het staat er wel, zichtbaar, met wat het model erover zei — en met
 *   een vinkje dat jij kunt aanzetten. Dat is het verschil tussen "model
 *   leidend" en "model beslist".
 *
 * - **De tweede lijst staat er even groot bij.** Wat LabX niet kan nabouwen
 *   en dus uit de kluis moet komen, is geen bijzaak. Het is precies waar een
 *   branch-lab veertig minuten later op omvalt als niemand het vooraf
 *   opschreef.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  AlertTriangle, ArrowRight, Box, Check, FileText, HelpCircle, KeyRound,
  Layers, Loader2, Package, Settings2, Sparkles, Terminal,
} from "lucide-react";
import { clusterApi, SOORT_UITLEG } from "@/lib/clusters";
import type { ClusterPlan, Vondst, VondstKeuze } from "@/lib/clusters";
import { labHostsApi } from "@/lib/labs";
import { secretApi } from "@/lib/secrets";
import type { SecretDto } from "@/lib/secrets";
import type { LabHost } from "@/lib/types";
import { Badge, Button, Card, Input, Label, Select } from "@/components/ui";
import { useMelding } from "@/components/Meldingen";
import { ApiError } from "@/lib/api";

const SOORT_ICOON: Record<Vondst["soort"], typeof Box> = {
  dienst: Box,
  applicatie: Layers,
  lab_image: Package,
  startscript: Terminal,
  omgeving: Settings2,
  extern: KeyRound,
  geheim: KeyRound,
};

const VOLGORDE: Vondst["soort"][] = [
  "dienst", "applicatie", "lab_image", "startscript", "omgeving", "extern", "geheim",
];

/** Een vaststelling, geen voorstel: wat uit de repo gebouwd wordt, wordt
 *  gebouwd. Daar hoort geen vinkje bij, want er valt niets te kiezen. */
const ZONDER_VINKJE: Vondst["soort"][] = ["applicatie"];

/** Hiervan kan er precies EEN waar zijn. Vijf aangevinkte lab-images leveren
 *  er alsnog maar een op, en dan bepaalt de volgorde van een lus welke je
 *  krijgt -- dat is geen keuze maar een loterij. */
const EEN_UIT: Vondst["soort"][] = ["lab_image"];

function sleutelVan(v: { soort: string; sleutel: string }) {
  return `${v.soort}::${v.sleutel}`;
}

function zekerheidsTint(z: Vondst["zekerheid"]) {
  return z === "hoog" ? "green" : z === "midden" ? "yellow" : "neutral";
}

/** Waar een vondst vandaan komt, in gewone taal. "detector:compose" zegt
 *  niets tegen iemand die de code niet kent. */
function bronTekst(bron: string): string {
  if (bron === "model") return "voorgesteld door het model";
  if (bron.startsWith("detector:")) return `gevonden in ${bron.slice(9)}`;
  return bron;
}

function Vindplaats({ bewijs }: { bewijs: Vondst["bewijs"] }) {
  if (!bewijs.length) return null;
  return (
    <ul className="mt-2 space-y-1">
      {bewijs.slice(0, 4).map((b, i) => (
        <li key={i} className="flex items-start gap-2 text-xs text-muted-foreground">
          <FileText className="mt-0.5 h-3 w-3 shrink-0" />
          <span className="min-w-0">
            <span className="font-mono">{b.bestand}{b.regel ? `:${b.regel}` : ""}</span>
            {b.fragment && (
              <span className="ml-2 truncate font-mono opacity-70">{b.fragment}</span>
            )}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** Wat deze vondst concreet oplevert: een image met poorten, een lijst
 *  sleutels, een keuze. Een naam alleen zegt te weinig om op te klikken. */
function Inhoud({ v }: { v: Vondst }) {
  const w = v.waarde || {};
  if (v.soort === "applicatie") {
    return (
      <div className="mt-1 text-xs text-muted-foreground">
        gebouwd uit <span className="font-mono">{String(w.build ?? "de repo")}</span>
      </div>
    );
  }
  if (v.soort === "dienst" || v.soort === "lab_image") {
    const uitKluis = Object.keys(w.omgeving_uit_kluis || {});
    if (!w.image) return null;
    return (
      <div className="mt-1 text-xs text-muted-foreground">
        <div>
          <span className="font-mono">{w.image}</span>
          {!!w.poorten?.length && <span className="ml-2">poorten {w.poorten.join(", ")}</span>}
        </div>
        {!!Object.keys(w.lab_omgeving || {}).length && (
          <div className="mt-1 font-mono">
            {Object.entries(w.lab_omgeving || {}).map(([k, val]) => (
              <div key={k} className="truncate">{k}={val}</div>
            ))}
          </div>
        )}
        {uitKluis.length > 0 && (
          /* Variabelen waarvan het model de waarde niet wist. Die mogen geen
             omgevingswaarde worden -- dan draait een lab met een sleutel die
             iedereen kan raden -- dus ze gaan naar de kluislijst. */
          <div className="mt-1 flex items-start gap-1.5 text-amber-400">
            <KeyRound className="mt-0.5 h-3 w-3 shrink-0" />
            <span>moet uit de kluis komen: <span className="font-mono">{uitKluis.join(", ")}</span></span>
          </div>
        )}
      </div>
    );
  }
  if (v.soort === "extern" && w.sleutels?.length) {
    return (
      <div className="mt-1 font-mono text-xs text-muted-foreground">{w.sleutels.join(", ")}</div>
    );
  }
  if (v.soort === "omgeving" && w.keuzes?.length) {
    return (
      <div className="mt-1 text-xs text-muted-foreground">
        {w.keuzes.join(" · ")}{w.standaard ? ` — standaard: ${w.standaard}` : ""}
      </div>
    );
  }
  if (v.soort === "startscript" && (w.bouwen?.length || w.starten?.length)) {
    return (
      <div className="mt-1 text-xs text-muted-foreground">
        {!!w.bouwen?.length && <span>bouwen: {w.bouwen.join(", ")}</span>}
        {!!w.bouwen?.length && !!w.starten?.length && <span className="mx-2">·</span>}
        {!!w.starten?.length && <span>starten: {w.starten.join(", ")}</span>}
      </div>
    );
  }
  return null;
}

export function ClusterPlanPage() {
  const { planId } = useParams<{ planId: string }>();
  const navigate = useNavigate();
  const melding = useMelding();

  const [plan, setPlan] = useState<ClusterPlan | null>(null);
  const [bezig, setBezig] = useState(true);
  const [opslaan, setOpslaan] = useState(false);
  const [aan, setAan] = useState<Set<string>>(new Set());
  const [naam, setNaam] = useState("");
  const [hostId, setHostId] = useState<string>("");
  const [tokenGeheim, setTokenGeheim] = useState<string>("");
  const [envProfiel, setEnvProfiel] = useState<string>("");
  const [hosts, setHosts] = useState<LabHost[]>([]);
  const [geheimen, setGeheimen] = useState<SecretDto[]>([]);

  const laad = useCallback(async () => {
    if (!planId) return;
    setBezig(true);
    try {
      const p = await clusterApi.plan(Number(planId));
      setPlan(p);
      setAan(new Set(p.standaardkeuze.map(sleutelVan)));
      setNaam(p.repo_url.replace(/\/$/, "").split("/").pop()?.replace(/\.git$/, "") || "");
      const profiel = p.vondsten.find((v) => v.soort === "omgeving" && v.sleutel === "env_profiel");
      setEnvProfiel(String(profiel?.waarde?.standaard || ""));
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Plan niet op te halen");
    } finally {
      setBezig(false);
    }
  }, [planId, melding]);

  useEffect(() => { void laad(); }, [laad]);
  useEffect(() => {
    labHostsApi.list().then((h) => setHosts(h.filter((x) => x.status === "gekoppeld"))).catch(() => {});
    secretApi.list().then(setGeheimen).catch(() => {});
  }, []);

  const groepen = useMemo(() => {
    if (!plan) return [];
    return VOLGORDE
      .map((soort) => ({ soort, items: plan.vondsten.filter((v) => v.soort === soort) }))
      .filter((g) => g.items.length > 0);
  }, [plan]);

  const profielKeuzes = useMemo(() => {
    const v = plan?.vondsten.find((x) => x.soort === "omgeving" && x.sleutel === "env_profiel");
    return (v?.waarde?.keuzes as string[] | undefined) || [];
  }, [plan]);

  const aantalAan = aan.size;
  const aantalKiesbaar = plan?.vondsten.filter((v) => !ZONDER_VINKJE.includes(v.soort)).length || 0;
  const aantalOnbevestigd = plan?.vondsten.filter((v) => !v.bevestigd).length || 0;

  function wissel(v: Vondst) {
    setAan((oud) => {
      const nieuw = new Set(oud);
      const k = sleutelVan(v);
      if (EEN_UIT.includes(v.soort)) {
        // Een keuze, geen lijstje: de vorige in deze groep gaat uit.
        for (const ander of plan?.vondsten || []) {
          if (ander.soort === v.soort) nieuw.delete(sleutelVan(ander));
        }
        if (!oud.has(k)) nieuw.add(k);
        return nieuw;
      }
      if (nieuw.has(k)) nieuw.delete(k); else nieuw.add(k);
      return nieuw;
    });
  }

  async function goedkeuren() {
    if (!plan) return;
    const gekozen: VondstKeuze[] = plan.vondsten
      .filter((v) => aan.has(sleutelVan(v)))
      .map((v) => ({ soort: v.soort, sleutel: v.sleutel }));
    setOpslaan(true);
    try {
      const uit = await clusterApi.goedkeuren(plan.id, {
        naam: plan.cluster_id ? undefined : naam.trim(),
        host_id: hostId ? Number(hostId) : null,
        gekozen,
        token_geheim: tokenGeheim || null,
        env_profiel: envProfiel || null,
      });
      melding.ok(
        uit.diensten_toegevoegd.length
          ? `Cluster klaar. Vaste diensten: ${uit.diensten_toegevoegd.join(", ")}.`
          : "Cluster klaar.");
      if (uit.geheimen_ontbreken.length) {
        melding.info(
          `Nog niet in de kluis: ${uit.geheimen_ontbreken.join(", ")}. ` +
          "Zonder die geheimen draait een branch-lab niet.");
      }
      navigate(`/clusters/${uit.cluster.id}`);
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Goedkeuren mislukte");
    } finally {
      setOpslaan(false);
    }
  }

  if (bezig) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Plan ophalen…
      </div>
    );
  }
  if (!plan) return <div className="p-6 text-sm text-muted-foreground">Plan niet gevonden.</div>;

  const alGoedgekeurd = plan.status === "goedgekeurd";

  return (
    <div className="mx-auto w-full max-w-4xl space-y-4 p-4 pb-24">
      {/* ── kop ─────────────────────────────────────────────────────────── */}
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-lg font-semibold">Voorstel voor een cluster</h1>
          {alGoedgekeurd && <Badge tone="green">goedgekeurd</Badge>}
          {plan.model_gebruikt
            ? <Badge tone="violet">model keek mee</Badge>
            : <Badge tone="yellow">zonder model</Badge>}
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          <span className="font-mono">{plan.repo_url}</span>
          {" · "}{plan.branch}
          {plan.commit && <> · <span className="font-mono">{plan.commit.slice(0, 8)}</span></>}
          {plan.bestanden_gelezen != null && <> · {plan.bestanden_gelezen} bestanden gelezen</>}
        </p>
      </div>

      {plan.url_omgezet && plan.url_melding && (
        <Card className="border-amber-500/40 bg-amber-500/5 p-3 text-sm">{plan.url_melding}</Card>
      )}

      {!plan.model_gebruikt && plan.fout && (
        <Card className="border-amber-500/40 bg-amber-500/5 p-3 text-sm">
          Het model keek niet mee ({plan.fout}). Dat is geen mislukte verkenning — de
          detectoren hebben dit plan neergelegd, het is alleen dunner dan het had kunnen zijn.
        </Card>
      )}

      {plan.samenvatting && (
        <Card className="p-4">
          <div className="flex items-start gap-2">
            <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-violet-400" />
            <p className="text-sm leading-relaxed">{plan.samenvatting}</p>
          </div>
        </Card>
      )}

      {/* ── wat er overgenomen wordt ────────────────────────────────────── */}
      {groepen.map((groep) => {
        const uitleg = SOORT_UITLEG[groep.soort];
        const Icoon = SOORT_ICOON[groep.soort];
        return (
          <Card key={groep.soort} className="p-4">
            <div className="mb-3 flex items-start gap-2">
              <Icoon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
              <div>
                <h2 className="text-sm font-semibold">{uitleg.titel}</h2>
                <p className="text-xs text-muted-foreground">{uitleg.onder}</p>
              </div>
            </div>
            <ul className="space-y-3">
              {groep.items.map((v) => {
                const k = sleutelVan(v);
                const kiesbaar = !ZONDER_VINKJE.includes(v.soort);
                const checked = kiesbaar ? aan.has(k) : true;
                return (
                  <li
                    key={k}
                    className={`rounded-lg border p-3 transition ${
                      checked ? "border-border bg-muted/30" : "border-dashed border-border/60"
                    }`}
                  >
                    <label className={`flex items-start gap-3 ${kiesbaar ? "cursor-pointer" : ""}`}>
                      {kiesbaar ? (
                        <input
                          type={EEN_UIT.includes(v.soort) ? "radio" : "checkbox"}
                          name={EEN_UIT.includes(v.soort) ? `keuze-${v.soort}` : undefined}
                          className="mt-1 h-4 w-4 shrink-0 accent-violet-500"
                          checked={checked}
                          disabled={alGoedgekeurd}
                          onChange={() => wissel(v)}
                        />
                      ) : (
                        <Layers className="mt-1 h-4 w-4 shrink-0 text-muted-foreground" />
                      )}
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-medium">{v.sleutel}</span>
                          <Badge tone={zekerheidsTint(v.zekerheid)}>{v.zekerheid}</Badge>
                          {!v.bevestigd && <Badge tone="red">niet teruggevonden</Badge>}
                          <span className="text-xs text-muted-foreground">{bronTekst(v.bron)}</span>
                        </div>
                        <Inhoud v={v} />
                        {v.toelichting && (
                          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                            {v.toelichting}
                          </p>
                        )}
                        <Vindplaats bewijs={v.bewijs} />
                      </div>
                    </label>
                  </li>
                );
              })}
            </ul>
          </Card>
        );
      })}

      {aantalOnbevestigd > 0 && (
        <Card className="border-amber-500/40 bg-amber-500/5 p-3 text-sm">
          <div className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-400" />
            <p>
              {aantalOnbevestigd === 1 ? "Eén onderdeel is" : `${aantalOnbevestigd} onderdelen zijn`}{" "}
              niet terug te vinden in de repo. Het model stelde {aantalOnbevestigd === 1 ? "het" : "ze"} voor,
              maar de detectoren vonden er geen bewijs voor — daarom {aantalOnbevestigd === 1 ? "staat het" : "staan ze"} uit.
              Weet jij beter, dan zet je {aantalOnbevestigd === 1 ? "het" : "ze"} gewoon aan.
            </p>
          </div>
        </Card>
      )}

      {/* ── het startscript ─────────────────────────────────────────────── */}
      {plan.startscript && (
        <Card className="p-4">
          <div className="mb-2 flex items-start gap-2">
            <Terminal className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
            <div>
              <h2 className="text-sm font-semibold">Startscript</h2>
              <p className="text-xs text-muted-foreground">
                Draait in elk branch-lab, in de werkkopie. Je kunt het later bij het
                cluster aanpassen — dan geldt het meteen voor alle branches.
              </p>
            </div>
          </div>
          <pre className="max-h-72 overflow-auto rounded-lg bg-muted/40 p-3 font-mono text-xs leading-relaxed">
            {plan.startscript}
          </pre>
        </Card>
      )}

      {/* ── wat het model niet wist ─────────────────────────────────────── */}
      {!!plan.vragen.length && (
        <Card className="p-4">
          <div className="mb-2 flex items-start gap-2">
            <HelpCircle className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
            <div>
              <h2 className="text-sm font-semibold">Niet uit de repo op te maken</h2>
              <p className="text-xs text-muted-foreground">
                Liever een vraag dan een gok. Dit zijn de dingen die je zelf moet beslissen.
              </p>
            </div>
          </div>
          <ul className="space-y-2 text-sm">
            {plan.vragen.map((v, i) => (
              <li key={i} className="flex gap-2">
                <span className="text-muted-foreground">·</span>
                <span className="leading-relaxed">{v}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {/* ── afronden ────────────────────────────────────────────────────── */}
      {!alGoedgekeurd && (
        <Card className="p-4">
          <h2 className="mb-3 text-sm font-semibold">Overnemen</h2>
          <div className="grid gap-3 sm:grid-cols-2">
            {!plan.cluster_id && (
              <div>
                <Label>Naam van het cluster</Label>
                <Input value={naam} onChange={(e) => setNaam(e.target.value)} placeholder="IntelligentHive" />
              </div>
            )}
            <div>
              <Label>Machine</Label>
              <Select value={hostId} onChange={(e) => setHostId(e.target.value)}>
                <option value="">deze server</option>
                {hosts.map((h) => <option key={h.id} value={h.id}>{h.naam}</option>)}
              </Select>
            </div>
            <div>
              <Label>Toegangstoken (uit de kluis)</Label>
              <Select value={tokenGeheim} onChange={(e) => setTokenGeheim(e.target.value)}>
                <option value="">geen — alleen publieke repo's</option>
                {geheimen.map((g) => <option key={g.name} value={g.name}>{g.name}</option>)}
              </Select>
            </div>
            {profielKeuzes.length > 0 && (
              <div>
                <Label>Omgevingsprofiel</Label>
                <Select value={envProfiel} onChange={(e) => setEnvProfiel(e.target.value)}>
                  {profielKeuzes.map((k) => <option key={k} value={k}>{k}</option>)}
                </Select>
              </div>
            )}
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-3">
            <Button onClick={goedkeuren} disabled={opslaan || (!plan.cluster_id && !naam.trim())}>
              {opslaan ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
              Goedkeuren en cluster maken
            </Button>
            <span className="text-xs text-muted-foreground">
              {aantalAan} van {aantalKiesbaar} onderdelen aangevinkt
            </span>
          </div>
          <p className="mt-2 text-xs text-muted-foreground">
            Hierna is dit plan bevroren. Verander er later iets aan de repo, dan verken je
            opnieuw — en zie je het verschil, in plaats van dat het stilletjes overschreven wordt.
          </p>
        </Card>
      )}

      {alGoedgekeurd && plan.cluster_id && (
        <Button variant="secondary" onClick={() => navigate(`/clusters/${plan.cluster_id}`)}>
          Naar het cluster <ArrowRight className="h-4 w-4" />
        </Button>
      )}
    </div>
  );
}
