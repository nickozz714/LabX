/**
 * pages/ClusterDetailPage.tsx — één cluster: de vaste laag en de branches.
 *
 * De twee lagen staan hier bewust onder elkaar en niet door elkaar, want het
 * verschil is het hele punt van een cluster: de vaste diensten BLIJVEN staan
 * terwijl je branch-labs weggooit en nieuwe maakt.
 *
 * Twee dingen krijgen meer ruimte dan hun omvang rechtvaardigt:
 *
 * - **Ontbrekende geheimen.** Een branch-lab dat gaat bouwen en veertig
 *   minuten later omvalt op een sleutel die niemand had klaargezet, is de
 *   duurste fout die dit scherm kan voorkomen. Dus staat die lijst bovenaan
 *   en niet onderin bij de instellingen.
 *
 * - **De basis bij een nieuwe branch.** Aftakken van `main` is de
 *   gewoonte, niet de regel. Van `release/2026.09` aftakken moet net zo
 *   makkelijk zijn, want dat achteraf rechtzetten in een lab is omslachtiger
 *   dan het meteen goed doen.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft, Box, Check, Copy, Download, ExternalLink, FileKey, GitBranch, KanbanSquare,
  KeyRound, Loader2, MessageSquare, Play, PlayCircle, Plus, RefreshCw, Search,
  Server, Square, Terminal, Trash2, TriangleAlert,
} from "lucide-react";
import { azureProfilesApi } from "@/lib/azureProfiles";
import { boardApi } from "@/lib/boards";
import { clusterApi } from "@/lib/clusters";
import type {
  BenodigdGeheim, Cluster, ClusterDienst, ClusterGeheim, ClusterPlan, EnvBestand,
  Toegang, TunnelOs,
} from "@/lib/clusters";
import { TUNNEL_OS } from "@/lib/clusters";
import {
  Badge, Button, Card, EmptyState, Input, Label, Modal, Select, TextArea,
} from "@/components/ui";
import { useMelding } from "@/components/Meldingen";
import { useBevestiging } from "@/components/Bevestiging";
import { ApiError } from "@/lib/api";

function DienstRegel({ d, onActie }: { d: ClusterDienst; onActie: () => void }) {
  const melding = useMelding();
  const bevestig = useBevestiging();

  async function doe(wat: "start" | "stop" | "weg") {
    try {
      if (wat === "start") await clusterApi.dienstStart(d.id);
      if (wat === "stop") await clusterApi.dienstStop(d.id);
      if (wat === "weg") {
        const ja = await bevestig.vraag({
          titel: `Dienst '${d.naam}' verwijderen?`,
          tekst: "Branch-labs die hem op naam aanspreken vinden hem daarna niet meer.",
          variant: "danger",
          bevestig: "Verwijderen",
        });
        if (!ja) return;
        await clusterApi.dienstWeg(d.id);
      }
      onActie();
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Dat lukte niet");
    }
  }

  const adressen = Object.entries(d.poort_map || {});
  return (
    <li className="rounded-lg border border-border p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{d.naam}</span>
            <Badge tone={d.draait ? "green" : d.status === "fout" ? "red" : "neutral"}>
              {d.draait ? "draait" : d.status}
            </Badge>
          </div>
          <p className="mt-0.5 truncate font-mono text-xs text-muted-foreground">{d.image}</p>
          {adressen.length > 0 && (
            <p className="mt-1 text-xs text-muted-foreground">
              van buiten:{" "}
              {adressen.map(([container, machine]) => (
                <span key={container} className="mr-2 font-mono">
                  :{machine} → {container}
                </span>
              ))}
            </p>
          )}
          {d.fout && (
            <p className="mt-1 flex items-start gap-1.5 text-xs text-destructive">
              <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {d.fout}
            </p>
          )}
        </div>
        <div className="flex shrink-0 gap-1">
          {d.draait
            ? <Button variant="secondary" onClick={() => doe("stop")}><Square className="h-3.5 w-3.5" /> Stop</Button>
            : <Button variant="secondary" onClick={() => doe("start")}><Play className="h-3.5 w-3.5" /> Start</Button>}
          <Button variant="ghost" onClick={() => doe("weg")} aria-label="Verwijderen">
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </li>
  );
}

/** De kluis van dit cluster.
 *
 * Staat bovenaan en niet onderin bij de instellingen, om dezelfde reden als
 * de lijst erboven: een branch-lab bouwt eerst twintig minuten en valt dan
 * pas om op een ontbrekende sleutel. Dat kost je een ochtend; dit kaartje
 * kost een minuut.
 *
 * De namen staan er al in — ze komen uit het plan, van de verkenner. Je hoeft
 * dus niet te raden hoe iets heet, alleen de waarde te plakken.
 */
/** De deploy key van dit cluster.
 *
 * Staat hier omdat een token bij een organisatie met goedkeuringsbeleid
 * tegengehouden kan worden — en dan sta je stil tot iemand anders klikt. Een
 * deploy key is op REPO-niveau en valt buiten dat beleid: wie admin is op de
 * repo zet hem erin, en klaar.
 *
 * Per cluster een eigen sleutel, niet één voor heel LabX. Intrekken raakt dan
 * alleen dit cluster, en bij de repo zie je welke omgeving toegang heeft.
 */
/**
 * De proefbouw: het startscript één keer echt draaien.
 *
 * Een plan dat er goed uitziet maar waarvan het script omvalt, heeft zijn
 * werk niet af. Het model schrijft dat script op uit wat het LAS, en dat is
 * een gok die pas bij het uitvoeren waar of onwaar wordt.
 *
 * En "af" is niet "exitcode 0". Af is wanneer JIJ zegt dat de branch goed is
 * — dus blijft het proeflab staan, met zijn adres, zijn terminal en zijn
 * bestanden, tot je het bevestigt. Daarom staan hier twee knoppen en niet één.
 */
function ProefKaart({ c, onWijziging }: { c: Cluster; onWijziging: () => void }) {
  const melding = useMelding();
  const [bezig, setBezig] = useState(false);
  const [afkeurOpen, setAfkeurOpen] = useState(false);
  const [opmerking, setOpmerking] = useState("");

  const adres = [...(c.proef_log || [])].reverse().find((r) => r.adres)?.adres || null;

  async function doe(taak: Promise<unknown>, gelukt: string) {
    setBezig(true);
    try {
      await taak;
      melding.ok(gelukt);
      onWijziging();
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Dat lukte niet");
    } finally {
      setBezig(false);
    }
  }

  return (
    <Card className="p-4">
      <div className="mb-3 flex items-start gap-2">
        <PlayCircle className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold">Proefbouw</h2>
            {c.proef_status === "bezig" && <Badge tone="yellow">bezig</Badge>}
            {c.proef_status === "wacht_op_fiat" && <Badge tone="violet">wacht op jouw oordeel</Badge>}
            {c.proef_status === "goedgekeurd" && <Badge tone="green">goedgekeurd</Badge>}
            {c.proef_status === "mislukt" && <Badge tone="red">niet gelukt</Badge>}
          </div>
          <p className="text-xs text-muted-foreground">
            Het startscript wordt echt gedraaid op een proefbranch. Faalt het, dan stelt
            LabX het bij op grond van de echte fout — hooguit drie keer. Daarna is het aan jou.
          </p>
        </div>
      </div>

      {(adres || c.proef_lab_id) && (
        <div className="mb-3 flex flex-wrap items-center gap-2">
          {adres && (
            <a href={adres} target="_blank" rel="noreferrer"
               className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 font-mono text-xs hover:bg-secondary">
              <ExternalLink size={12} /> {adres}
            </a>
          )}
          {c.proef_lab_id && (
            <Link to={`/labs/${c.proef_lab_id}`} className="text-xs underline">
              naar het proeflab
            </Link>
          )}
        </div>
      )}

      {(c.proef_log || []).length > 0 && (
        <ol className="mb-3 space-y-2">
          {c.proef_log.map((r, i) => (
            <li key={i} className="rounded-lg border border-border p-2 text-xs">
              <div className="flex flex-wrap items-center gap-2">
                {r.ronde > 0 && <span className="text-muted-foreground">ronde {r.ronde}</span>}
                <span className="font-medium">{r.fase}</span>
                {r.gewijzigd && <Badge tone="neutral">script aangepast</Badge>}
              </div>
              {r.toelichting && <p className="mt-1 leading-relaxed">{r.toelichting}</p>}
              {r.fout && (
                <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap rounded bg-muted/40 p-2 font-mono text-[11px]">
                  {r.fout}
                </pre>
              )}
            </li>
          ))}
        </ol>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {/* Jouw oordeel staat ALTIJD open zodra er een proefbouw geweest is,
            ook als LabX hem mislukt noemt. Die kan er namelijk naast zitten:
            de poortcontrole zei ooit "er antwoordt niets" terwijl de
            applicatie gewoon HTTP 200 gaf. Jij kijkt op het adres; LabX
            kijkt naar een signaal. */}
        {c.proef_status !== "geen" && c.proef_status !== "bezig" && (
          <>
            <Button onClick={() => doe(clusterApi.proefBevestigen(c.id), "Cluster klaar")}
                    disabled={bezig}>
              <Check className="h-4 w-4" />
              {c.proef_status === "goedgekeurd" ? "Bevestigd" : "Dit werkt — cluster is klaar"}
            </Button>
            <Button variant="secondary" onClick={() => setAfkeurOpen(true)} disabled={bezig}>
              Nog niet goed
            </Button>
          </>
        )}
        {c.proef_status !== "wacht_op_fiat" && (
          <Button variant="secondary" disabled={bezig || !c.start_script}
                  onClick={() => doe(clusterApi.proefbouw(c.id),
                                     "Proefbouw klaar — kijk of de branch goed is")}>
            {bezig ? <Loader2 className="h-4 w-4 animate-spin" /> : <PlayCircle className="h-4 w-4" />}
            {c.proef_status === "geen" ? "Proefbouw starten" : "Opnieuw proberen"}
          </Button>
        )}
        {!c.start_script && (
          <span className="text-xs text-muted-foreground">
            Eerst een plan goedkeuren; zonder startscript valt er niets te proberen.
          </span>
        )}
        {bezig && (
          <span className="text-xs text-muted-foreground">
            Dit duurt zo lang als de bouw duurt — bij een monorepo minuten.
          </span>
        )}
      </div>

      <Modal open={afkeurOpen} onClose={() => setAfkeurOpen(false)} title="Wat is er niet goed?">
        <p className="text-sm text-muted-foreground">
          Jouw woorden gaan mee de volgende ronde in. Dit is geen mislukking maar de normale
          gang van zaken: het model weet wat de repo zegt, jij weet wat je wilde zien.
        </p>
        <TextArea
          className="mt-3"
          rows={3}
          value={opmerking}
          onChange={(e) => setOpmerking(e.target.value)}
          placeholder="Bijvoorbeeld: de front-end laadt wel maar de API geeft 502, en er staat niets in de logs."
        />
        <div className="mt-4 flex gap-2">
          <Button
            disabled={!opmerking.trim() || bezig}
            onClick={async () => {
              setAfkeurOpen(false);
              await doe(clusterApi.proefAfkeuren(c.id, opmerking.trim()), "Opnieuw geprobeerd");
              setOpmerking("");
            }}
          >
            Opnieuw proberen
          </Button>
          <Button variant="secondary" onClick={() => setAfkeurOpen(false)}>Annuleren</Button>
        </div>
      </Modal>
    </Card>
  );
}

function SleutelKaart({ c, onWijziging }: { c: Cluster; onWijziging: () => void }) {
  const melding = useMelding();
  const bevestig = useBevestiging();
  const [bezig, setBezig] = useState(false);
  const [gekopieerd, setGekopieerd] = useState(false);

  async function maak() {
    if (c.ssh_publiek) {
      const ja = await bevestig.vraag({
        titel: "Een nieuwe sleutel maken?",
        tekst: "De huidige werkt daarna niet meer en moet bij de repo weggehaald worden.",
        bevestig: "Nieuwe maken",
      });
      if (!ja) return;
    }
    setBezig(true);
    try {
      await clusterApi.sleutelMaken(c.id);
      melding.ok("Sleutel gemaakt. Zet de publieke helft bij de repo.");
      onWijziging();
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Sleutel maken mislukte");
    } finally {
      setBezig(false);
    }
  }

  async function weg() {
    const ja = await bevestig.vraag({
      titel: "Deploy key verwijderen?",
      tekst: "Dit cluster kloont daarna alleen nog met een token, als dat er is.",
      variant: "danger",
      bevestig: "Verwijderen",
    });
    if (!ja) return;
    await clusterApi.sleutelWeg(c.id);
    onWijziging();
  }

  const repoPad = (c.repo_url || "").replace(/^https?:\/\/github\.com\//, "").replace(/\.git$/, "");

  return (
    <Card className="p-4">
      <div className="mb-3 flex items-start gap-2">
        <KeyRound className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
        <div>
          <h2 className="text-sm font-semibold">Toegang tot de repo</h2>
          <p className="text-xs text-muted-foreground">
            {c.toegang_soort === "ssh"
              ? "Dit cluster kloont met een eigen deploy key."
              : c.toegang_soort === "https"
                ? "Dit cluster kloont met een token uit de kluis."
                : "Dit cluster heeft nog geen toegang tot zijn repo."}
          </p>
        </div>
      </div>

      {c.ssh_publiek ? (
        <>
          <p className="mb-2 text-xs text-muted-foreground">
            Zet deze regel bij de repo onder <span className="font-medium">Settings → Deploy keys →
            Add deploy key</span>
            {repoPad && <> (<span className="font-mono">github.com/{repoPad}/settings/keys</span>)</>}.
            Vink <span className="font-medium">Allow write access</span> aan als een agent vanuit
            een branch-lab moet kunnen pushen.
          </p>
          <pre className="overflow-x-auto rounded-lg bg-muted/40 p-3 font-mono text-xs">
            {c.ssh_publiek}
          </pre>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button
              variant="secondary"
              onClick={async () => {
                await navigator.clipboard.writeText(c.ssh_publiek || "");
                setGekopieerd(true);
                setTimeout(() => setGekopieerd(false), 2000);
              }}
            >
              {gekopieerd ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
              {gekopieerd ? "Gekopieerd" : "Kopiëren"}
            </Button>
            <Button variant="secondary" onClick={maak} disabled={bezig}>
              {bezig ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
              Nieuwe sleutel
            </Button>
            <Button variant="ghost" onClick={weg}>
              <Trash2 className="h-4 w-4" /> Verwijderen
            </Button>
          </div>
        </>
      ) : (
        <div className="space-y-2">
          <p className="text-xs text-muted-foreground">
            Werkt een token niet omdat de organisatie het nog moet goedkeuren? Een deploy key
            is op repo-niveau en valt buiten dat beleid. LabX maakt een sleutelpaar, bewaart de
            privéhelft, en jij plakt de publieke helft één keer bij de repo.
          </p>
          <Button variant="secondary" onClick={maak} disabled={bezig}>
            {bezig ? <Loader2 className="h-4 w-4 animate-spin" /> : <KeyRound className="h-4 w-4" />}
            Deploy key maken
          </Button>
        </div>
      )}
    </Card>
  );
}

function KluisKaart({ clusterId, benodigd }: { clusterId: number; benodigd: Record<string, string> }) {
  const melding = useMelding();
  const bevestig = useBevestiging();
  const [eigen, setEigen] = useState<ClusterGeheim[]>([]);
  const [nodig, setNodig] = useState<BenodigdGeheim[]>([]);
  const [waarden, setWaarden] = useState<Record<string, string>>({});
  const [bezigMet, setBezigMet] = useState<string | null>(null);
  const [extraNaam, setExtraNaam] = useState("");

  const laad = useCallback(async () => {
    try {
      const uit = await clusterApi.geheimen(clusterId);
      setEigen(uit.eigen);
      setNodig(uit.benodigd);
    } catch {
      /* de clusterpagina werkt ook zonder dit kaartje */
    }
  }, [clusterId]);

  useEffect(() => { void laad(); }, [laad]);

  async function bewaar(naam: string) {
    const waarde = (waarden[naam] || "").trim();
    if (!waarde) return;
    setBezigMet(naam);
    try {
      await clusterApi.geheimZetten(clusterId, naam, {
        waarde,
        omschrijving: benodigd[naam] || undefined,
      });
      setWaarden((o) => ({ ...o, [naam]: "" }));
      melding.ok(`${naam} opgeslagen in de kluis van dit cluster`);
      void laad();
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Opslaan mislukte");
    } finally {
      setBezigMet(null);
    }
  }

  async function weg(naam: string) {
    const ja = await bevestig.vraag({
      titel: `'${naam}' uit de kluis van dit cluster halen?`,
      tekst: "Branch-labs krijgen hem daarna niet meer mee. Bestaat er een gedeeld "
        + "geheim met dezelfde naam, dan geldt dat weer.",
      variant: "danger",
      bevestig: "Verwijderen",
    });
    if (!ja) return;
    try {
      await clusterApi.geheimWeg(clusterId, naam);
      void laad();
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Verwijderen mislukte");
    }
  }

  const eigenNamen = new Set(eigen.map((g) => g.naam));
  const los = eigen.filter((g) => !(g.naam in benodigd));
  // Dekken de geïmporteerde env-bestanden alles wat dit cluster nodig heeft,
  // dan valt er hier niets meer te doen. Een rij groene regels suggereert werk
  // dat er niet is. De regels blijven wel opvraagbaar: WELK bestand welke
  // sleutel levert, is precies wat je wilt weten als er straks iets mist.
  const allesUitEnv = nodig.length > 0 && nodig.every((n) => n.in_env_bestand) && los.length === 0;
  const [toonSleutels, setToonSleutels] = useState(false);
  const regels = [
    ...nodig.map((n) => ({ naam: n.naam, waarvoor: n.waarvoor, gevonden: n.gevonden,
                           eigen: n.in_eigen_kluis, uitEnv: n.in_env_bestand || null,
                           verplicht: true })),
    ...los.map((g) => ({ naam: g.naam, waarvoor: g.omschrijving || "", gevonden: true,
                         eigen: true, uitEnv: null, verplicht: false })),
  ];

  return (
    <Card className="p-4">
      <div className="mb-3 flex items-start gap-2">
        <KeyRound className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
        <div>
          <h2 className="text-sm font-semibold">Kluis van dit cluster</h2>
          <p className="text-xs text-muted-foreground">
            Elk branch-lab krijgt deze waarden mee als omgevingsvariabele. Ze staan los
            van de gedeelde kluis, zodat <span className="font-mono">JWT_SECRET</span> hier
            iets anders kan zijn dan in een ander cluster. Wordt een naam hier niet
            gevonden, dan telt de gedeelde kluis nog steeds.
          </p>
        </div>
      </div>

      {allesUitEnv && !toonSleutels ? (
        <div className="rounded-lg border border-emerald-500/40 bg-emerald-500/5 p-3">
          <p className="text-sm">
            Niets nodig in deze kluis — alle {nodig.length} sleutels komen uit de
            geïmporteerde env-bestanden.
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            Die bestanden gaan bij elke start mee naar de werkkopie. Zet een sleutel hier
            alleen neer als hij óók als omgevingsvariabele nodig is — bijvoorbeeld omdat
            het startscript hem leest, of omdat een vaste dienst hem nodig heeft voordat
            de repo er is.
          </p>
          <Button className="mt-2" variant="ghost" onClick={() => setToonSleutels(true)}>
            Toon de sleutels
          </Button>
        </div>
      ) : regels.length === 0 ? (
        <EmptyState>
          Nog niets nodig. Zodra een verkenning zegt dat deze applicatie geheimen nodig
          heeft, staan de namen hier klaar.
        </EmptyState>
      ) : (
        <ul className="space-y-3">
          {regels.map((r) => (
            <li key={r.naam} className="rounded-lg border border-border p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-sm">{r.naam}</span>
                {r.eigen
                  ? <Badge tone="green">in deze kluis</Badge>
                  : r.uitEnv
                    ? <Badge tone="green">uit {r.uitEnv}</Badge>
                    : r.gevonden
                      ? <Badge tone="neutral">uit de gedeelde kluis</Badge>
                      : <Badge tone="red">ontbreekt</Badge>}
                {!r.verplicht && <span className="text-xs text-muted-foreground">extra</span>}
              </div>
              {r.waarvoor && (
                <p className="mt-0.5 text-xs text-muted-foreground">{r.waarvoor}</p>
              )}
              {r.uitEnv && !r.eigen ? (
                <p className="mt-1 text-xs text-muted-foreground">
                  Dit bestand gaat bij elke start mee naar de werkkopie, dus deze sleutel
                  hoeft niet in de kluis. Zet hem er alleen in als hij óók als
                  omgevingsvariabele nodig is.
                </p>
              ) : (
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <Input
                  type="password"
                  className="min-w-0 flex-1"
                  value={waarden[r.naam] || ""}
                  disabled={bezigMet === r.naam}
                  placeholder={r.eigen ? "Nieuwe waarde (leeg = laat staan)" : "Waarde plakken"}
                  onChange={(e) => setWaarden((o) => ({ ...o, [r.naam]: e.target.value }))}
                  onKeyDown={(e) => { if (e.key === "Enter") void bewaar(r.naam); }}
                />
                <Button
                  variant="secondary"
                  onClick={() => bewaar(r.naam)}
                  disabled={bezigMet === r.naam || !(waarden[r.naam] || "").trim()}
                >
                  {bezigMet === r.naam ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                  Opslaan
                </Button>
                {eigenNamen.has(r.naam) && (
                  <Button variant="ghost" onClick={() => weg(r.naam)} aria-label="Verwijderen">
                    <Trash2 className="h-4 w-4" />
                  </Button>
                )}
              </div>
              )}
            </li>
          ))}
        </ul>
      )}

      {allesUitEnv && toonSleutels && (
        <Button className="mt-2" variant="ghost" onClick={() => setToonSleutels(false)}>
          Verberg de sleutels
        </Button>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Input
          className="min-w-0 flex-1"
          value={extraNaam}
          placeholder="Nog een geheim toevoegen — naam van de omgevingsvariabele"
          onChange={(e) => setExtraNaam(e.target.value)}
        />
        <Button
          variant="secondary"
          disabled={!extraNaam.trim() || eigenNamen.has(extraNaam.trim())}
          onClick={() => {
            setNodig((o) => [...o, { naam: extraNaam.trim(), waarvoor: "",
                                     in_eigen_kluis: false, gevonden: false }]);
            setExtraNaam("");
          }}
        >
          <Plus className="h-4 w-4" /> Toevoegen
        </Button>
      </div>
    </Card>
  );
}

export function ClusterDetailPage() {
  const { clusterId } = useParams<{ clusterId: string }>();
  const navigate = useNavigate();
  const melding = useMelding();

  const [c, setC] = useState<Cluster | null>(null);
  const [plannen, setPlannen] = useState<ClusterPlan[]>([]);
  const [bezig, setBezig] = useState(true);
  const [toegang, setToegang] = useState<Toegang | null>(null);
  const [branchOpen, setBranchOpen] = useState(false);
  const [verkenOpen, setVerkenOpen] = useState(false);
  const [verkenBranch, setVerkenBranch] = useState("main");
  const [instructie, setInstructie] = useState("");
  const [verkent, setVerkent] = useState(false);
  const [bordId, setBordId] = useState<number | null>(null);
  const [azureProfielen, setAzureProfielen] = useState<{ id: number; name: string }[]>([]);
  const [branch, setBranch] = useState("");
  const [basis, setBasis] = useState("main");
  const [maakt, setMaakt] = useState(false);

  const laad = useCallback(async () => {
    if (!clusterId) return;
    try {
      const [cluster, lijst] = await Promise.all([
        clusterApi.lees(Number(clusterId)),
        clusterApi.plannen(Number(clusterId)).catch(() => [] as ClusterPlan[]),
      ]);
      setC(cluster);
      setPlannen(lijst);
      // De staande opdracht van de laatste verkenning voorvullen: bijstellen
      // is bijna altijd aanvullen, en hem leeg laten betekent dat je hem
      // stilzwijgend weggooit bij de volgende ronde.
      // Het bord van dit cluster, als er een is. Zonder dit kan de
      // Bord-knop nergens heen wijzen.
      azureProfilesApi.list()
        .then((ps: { id: number; name: string }[]) =>
          setAzureProfielen(ps.map((x) => ({ id: x.id, name: x.name }))))
        .catch(() => { /* geen profielen is geen fout */ });
      boardApi.list()
        .then((bs) => setBordId(
          bs.find((b) => (b as { cluster_id?: number }).cluster_id === Number(clusterId))?.id ?? null))
        .catch(() => { /* geen borden is geen fout */ });
      const laatste = lijst.find((p) => p.instructie);
      if (laatste?.instructie) setInstructie(laatste.instructie);
      if (lijst[0]?.branch) setVerkenBranch(lijst[0].branch);
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Cluster niet op te halen");
    } finally {
      setBezig(false);
    }
  }, [clusterId, melding]);

  useEffect(() => { void laad(); }, [laad]);

  async function controleerToegang() {
    if (!c) return;
    try {
      setToegang(await clusterApi.toegang(c.id));
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Toegangscontrole mislukte");
    }
  }

  async function opnieuwVerkennen() {
    if (!c?.repo_url) return;
    setVerkent(true);
    try {
      const plan = await clusterApi.verkennen({
        repo_url: c.repo_url,
        branch: verkenBranch.trim() || "main",
        cluster_id: c.id,
        instructie: instructie.trim() || undefined,
      });
      setVerkenOpen(false);
      navigate(`/clusters/plannen/${plan.id}`);
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Verkennen mislukte");
    } finally {
      setVerkent(false);
    }
  }

  async function maakBranch() {
    if (!c || !branch.trim()) return;
    setMaakt(true);
    try {
      const lab = await clusterApi.branchLab(c.id, {
        branch: branch.trim(), basis: basis.trim() || "main",
      });
      melding.ok(`Branch-lab '${lab.name}' aangemaakt. Het inrichten loopt door op de achtergrond.`);
      setBranchOpen(false);
      setBranch("");
      void laad();
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Branch-lab aanmaken mislukte");
    } finally {
      setMaakt(false);
    }
  }

  if (bezig) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Ophalen…
      </div>
    );
  }
  if (!c) return <div className="p-6 text-sm text-muted-foreground">Cluster niet gevonden.</div>;

  return (
    <div className="mx-auto w-full max-w-4xl space-y-4 p-4 pb-24">
      <Link to="/clusters" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-4 w-4" /> Clusters
      </Link>

      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-lg font-semibold">{c.naam}</h1>
            <Badge tone={c.status === "draait" ? "green" : c.status === "fout" ? "red" : "neutral"}>
              {c.status}
            </Badge>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            <span className="inline-flex items-center gap-1"><Server className="h-3.5 w-3.5" />{c.machine}</span>
            {" · netwerk "}<span className="font-mono">{c.netwerk}</span>
          </p>
          {c.repo_url && <p className="truncate font-mono text-xs text-muted-foreground">{c.repo_url}</p>}
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={controleerToegang}>
            <KeyRound className="h-4 w-4" /> Toegang testen
          </Button>
          <Button
            variant="secondary"
            onClick={async () => {
              if (bordId) { navigate(`/boards/${bordId}`); return; }
              try {
                const b = await boardApi.create({
                  name: c.naam, cluster_id: c.id,
                  key_prefix: c.naam.replace(/[^A-Za-z0-9]/g, "").slice(0, 3).toUpperCase() || "CLU",
                });
                melding.ok("Bord aangemaakt voor dit cluster");
                navigate(`/boards/${b.id}`);
              } catch (e) {
                melding.fout(e instanceof ApiError ? e.message : "Bord maken mislukte");
              }
            }}
          >
            <KanbanSquare className="h-4 w-4" /> {bordId ? "Naar het bord" : "Bord maken"}
          </Button>
          <Button variant="secondary" onClick={() => setVerkenOpen(true)} disabled={!c.repo_url}>
            <Search className="h-4 w-4" /> Opnieuw verkennen
          </Button>
        </div>
      </div>

      {toegang && (
        <Card className={`p-3 text-sm ${toegang.ok === false ? "border-destructive/50 bg-destructive/5" : ""}`}>
          {!toegang.gecontroleerd
            ? "Niet te controleren — dit is geen GitHub-repo of er is geen token ingesteld."
            : toegang.ok
              ? `Het token kan bij de repo${toegang.mag_pushen ? " en mag pushen." : "."}`
              : "Het token komt er niet bij."}
          {toegang.hint && <p className="mt-1 text-xs text-muted-foreground">{toegang.hint}</p>}
        </Card>
      )}

      {/* Een project dat zijn geheimen zelf ophaalt, heeft een aanmelding
          nodig. In het lab kan niemand `az login` doen, dus die sessie moet
          van een profiel komen -- anders valt de helft van de applicatie om
          terwijl de rest gewoon draait, en dat ziet eruit als een half
          geslaagd lab. */}
      {(c.azure_profiel_nodig || c.azure_profiel_id) && (
        <Card className={`p-4 ${c.azure_profiel_nodig ? "border-amber-500/40 bg-amber-500/5" : ""}`}>
          <div className="flex flex-wrap items-center gap-2">
            <KeyRound className="h-4 w-4 shrink-0 text-muted-foreground" />
            <span className="text-sm font-semibold">Azure-aanmelding</span>
            {c.azure_profiel_id
              ? <Badge tone="green">profiel gekoppeld</Badge>
              : <Badge tone="yellow">nog geen profiel</Badge>}
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {c.geheimen_zelf
              ? "Deze applicatie haalt haar instellingen zelf uit een vault. Daarvoor moet er iemand aangemeld zijn, en in het lab kan dat niet — de sessie komt van dit profiel."
              : "Deze applicatie gebruikt de Azure CLI. Zonder aanmelding werkt die niet in het lab."}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Select
              className="max-w-xs"
              value={c.azure_profiel_id ?? ""}
              onChange={async (e: React.ChangeEvent<HTMLSelectElement>) => {
                try {
                  await clusterApi.wijzig(c.id, {
                    azure_profiel_id: e.target.value ? Number(e.target.value) : null,
                  } as Partial<Cluster>);
                  melding.ok("Azure-profiel gekoppeld");
                  laad();
                } catch (err) {
                  melding.fout(err instanceof ApiError ? err.message : "Koppelen mislukte");
                }
              }}
            >
              <option value="">Geen profiel</option>
              {azureProfielen.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </Select>
            <span className="text-xs text-muted-foreground">
              Elk branch-lab krijgt dit profiel mee.
            </span>
          </div>
        </Card>
      )}

      {/* ── de proefbouw ────────────────────────────────────────────────── */}
      <ProefKaart c={c} onWijziging={laad} />

      {/* ── toegang en geheimen ─────────────────────────────────────────── */}
      <SleutelKaart c={c} onWijziging={laad} />

      {/* ── de kluis ────────────────────────────────────────────────────── */}
      <KluisKaart clusterId={c.id} benodigd={c.benodigde_geheimen || {}} />

      {/* ── de vaste laag ────────────────────────────────────────────────── */}
      <EnvBestandenKaart clusterId={c.id} paden={c.env_paden || []} />

      <Card className="p-4">
        <div className="mb-3 flex items-start gap-2">
          <Box className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
          <div>
            <h2 className="text-sm font-semibold">Vaste diensten</h2>
            <p className="text-xs text-muted-foreground">
              Blijven staan terwijl branch-labs komen en gaan. Een branch-lab bereikt ze op
              naam, zonder poort of adres.
            </p>
          </div>
        </div>
        {c.diensten.length === 0
          ? <EmptyState>Nog geen vaste diensten. Verken de repo opnieuw als er een bij hoort.</EmptyState>
          : <ul className="space-y-2">
              {c.diensten.map((d) => <DienstRegel key={d.id} d={d} onActie={laad} />)}
            </ul>}
      </Card>

      {/* ── de branches ──────────────────────────────────────────────────── */}
      <Card className="p-4">
        <div className="mb-3 flex items-start justify-between gap-2">
          <div className="flex items-start gap-2">
            <GitBranch className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
            <div>
              <h2 className="text-sm font-semibold">Branch-labs</h2>
              <p className="text-xs text-muted-foreground">
                Eén lab per branch, met de code van díé branch. Wegwerpbaar.
              </p>
            </div>
          </div>
          <Button variant="secondary" onClick={() => setBranchOpen(true)} disabled={!c.repo_url}>
            <Plus className="h-4 w-4" /> Branch
          </Button>
        </div>
        {c.labs.length === 0
          ? <EmptyState>Nog geen branch-labs.</EmptyState>
          : <ul className="space-y-2">
              {c.labs.map((l) => (
                <li key={l.id} className="rounded-lg border border-border p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <Link to={`/labs/${l.id}`} className="min-w-0">
                      <span className="font-medium">{l.name}</span>
                      {l.branch && <span className="ml-2 font-mono text-xs text-muted-foreground">{l.branch}</span>}
                    </Link>
                    <div className="flex flex-wrap gap-1">
                      {/* Chatten en het bord staan hier omdat dit de plek is
                          waar je een branch uitkiest. Anders moet je de naam
                          van het lab onthouden en hem elders terugzoeken. */}
                      <Button variant="secondary" onClick={() => navigate(`/chat?lab=${l.id}`)}>
                        <MessageSquare className="h-3.5 w-3.5" /> Chat
                      </Button>
                      <TunnelKnop lab={l} />
                      {bordId && (
                        <Button variant="secondary"
                                onClick={() => navigate(`/boards/${bordId}?branch=${encodeURIComponent(l.branch || "")}`)}>
                          <KanbanSquare className="h-3.5 w-3.5" /> Bord
                        </Button>
                      )}
                      <Button
                        variant="secondary"
                        onClick={async () => {
                          try {
                            await clusterApi.branchStart(l.id);
                            melding.ok("Branch-lab gestart");
                            void laad();
                          } catch (e) {
                            melding.fout(e instanceof ApiError ? e.message : "Starten mislukte");
                          }
                        }}
                      >
                        <Play className="h-3.5 w-3.5" /> Start
                      </Button>
                      {/* Opnieuw starten van de APPLICATIE, niet van het lab.
                          Meestal niet nodig: draait het startscript een
                          devserver die meekijkt (Vite, nodemon), dan is een
                          wijziging van de agent meteen te zien -- gemeten op
                          dit cluster: index.html aanpassen en de pagina
                          serveert het direct.

                          Wel nodig als het script BOUWT in plaats van kijkt,
                          of als de applicatie is omgevallen. Vandaar een
                          stil knopje en geen prominente knop. */}
                      <Button
                        variant="ghost"
                        title="Applicatie opnieuw starten — meestal niet nodig: een devserver pikt wijzigingen zelf op"
                        onClick={async () => {
                          try {
                            await clusterApi.branchAppHerstarten(l.id);
                            melding.ok("Applicatie opnieuw gestart");
                          } catch (e) {
                            melding.fout(e instanceof ApiError ? e.message : "Herstarten mislukte");
                          }
                        }}
                      >
                        <RefreshCw className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </div>
                </li>
              ))}
            </ul>}
      </Card>

      {/* ── hoe het is ingericht ─────────────────────────────────────────── */}
      <Card className="p-4">
        <div className="mb-2 flex items-start gap-2">
          <Terminal className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
          <div>
            <h2 className="text-sm font-semibold">Inrichting</h2>
            <p className="text-xs text-muted-foreground">
              Geldt voor elk branch-lab. Eén keer opgeschreven, op één plek aan te passen.
            </p>
          </div>
        </div>
        <dl className="space-y-2 text-sm">
          <div className="flex gap-2">
            <dt className="w-32 shrink-0 text-muted-foreground">Lab-image</dt>
            <dd className="font-mono text-xs">{c.lab_image || "—"}</dd>
          </div>
          <div className="flex gap-2">
            <dt className="w-32 shrink-0 text-muted-foreground">Env-profiel</dt>
            <dd>{c.env_profiel || "—"}</dd>
          </div>
          <div className="flex gap-2">
            <dt className="w-32 shrink-0 text-muted-foreground">Luistert op</dt>
            <dd className="font-mono text-xs">
              {c.lab_poorten?.length ? c.lab_poorten.join(", ") : "3000, 7071 (standaard)"}
            </dd>
          </div>
          {Object.keys(c.omgeving).length > 0 && (
            <div className="flex gap-2">
              <dt className="w-32 shrink-0 text-muted-foreground">Omgeving</dt>
              <dd className="min-w-0 font-mono text-xs">
                {Object.entries(c.omgeving).map(([k, v]) => (
                  <div key={k} className="truncate">{k}={v}</div>
                ))}
              </dd>
            </div>
          )}
        </dl>
        {c.start_script && (
          <pre className="mt-3 max-h-56 overflow-auto rounded-lg bg-muted/40 p-3 font-mono text-xs leading-relaxed">
            {c.start_script}
          </pre>
        )}
      </Card>

      {/* ── wat er verkend is ────────────────────────────────────────────── */}
      {plannen.length > 0 && (
        <Card className="p-4">
          <h2 className="mb-2 text-sm font-semibold">Verkenningen</h2>
          <p className="mb-3 text-xs text-muted-foreground">
            Elk plan hoort bij een commit en blijft staan. Zo zie je wat er veranderd is in
            plaats van dat de nieuwste de vorige overschrijft.
          </p>
          <ul className="space-y-2">
            {plannen.map((p) => (
              <li key={p.id} className="flex items-center justify-between gap-2 rounded-lg border border-border p-3">
                <Link to={`/clusters/plannen/${p.id}`} className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-xs">{p.commit?.slice(0, 8) || "—"}</span>
                    <span className="text-xs text-muted-foreground">{p.branch}</span>
                    <Badge tone={p.status === "goedgekeurd" ? "green" : "neutral"}>{p.status}</Badge>
                    {!p.model_gebruikt && <Badge tone="yellow">zonder model</Badge>}
                  </div>
                  {p.samenvatting && (
                    <p className="mt-1 line-clamp-1 text-xs text-muted-foreground">{p.samenvatting}</p>
                  )}
                </Link>
                <RefreshCw className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Modal open={verkenOpen} onClose={() => setVerkenOpen(false)} title="Opnieuw verkennen">
        <p className="text-sm text-muted-foreground">
          De repo wordt opnieuw gelezen. Het huidige plan blijft staan, zodat je ziet wat
          er veranderd is.
        </p>
        <div className="mt-4 space-y-3">
          <div>
            <Label>Branch</Label>
            <Input value={verkenBranch} onChange={(e) => setVerkenBranch(e.target.value)}
                   placeholder="main" />
          </div>
          <div>
            <Label>Opdracht voor de verkenner</Label>
            <TextArea
              rows={4}
              value={instructie}
              onChange={(e) => setInstructie(e.target.value)}
              placeholder="Bijvoorbeeld: de agent in components/agent hoort er niet bij, en de spark-compose is een testopstelling."
            />
            <p className="mt-1 text-xs text-muted-foreground">
              Voorgevuld met wat er bij de vorige verkenning gold. Dit weegt zwaarder dan
              wat de repo suggereert, en het kan ook vondsten van de detectoren uitzetten.
            </p>
          </div>
        </div>
        <div className="mt-5 flex items-center gap-2">
          <Button onClick={opnieuwVerkennen} disabled={verkent}>
            {verkent ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
            {verkent ? "Bezig met lezen…" : "Verkennen"}
          </Button>
          <Button variant="secondary" onClick={() => setVerkenOpen(false)}>Annuleren</Button>
        </div>
        {verkent && (
          <p className="mt-2 text-xs text-muted-foreground">
            Dit duurt een halve tot twee minuten: klonen, lezen, en het model laten meekijken.
          </p>
        )}
      </Modal>

      <Modal open={branchOpen} onClose={() => setBranchOpen(false)} title="Branch-lab aanmaken">
        <div className="space-y-3">
          <div>
            <Label>Branch</Label>
            <Input value={branch} onChange={(e) => setBranch(e.target.value)}
                   placeholder="feature/zoek-filter" autoFocus />
            <p className="mt-1 text-xs text-muted-foreground">
              Bestaat hij al op de remote, dan wordt hij uitgecheckt. Zo niet, dan wordt hij
              afgetakt van de basis hieronder.
            </p>
          </div>
          <div>
            <Label>Aftakken van</Label>
            <Input value={basis} onChange={(e) => setBasis(e.target.value)} placeholder="main" />
            <p className="mt-1 text-xs text-muted-foreground">
              Hoeft geen <span className="font-mono">main</span> te zijn — van{" "}
              <span className="font-mono">release/2026.09</span> aftakken gaat net zo goed.
            </p>
          </div>
        </div>
        <div className="mt-5 flex gap-2">
          <Button onClick={maakBranch} disabled={maakt || !branch.trim()}>
            {maakt ? <Loader2 className="h-4 w-4 animate-spin" /> : <GitBranch className="h-4 w-4" />}
            Aanmaken
          </Button>
          <Button variant="secondary" onClick={() => setBranchOpen(false)}>Annuleren</Button>
        </div>
      </Modal>
    </div>
  );
}

/**
 * Een vaste poort op jouw machine naar dit branch-lab.
 *
 * LabX deelt oplopende machinepoorten uit vanaf 18000, zodat branches naast
 * elkaar kunnen draaien zonder elkaar te verdringen. Applicaties met een
 * OAuth-redirect eisen juist een vast nummer: `http://localhost:5173` staat
 * bij de identity provider geregistreerd en nergens anders, en daar valt in
 * de repo niets aan te doen.
 *
 * Waarom een venster en geen knop in de rij: je moet twee dingen weten die
 * nergens anders staan -- welk adres je daarna opent, en dat de tunnel na een
 * herstart weg is. Een knop die stil een bestand neerzet laat je met allebei
 * die vragen zitten.
 */
function TunnelKnop(
  { lab }: { lab: { id: string; name: string; branch: string | null;
                    poort_map?: Record<string, number> } },
) {
  const melding = useMelding();
  const [open, setOpen] = useState(false);
  const [bezig, setBezig] = useState<TunnelOs | null>(null);

  const poorten = Object.keys(lab.poort_map || {}).map(Number);
  const [poort, setPoort] = useState<number | undefined>(poorten[0]);

  async function haal(os: TunnelOs) {
    setBezig(os);
    try {
      const url = await clusterApi.branchTunnel(lab.id, os, poort);
      melding.ok("Tunnelscript gedownload",
                 `Start het en open daarna ${url}`);
      setOpen(false);
    } catch (e) {
      melding.fout("Tunnel maken lukte niet",
                   e instanceof ApiError ? e.message : String(e));
    } finally {
      setBezig(null);
    }
  }

  return (
    <>
      <Button variant="secondary" onClick={() => setOpen(true)}>
        <Download className="h-3.5 w-3.5" /> Tunnel
      </Button>
      <Modal open={open} onClose={() => setOpen(false)} klikBuitenSluit
             title={`Tunnel naar ${lab.branch || lab.name}`}>
        <p className="text-sm text-muted-foreground">
          LabX publiceert dit lab op een oplopend nummer, zodat meerdere branches
          naast elkaar kunnen draaien. Sommige applicaties eisen juist een vaste
          poort op <code>localhost</code> — een OAuth-redirect staat daar op
          geregistreerd en nergens anders. Dit scriptje legt die verbinding.
        </p>
        <p className="mt-2 text-sm text-muted-foreground">
          Je draait het zelf en laat het venster open staan. Sluiten stopt de
          tunnel, en na een herstart van je machine is hij weg — dat is opzet:
          een tunnel die blijft bestaan wijst op een dag naar een lab dat er
          niet meer is. Eén poort kan maar naar één branch tegelijk.
        </p>

        {poorten.length > 1 && (
          <div className="mt-4">
            <Label>Welke poort van dit lab?</Label>
            <Select value={poort} onChange={(e) => setPoort(Number(e.target.value))}>
              {poorten.map((q) => (
                <option key={q} value={q}>
                  {q} → wordt localhost:{q} op jouw machine
                </option>
              ))}
            </Select>
          </div>
        )}

        <div className="mt-4 flex flex-wrap gap-2">
          {TUNNEL_OS.map((o) => (
            <Button key={o.waarde} variant="secondary" disabled={bezig !== null}
                    onClick={() => void haal(o.waarde)}>
              {bezig === o.waarde
                ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                : <Download className="h-3.5 w-3.5" />}
              {o.naam}
            </Button>
          ))}
        </div>
      </Modal>
    </>
  );
}

/**
 * De env-bestanden van dit cluster.
 *
 * De bestanden die een applicatie lokaal laten werken staan vaak in
 * .gitignore en nergens anders. IntelligentHive zegt het met zoveel woorden
 * in components/app/.env.example: "ask these values from another developer".
 * Hun eigen start-dev-skill lost dat op door die bestanden uit een bestaande
 * hoofdcheckout te kopiëren — en precies dat is wat een branch-lab niet
 * heeft.
 *
 * Het cluster is hier de hoofdcheckout: importeer ze één keer, en elk lab
 * eronder krijgt ze bij het starten in zijn werkkopie.
 *
 * Je ziet alleen het pad en de NAMEN van de sleutels. De waarden komen niet
 * uit de API terug: ze gaan één kant op.
 */
function EnvBestandenKaart({ clusterId, paden }: { clusterId: number; paden: string[] }) {
  const melding = useMelding();
  const bevestig = useBevestiging();
  const [bestanden, setBestanden] = useState<EnvBestand[]>([]);
  const [pad, setPad] = useState("");
  const [inhoud, setInhoud] = useState("");
  const [open, setOpen] = useState(false);
  const [bezig, setBezig] = useState(false);

  const laad = useCallback(() => {
    clusterApi.envBestanden(clusterId).then(setBestanden).catch(() => { /* leeg is geen fout */ });
  }, [clusterId]);
  useEffect(laad, [laad]);

  // De verwachte plekken eerst, in de volgorde van de verkenning, en daarna
  // wat je zelf hebt geïmporteerd en wat de verkenning niet zag. Zo zie je in
  // één oogopslag wat er nog ontbreekt in plaats van een lijst die alleen
  // toont wat er al is.
  const slots = useMemo(() => {
    const perPad = new Map(bestanden.map((b) => [b.pad, b]));
    const uit = paden.map((pad) => ({ pad, bestand: perPad.get(pad) }));
    for (const b of bestanden) {
      if (!paden.includes(b.pad)) uit.push({ pad: b.pad, bestand: b });
    }
    return uit;
  }, [paden, bestanden]);

  async function kiesBestand(bestand: File) {
    setInhoud(await bestand.text());
    // De browser geeft geen pad mee, alleen de naam. Een gok die vaak klopt is
    // beter dan een leeg veld, en je kunt hem aanpassen voordat je opslaat.
    if (!pad) setPad(bestand.name.startsWith(".env") ? `components/app/${bestand.name}` : bestand.name);
  }

  async function bewaar() {
    setBezig(true);
    try {
      await clusterApi.envBestandZetten(clusterId, pad.trim(), inhoud);
      melding.ok("Bestand geïmporteerd", "Elk branch-lab krijgt het bij de volgende start.");
      setOpen(false);
      setPad("");
      setInhoud("");
      laad();
    } catch (e) {
      melding.fout("Importeren lukte niet", e instanceof ApiError ? e.message : String(e));
    } finally {
      setBezig(false);
    }
  }

  return (
    <>
      <Card className="p-4">
        <div className="mb-3 flex items-start gap-2">
          <FileKey className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
          <div className="min-w-0">
            <h2 className="text-sm font-semibold">Env-bestanden</h2>
            <p className="text-xs text-muted-foreground">
              Bestanden die in <code>.gitignore</code> staan en die een verse kloon dus niet
              heeft. Elk branch-lab krijgt ze bij het starten in zijn werkkopie. Je ziet hier
              alleen de namen van de sleutels — de waarden komen niet terug uit de server.
            </p>
          </div>
        </div>

        {slots.length === 0
          ? <EmptyState>
              De verkenning vond geen env-bestanden in deze repo. Hoort er toch een, importeer
              hem dan met de knop hieronder.
            </EmptyState>
          : <ul className="space-y-2">
              {slots.map((slot) => (
                <li key={slot.pad} className="rounded-md border p-2">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-mono text-xs">{slot.pad}</span>
                    <div className="flex items-center gap-2">
                      {slot.bestand
                        ? <Badge>{slot.bestand.aantal} {slot.bestand.aantal === 1 ? "sleutel" : "sleutels"}</Badge>
                        : <Badge tone="yellow">ontbreekt</Badge>}
                      <Button variant="ghost"
                              onClick={() => { setPad(slot.pad); setInhoud(""); setOpen(true); }}>
                        {slot.bestand
                          ? <RefreshCw className="h-3.5 w-3.5" />
                          : <Plus className="h-3.5 w-3.5" />}
                      </Button>
                      {slot.bestand && (
                        <Button
                          variant="ghost"
                          onClick={async () => {
                            const ja = await bevestig.vraag({
                              titel: `${slot.pad} verwijderen?`,
                              tekst: "Branch-labs krijgen dit bestand daarna niet meer; een lab "
                                + "dat al draait houdt wat er staat tot de volgende start.",
                              bevestig: "Verwijderen",
                              variant: "danger",
                            });
                            if (!ja) return;
                            await clusterApi.envBestandVerwijderen(clusterId, slot.bestand!.id);
                            laad();
                          }}>
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      )}
                    </div>
                  </div>
                  {slot.bestand && slot.bestand.sleutels.length > 0 && (
                    <p className="mt-1 break-words font-mono text-[11px] text-muted-foreground">
                      {slot.bestand.sleutels.join(", ")}
                    </p>
                  )}
                </li>
              ))}
            </ul>}

        <Button className="mt-3" variant="secondary" onClick={() => setOpen(true)}>
          <Plus className="h-3.5 w-3.5" /> Bestand importeren
        </Button>
      </Card>

      <Modal open={open} onClose={() => setOpen(false)} dirty={!!inhoud}
             title="Env-bestand importeren">
        <p className="text-sm text-muted-foreground">
          Kies het bestand of plak de inhoud. Het pad is waar het in de werkkopie hoort,
          gerekend vanaf de wortel van de repo.
        </p>

        <div className="mt-3" />
        <Label>Pad in de repo</Label>
        <Input value={pad} onChange={(e) => setPad(e.target.value)}
               placeholder="components/app/.env.local" />

        <div className="mt-3" />
        <Label>Bestand</Label>
        <input type="file" className="text-xs"
               onChange={(e) => { const f = e.target.files?.[0]; if (f) void kiesBestand(f); }} />

        <div className="mt-3" />
        <Label>Inhoud</Label>
        <TextArea rows={8} className="font-mono text-xs" value={inhoud}
                  onChange={(e) => setInhoud(e.target.value)}
                  placeholder="VITE_TENANT_ID=..." />

        <div className="mt-4 flex gap-2">
          <Button onClick={() => void bewaar()} disabled={bezig || !pad.trim() || !inhoud}>
            {bezig ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
            Importeren
          </Button>
          <Button variant="ghost" onClick={() => setOpen(false)}>Annuleren</Button>
        </div>
      </Modal>
    </>
  );
}
