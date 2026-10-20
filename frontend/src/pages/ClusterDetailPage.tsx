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
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  ScrollText,
  ArrowLeft, Bot, Box, Check, Copy, Download, ExternalLink, FileKey, GitBranch,
  KanbanSquare,
  KeyRound, LayoutDashboard, Loader2, MessageSquare, Network, Package, Play, PlayCircle,
  Plus,
  RefreshCw, Search, Server, ShieldCheck, SlidersHorizontal, Square, Terminal, Trash2,
  TriangleAlert, Wand2,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { azureProfilesApi } from "@/lib/azureProfiles";
import { boardApi } from "@/lib/boards";
import { labHostsApi, labsApi } from "@/lib/labs";
import type { LabExtra, LabHost } from "@/lib/types";
import { clusterApi } from "@/lib/clusters";
import type {
  BenodigdGeheim, Cluster, ClusterDienst, ClusterGeheim, ClusterPlan, EnvBestand,
  Toegang, TunnelOs,
} from "@/lib/clusters";
import { TUNNEL_OS } from "@/lib/clusters";
import {
  Badge, Button, Card, EmptyState, Input, Label, MenuKnop, Modal, Select, TextArea,
  Toggle,
} from "@/components/ui";
import { useMelding } from "@/components/Meldingen";
import { MODEL_OPTIONS } from "@/lib/modellen";
import { DienstActies } from "@/components/DienstActies";
import { ClusterBundels } from "@/components/ClusterBundels";
import { BranchGit } from "@/components/BranchGit";
import { uitTitel } from "@/lib/branchNaam";
import { useBevestiging } from "@/components/Bevestiging";
import { ApiError } from "@/lib/api";

function DienstRegel({ d, onActie }: { d: ClusterDienst; onActie: () => void }) {
  const melding = useMelding();
  const bevestig = useBevestiging();
  // Dicht tot je hem openklapt: de meeste keren kom je hier kijken of hij
  // draait, niet om er iets mee te doen. Pas bij het openklappen worden de
  // knoppen opgehaald.
  const [bedienen, setBedienen] = useState(false);
  // "Draait" is wat docker weet; "antwoordt" is wat je nodig hebt. Zolang
  // die twee niet samenvallen blijven we kijken — een emulator met data erin
  // doet daar minuten over, en zonder dit druk je op een knop die dan een
  // stacktrace van de client teruggeeft.
  const [gezond, setGezond] = useState<{ stand: string; klaar: boolean; melding: string } | null>(null);

  useEffect(() => {
    if (d.status !== "draait" && d.status !== "ophalen") { setGezond(null); return; }
    let levend = true;
    const meet = () => clusterApi.dienstGezondheid(d.id)
      .then((g) => { if (levend) setGezond(g); return g; })
      .catch(() => null);
    void meet();
    const klok = setInterval(() => { void meet().then((g) => { if (g?.klaar) clearInterval(klok); }); }, 6000);
    return () => { levend = false; clearInterval(klok); };
  }, [d.id, d.status]);

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
            <Badge tone={
              d.status === "fout" ? "red"
                : d.status === "ophalen" ? "violet"
                : gezond && !gezond.klaar ? "yellow"
                : d.draait ? "green" : "neutral"}>
              {d.status === "ophalen" ? "image ophalen…"
                : gezond?.stand === "start_op" ? "start op…"
                : d.draait ? (gezond?.klaar ? "klaar" : "draait") : d.status}
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
          {gezond && !gezond.klaar && !d.fout && (
            <p className="mt-1 text-xs text-muted-foreground">{gezond.melding}</p>
          )}
        </div>
        <div className="flex shrink-0 gap-1">
          <Button variant={bedienen ? "primary" : "secondary"}
                  onClick={() => setBedienen((x) => !x)}
                  disabled={!!gezond && !gezond.klaar}
                  title={gezond && !gezond.klaar ? gezond.melding
                                                 : "Knoppen en chat voor deze dienst"}>
            <Wand2 className="h-3.5 w-3.5" /> Bedienen
          </Button>
          {d.draait
            ? <Button variant="secondary" onClick={() => doe("stop")}><Square className="h-3.5 w-3.5" /> Stop</Button>
            : <Button variant="secondary" onClick={() => doe("start")}><Play className="h-3.5 w-3.5" /> Start</Button>}
          <Button variant="ghost" onClick={() => doe("weg")} aria-label="Verwijderen">
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {bedienen && (
        <div className="mt-3 border-t border-border pt-3">
          <DienstActies dienst={d} />
        </div>
      )}
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

function SleutelKaart(
  { c, onWijziging, toegang, onTesten }: {
    c: Cluster; onWijziging: () => void; toegang: Toegang | null; onTesten: () => void;
  },
) {
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

      {/* De uitslag hoort bij de kaart die over toegang gaat, niet bij een
          knop in de kop van de pagina waar hij losstaand bleef hangen. */}
      <div className="mt-3 border-t border-border pt-3">
        <Button variant="secondary" onClick={onTesten}>
          <KeyRound className="h-4 w-4" /> Toegang testen
        </Button>
        {toegang && (
          <p className={`mt-2 text-xs ${toegang.ok === false ? "text-destructive" : "text-muted-foreground"}`}>
            {!toegang.gecontroleerd
              ? "Niet te controleren — dit is geen GitHub-repo of er is geen token ingesteld."
              : toegang.ok
                ? `Het token kan bij de repo${toegang.mag_pushen ? " en mag pushen." : "."}`
                : "Het token komt er niet bij."}
            {toegang.hint && <> {toegang.hint}</>}
          </p>
        )}
      </div>
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

/**
 * Eén branch-lab in de lijst.
 *
 * Eigen component omdat het git-paneel open en dicht moet kunnen: dat haalt
 * de status op uit een wegwerpcontainer, en dat wil je niet voor elk lab
 * tegelijk doen zodra je de tab opent.
 *
 * De TITEL staat vooraan en de branchnaam eronder, niet andersom. Je zoekt
 * hier op waar iets over ging; de branchnaam is de vertaling daarvan voor
 * git. De beschrijving staat erbij omdat dat de enige plek is waar hij
 * bestaat -- hij gaat met opzet niet mee naar de repo.
 */
function BranchRegel({ lab, bordId, onActie }: {
  lab: Cluster["labs"][number];
  bordId: number | null;
  onActie: () => void;
}) {
  const navigate = useNavigate();
  const melding = useMelding();
  const bevestig = useBevestiging();
  const [gitOpen, setGitOpen] = useState(false);
  const [tunnelOpen, setTunnelOpen] = useState(false);

  const draait = lab.draait ?? false;
  const aanStart = lab.applicatie_starten !== false;
  // Het adres waarop JIJ hem opent. De eerste gepubliceerde poort is bijna
  // altijd de front-end; de rest haal je uit het labscherm.
  const poort = Object.values(lab.poort_map || {})[0];

  // Het adres komt van de server, want alleen die weet op welke machine dit
  // lab draait. Een vast `http://localhost:<poort>` klopte zolang alles op
  // één computer stond en wees daarna naar een poort waar niets luistert --
  // "applicatie gestart" naast een knop die stuk leek.
  const [opent, setOpent] = useState(false);
  // Het startlog van de applicatie. Dit stond al in de API en werd nergens
  // getoond, terwijl het precies het bewijsmateriaal is als er een wit scherm
  // komt: daar staat waarom. De server maskeert er geheimen uit -- gemeten
  // geval: een applicatie die zijn Key Vault-responses op DEBUG logt.
  const [logOpen, setLogOpen] = useState(false);
  const [logBezig, setLogBezig] = useState(false);
  const [logTekst, setLogTekst] = useState("");

  async function toonLog() {
    if (logOpen) { setLogOpen(false); return; }
    setLogBezig(true);
    try {
      const { log } = await clusterApi.branchStartLog(lab.id);
      setLogTekst(log || "(nog niets gelogd — is de applicatie al gestart?)");
      setLogOpen(true);
    } catch (e) {
      melding.fout("Log ophalen lukte niet",
                   e instanceof ApiError ? e.message : String(e));
    } finally {
      setLogBezig(false);
    }
  }


  async function openen() {
    // Eerst het tabblad, dan pas het adres: een browser blokkeert een popup
    // die na een await opengaat, en dan lijkt de knop opnieuw kapot.
    const tab = window.open("", "_blank");
    setOpent(true);
    try {
      const { url, via } = await clusterApi.branchAdres(lab.id);
      if (tab) tab.location.href = url;
      else melding.ok("Open dit adres zelf", url);
      if (via !== "deze machine") melding.ok(`Doorgezet via ${via}`, url);
    } catch (e) {
      tab?.close();
      melding.fout("Openen lukte niet",
                   e instanceof ApiError ? e.message : String(e));
    } finally {
      setOpent(false);
    }
  }

  async function doe(wat: "start" | "stop" | "herstart-app" | "stop-app") {
    try {
      if (wat === "start") {
        await clusterApi.branchStart(lab.id);
        melding.ok("Branch-lab gestart");
      } else if (wat === "stop") {
        await clusterApi.branchStop(lab.id);
        melding.ok("Branch-lab gestopt. Het werk blijft staan.");
      } else if (wat === "herstart-app") {
        await clusterApi.branchAppHerstarten(lab.id);
        melding.ok("Applicatie opnieuw gestart");
      } else {
        const uit = await clusterApi.branchAppStoppen(lab.id);
        melding.ok(uit.uitvoer.includes("gestopt")
          ? "Applicatie gestopt. Het lab blijft draaien."
          : "Er draaide geen applicatie.");
      }
      onActie();
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Dat lukte niet");
    }
  }

  async function weg() {
    const ja = await bevestig.vraag({
      titel: `'${lab.titel || lab.name}' verwijderen?`,
      tekst: "Het lab en zijn werkkopie verdwijnen. Wat niet gepusht is, is daarna weg.",
      variant: "danger",
      bevestig: "Verwijderen",
    });
    if (!ja) return;
    try {
      await labsApi.remove(lab.id);
      melding.ok("Branch-lab verwijderd");
      onActie();
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Verwijderen mislukte");
    }
  }

  return (
    <li className="rounded-lg border border-border p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex min-w-0 items-start gap-2">
          {/* Een stip en geen badge: de status is hier bijzaak zolang hij
              draait, en springt eruit zodra dat niet zo is. */}
          <span
            title={draait ? "draait" : (lab.status || "gestopt")}
            className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${
              draait ? "bg-green-500"
                     : lab.status === "error" ? "bg-destructive" : "bg-muted-foreground/40"}`}
          />
          <Link to={`/labs/${lab.id}`} className="min-w-0">
            <span className="font-medium">{lab.titel || lab.name}</span>
            {lab.branch && (
              <span className="ml-2 font-mono text-xs text-muted-foreground">{lab.branch}</span>
            )}
            {lab.beschrijving && (
              <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">{lab.beschrijving}</p>
            )}
            {!draait && (
              <p className="mt-0.5 text-xs text-muted-foreground">
                Gestopt — het werk staat er nog; starten pakt het weer op.
              </p>
            )}
            {draait && !aanStart && (
              <p className="mt-0.5 text-xs text-muted-foreground">
                Lab draait met de code; de applicatie staat uit. Start hem via ⋯ als je
                hem nodig hebt.
              </p>
            )}
          </Link>
        </div>

        {/* Drie dingen in beeld in plaats van zes: openen, git, en de rest.
            Zes knoppen op een rij is geen keuze meer maar een muur, en het
            verschil tussen wat je dagelijks gebruikt en wat je zelden nodig
            hebt was nergens aan te zien. */}
        <div className="flex shrink-0 flex-wrap items-center gap-1">
          {draait && poort && (
            <Button variant="secondary" disabled={opent} onClick={() => void openen()}>
              {opent
                ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                : <ExternalLink className="h-3.5 w-3.5" />} Openen
            </Button>
          )}
          <Button variant={logOpen ? "primary" : "secondary"} onClick={() => void toonLog()}>
            {logBezig
              ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
              : <ScrollText className="h-3.5 w-3.5" />} Log
          </Button>
          <Button variant={gitOpen ? "primary" : "secondary"} onClick={() => setGitOpen((x) => !x)}>
            <GitBranch className="h-3.5 w-3.5" /> Git
          </Button>
          <MenuKnop
            label="Meer acties"
            items={[
              draait
                ? { label: "Stoppen", icon: <Square className="h-3.5 w-3.5" />,
                    onClick: () => doe("stop") }
                : { label: "Starten", icon: <Play className="h-3.5 w-3.5" />,
                    onClick: () => doe("start") },
              // Starten en herstarten zijn dezelfde handeling in de backend,
              // maar niet in je hoofd: "opnieuw starten" bij een applicatie
              // die nooit aanging leest als een knop die niets doet.
              { label: aanStart ? "Applicatie opnieuw starten" : "Applicatie starten",
                icon: <PlayCircle className="h-3.5 w-3.5" />,
                uit: !draait, onClick: () => doe("herstart-app") },
              // Stoppen van de APPLICATIE, niet van het lab. Voor een lab
              // waarin je alleen code leest: de container heb je nodig, de
              // devserver niet.
              { label: "Applicatie stoppen (lab blijft draaien)",
                icon: <Square className="h-3.5 w-3.5" />,
                uit: !draait, onClick: () => doe("stop-app") },
              { label: aanStart
                  ? "Applicatie niet meer vanzelf starten"
                  : "Applicatie voortaan vanzelf starten",
                icon: <RefreshCw className="h-3.5 w-3.5" />,
                onClick: async () => {
                  try {
                    await clusterApi.branchWijzigen(lab.id, { applicatie_starten: !aanStart });
                    melding.ok(aanStart
                      ? "Dit lab start voortaan alleen de container."
                      : "Dit lab start de applicatie voortaan vanzelf mee.");
                    onActie();
                  } catch (e) {
                    melding.fout(e instanceof ApiError ? e.message : "Opslaan mislukte");
                  }
                } },
              "scheiding",
              { label: "Chatten met dit lab", icon: <MessageSquare className="h-3.5 w-3.5" />,
                onClick: () => navigate(`/chat?lab=${lab.id}`) },
              { label: "Werkruimte openen", icon: <Terminal className="h-3.5 w-3.5" />,
                onClick: () => navigate(`/labs/${lab.id}`) },
              ...(bordId
                ? [{ label: "Naar het bord", icon: <KanbanSquare className="h-3.5 w-3.5" />,
                     onClick: () => navigate(
                       `/boards/${bordId}?branch=${encodeURIComponent(lab.branch || "")}`) }]
                : []),
              { label: "Tunnel naar een vaste poort…",
                icon: <Download className="h-3.5 w-3.5" />,
                uit: !draait, onClick: () => setTunnelOpen(true) },
              "scheiding",
              { label: "Branch-lab verwijderen", icon: <Trash2 className="h-3.5 w-3.5" />,
                gevaarlijk: true, onClick: weg },
            ]}
          />
        </div>
      </div>

      {gitOpen && (
        <div className="mt-3 space-y-3 border-t border-border pt-3">
          <BranchGit labId={lab.id} titel={lab.titel} onGewisseld={onActie} />
          <EigenStartscript lab={lab} onBewaard={onActie} />
        </div>
      )}

      {logOpen && (
        <div className="mt-2 rounded-md border border-border bg-muted/40 p-2">
          <div className="mb-1 flex items-center justify-between">
            <span className="text-xs text-muted-foreground">
              Startlog van de applicatie — geheimen zijn gemaskeerd
            </span>
            <Button variant="ghost" onClick={() => void toonLog()}>Sluiten</Button>
          </div>
          <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all
                          font-mono text-xs leading-relaxed">{logTekst}</pre>
        </div>
      )}
      <TunnelVenster lab={lab} open={tunnelOpen} onClose={() => setTunnelOpen(false)} />
    </li>
  );
}

/**
 * De tabs van dit scherm.
 *
 * Twaalf kaarten onder elkaar las niemand meer: wie een poort kwam aanpassen
 * scrolde langs de kluis, en wie een branch zocht langs het startscript. De
 * indeling volgt wat je kómt doen, niet hoe het model het cluster beschrijft.
 */
type TabSleutel = "overzicht" | "diensten" | "branches" | "toegang" | "instellingen";

const TABS: { key: TabSleutel; label: string; icon: LucideIcon }[] = [
  { key: "overzicht", label: "Overzicht", icon: LayoutDashboard },
  { key: "diensten", label: "Diensten", icon: Box },
  { key: "branches", label: "Branch-labs", icon: GitBranch },
  { key: "toegang", label: "Toegang & geheimen", icon: KeyRound },
  { key: "instellingen", label: "Instellingen", icon: SlidersHorizontal },
];

function TabTelling({ n }: { n: number }) {
  return (
    <span className="rounded-full bg-muted px-1.5 text-[10px] leading-4 text-muted-foreground">{n}</span>
  );
}

export function ClusterDetailPage() {
  const { clusterId } = useParams<{ clusterId: string }>();
  const navigate = useNavigate();
  const melding = useMelding();
  const [zoek, zetZoek] = useSearchParams();

  // De tab in de URL: terugkomen op dit scherm komt uit waar je was, en een
  // tab is door te sturen ("kijk bij toegang & geheimen").
  const gevraagd = zoek.get("tab") as TabSleutel | null;
  const tab: TabSleutel = TABS.some((t) => t.key === gevraagd) ? (gevraagd as TabSleutel) : "overzicht";
  const zetTab = useCallback((t: TabSleutel) => {
    const volgende = new URLSearchParams(zoek);
    volgende.set("tab", t);
    zetZoek(volgende, { replace: true });
  }, [zoek, zetZoek]);

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
  // De machines in het netwerk, voor het verplaatsen van het hele cluster.
  const [hosts, setHosts] = useState<LabHost[]>([]);
  const [titel, setTitel] = useState("");
  const [beschrijving, setBeschrijving] = useState("");
  // De branchnaam volgt de titel zolang je hem niet zelf aanraakt. Daarna
  // niet meer: een naam die onder je handen verandert omdat je de titel nog
  // even bijschaafde, is precies het soort verrassing dat je bij een push
  // niet wilt.
  const [branch, setBranch] = useState("");
  const [branchZelf, setBranchZelf] = useState(false);
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
      labHostsApi.list()
        .then(setHosts)
        .catch(() => { /* geen machines is geen fout */ });
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

  async function naarBord() {
    if (!c) return;
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
  }

  const branchNaam = branchZelf ? branch.trim() : uitTitel(titel);

  async function maakBranch() {
    if (!c || !branchNaam) return;
    setMaakt(true);
    try {
      const lab = await clusterApi.branchLab(c.id, {
        titel: titel.trim(),
        beschrijving: beschrijving.trim(),
        branch: branchNaam,
        basis: basis.trim() || "main",
      });
      melding.ok(`Branch-lab '${lab.name}' aangemaakt. Het inrichten loopt door op de achtergrond.`);
      setBranchOpen(false);
      setTitel("");
      setBeschrijving("");
      setBranch("");
      setBranchZelf(false);
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
        <div className="min-w-0">
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
          <Button variant="secondary" onClick={naarBord}>
            <KanbanSquare className="h-4 w-4" /> {bordId ? "Naar het bord" : "Bord maken"}
          </Button>
          <Button variant="secondary" onClick={() => setVerkenOpen(true)} disabled={!c.repo_url}>
            <Search className="h-4 w-4" /> Opnieuw verkennen
          </Button>
        </div>
      </div>

      {/* Eén strip in plaats van twaalf kaarten onder elkaar. De tab staat in
          de URL, zodat terugkomen op dezelfde plek uitkomt en je een tab kunt
          doorsturen. */}
      <div className="flex flex-wrap gap-1 border-b border-border text-sm">
        {TABS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            onClick={() => zetTab(key)}
            className={"flex items-center gap-1.5 px-3 py-1.5 "
              + (tab === key ? "border-b-2 border-primary font-medium" : "text-muted-foreground hover:text-foreground")}
          >
            <Icon className="h-3.5 w-3.5" />
            {label}
            {key === "diensten" && c.diensten.length > 0 && <TabTelling n={c.diensten.length} />}
            {key === "branches" && c.labs.length > 0 && <TabTelling n={c.labs.length} />}
            {key === "toegang" && c.azure_profiel_nodig && (
              <span className="h-1.5 w-1.5 rounded-full bg-amber-500" title="Er mist nog een Azure-profiel" />
            )}
          </button>
        ))}
      </div>

      {/* Een ontbrekend Azure-profiel laat de helft van de applicatie omvallen
          terwijl de rest doordraait. Dat mag je niet pas zien als je toevallig
          op de goede tab komt. */}
      {c.azure_profiel_nodig && tab !== "toegang" && (
        <button
          onClick={() => zetTab("toegang")}
          className="flex w-full items-center gap-2 rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 text-left text-sm"
        >
          <TriangleAlert className="h-4 w-4 shrink-0 text-amber-500" />
          <span>Deze applicatie heeft een Azure-aanmelding nodig, maar er is nog geen profiel gekoppeld.</span>
          <span className="ml-auto shrink-0 text-xs text-muted-foreground">Toegang & geheimen →</span>
        </button>
      )}

      {/* ── overzicht: de proefbouw en wat er verkend is ─────────────────── */}
      {tab === "overzicht" && (
        <div className="space-y-4">
          <ProefKaart c={c} onWijziging={laad} />

          <Card className="p-4">
            <h2 className="mb-2 text-sm font-semibold">Verkenningen</h2>
            <p className="mb-3 text-xs text-muted-foreground">
              Elk plan hoort bij een commit en blijft staan. Zo zie je wat er veranderd is in
              plaats van dat de nieuwste de vorige overschrijft.
            </p>
            {plannen.length === 0
              ? <EmptyState>Nog geen verkenning. Begin met “Opnieuw verkennen” hierboven.</EmptyState>
              : <ul className="space-y-2">
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
                </ul>}
          </Card>
        </div>
      )}

      {/* ── de vaste laag ────────────────────────────────────────────────── */}
      {tab === "diensten" && (
        <div className="space-y-4">
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

        {/* Hier en niet op een eigen tab: het enige wat je met een bundel
            doet is hem op een van de diensten hierboven loslaten. */}
        <ClusterBundels cluster={c} />
        </div>
      )}

      {/* ── de branches ──────────────────────────────────────────────────── */}
      {tab === "branches" && (
        <Card className="p-4">
          <div className="mb-3 flex items-start justify-between gap-2">
            <div className="flex items-start gap-2">
              <GitBranch className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
              <div>
                <h2 className="text-sm font-semibold">Branch-labs</h2>
                <p className="text-xs text-muted-foreground">
                  {c.repo_url
                    ? "Eén lab per branch, met de code van díé branch. Wegwerpbaar."
                    : "Dit cluster heeft nog geen repo. Het eerste lab begint leeg, met een verse git init en een eerste commit."}
                </p>
              </div>
            </div>
            <Button variant="secondary" onClick={() => setBranchOpen(true)}>
              <Plus className="h-4 w-4" /> Branch
            </Button>
          </div>
          {c.labs.length === 0
            ? <EmptyState>Nog geen branch-labs.</EmptyState>
            : <ul className="space-y-2">
                {c.labs.map((l) => (
                  <BranchRegel key={l.id} lab={l} bordId={bordId} onActie={laad} />
                ))}
              </ul>}
        </Card>
      )}

      {/* ── toegang en geheimen ─────────────────────────────────────────── */}
      {tab === "toegang" && (
        <div className="space-y-4">
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

          <SleutelKaart c={c} onWijziging={laad} toegang={toegang} onTesten={controleerToegang} />

          <EnvBestandenKaart clusterId={c.id} paden={c.env_paden || []} />

          <KluisKaart clusterId={c.id} benodigd={c.benodigde_geheimen || {}} />
        </div>
      )}

      {/* ── hoe het lab eruitziet ────────────────────────────────────────── */}
      {tab === "instellingen" && (
        <div className="space-y-4">
          <RepoKaart cluster={c} onWijzig={laad} />

          <PoortenKaart cluster={c} onWijzig={laad} />

          <MachineKaart cluster={c} hosts={hosts} onWijzig={laad} />

          <AgentKaart cluster={c} onWijzig={laad} />

          <GuardKaart cluster={c} onWijzig={laad} />

          <ExtrasKaart clusterId={c.id} gekozen={c.lab_extras || []} onWijzig={laad} />

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
        </div>
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

      <Modal open={branchOpen} onClose={() => setBranchOpen(false)} title="Nieuwe feature">
        <div className="space-y-3">
          <div>
            <Label>Titel</Label>
            <Input value={titel} onChange={(e) => setTitel(e.target.value)}
                   placeholder="Zoekfilter op het overzicht" autoFocus />
            {/* De branchnaam vóóraf laten zien en niet achteraf: hij gaat naar
                git, daar kijkt straks iemand anders naar, en hernoemen kan
                dan niet meer stilletjes. */}
            <p className="mt-1 text-xs text-muted-foreground">
              Dit wordt de naam van de branch:{" "}
              {branchNaam
                ? <span className="font-mono text-foreground">{branchNaam}</span>
                : <span className="italic">nog niets bruikbaars om een branchnaam van te maken</span>}
            </p>
          </div>
          <div>
            <Label>Beschrijving (intern)</Label>
            <TextArea rows={3} value={beschrijving}
                      onChange={(e) => setBeschrijving(e.target.value)}
                      placeholder="Waar gaat dit over, wat is er nog onzeker, waar moet je aan denken." />
            <p className="mt-1 text-xs text-muted-foreground">
              Blijft in LabX. Dit komt niet in de branchnaam en niet in een commit.
            </p>
          </div>
          <details open={branchZelf}>
            <summary className="cursor-pointer text-xs text-muted-foreground">
              Branchnaam en basis zelf bepalen
            </summary>
            <div className="mt-2 space-y-3">
              <div>
                <Label>Branchnaam</Label>
                <Input
                  value={branchZelf ? branch : branchNaam}
                  onChange={(e) => { setBranchZelf(true); setBranch(e.target.value); }}
                  placeholder="feature/zoek-filter" />
                <p className="mt-1 text-xs text-muted-foreground">
                  {c.repo_url
                    ? "Bestaat hij al op de remote, dan wordt hij uitgecheckt. Zo niet, dan wordt hij afgetakt van de basis hieronder."
                    : "Er is nog geen repo, dus er valt niets uit te checken: dit wordt de branch van een verse git init."}
                </p>
              </div>
              <div className={c.repo_url ? "" : "hidden"}>
                <Label>Aftakken van</Label>
                <Input value={basis} onChange={(e) => setBasis(e.target.value)} placeholder="main" />
                <p className="mt-1 text-xs text-muted-foreground">
                  Hoeft geen <span className="font-mono">main</span> te zijn — van{" "}
                  <span className="font-mono">release/2026.09</span> aftakken gaat net zo goed.
                </p>
              </div>
            </div>
          </details>
        </div>
        <div className="mt-5 flex gap-2">
          <Button onClick={maakBranch} disabled={maakt || !branchNaam}>
            {maakt ? <Loader2 className="h-4 w-4 animate-spin" /> : <GitBranch className="h-4 w-4" />}
            Aanmaken
          </Button>
          <Button variant="secondary" onClick={() => setBranchOpen(false)}>Annuleren</Button>
        </div>
      </Modal>
    </div>
  );
}

/** Het startscript van dít lab, los van dat van het cluster.
 *
 * Een cluster heeft één startscript voor al zijn branches, en dat is bijna
 * altijd goed. Bijna: een lab waarin je andermans werk nakijkt wil soms iets
 * anders draaien dan de rest — de devcontainer van de repo bijvoorbeeld, in
 * plaats van de losse diensten die het cluster start.
 *
 * Leeg laten betekent: volg het cluster. Met opzet geen aparte schakelaar —
 * een vinkje "eigen script" dat je aan kunt zetten met een leeg veld
 * eronder, levert een lab op dat niets start en nergens zegt waarom.
 */
function EigenStartscript({ lab, onBewaard }: {
  lab: Cluster["labs"][number];
  onBewaard: () => void;
}) {
  const melding = useMelding();
  const [open, setOpen] = useState(false);
  const [script, setScript] = useState<string | null>(null);
  const [bezig, setBezig] = useState(false);

  async function laad() {
    setOpen(true);
    if (script !== null) return;
    try {
      // Een lege PATCH verandert niets en geeft de huidige stand terug.
      const uit = await clusterApi.branchWijzigen(lab.id, {});
      setScript(uit.setup_script || "");
    } catch {
      setScript("");
    }
  }

  async function bewaar() {
    setBezig(true);
    try {
      await clusterApi.branchWijzigen(lab.id, { setup_script: script || "" });
      melding.ok((script || "").trim()
        ? "Eigen startscript bewaard. Het geldt bij de volgende start van dit lab."
        : "Dit lab volgt weer het startscript van het cluster.");
      onBewaard();
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Bewaren mislukte");
    } finally {
      setBezig(false);
    }
  }

  if (!open) {
    return (
      <button onClick={laad} className="text-xs text-muted-foreground hover:text-foreground">
        Eigen startscript voor dit lab…
      </button>
    );
  }

  return (
    <div className="rounded-lg border border-border p-3">
      <div className="mb-2 flex items-start gap-2">
        <Terminal className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
        <div>
          <h3 className="text-sm font-semibold">Startscript van dit lab</h3>
          <p className="text-xs text-muted-foreground">
            Leeg = het startscript van het cluster. Vul je hier iets in, dan draait dát in
            dit lab — bijvoorbeeld de devcontainer van de repo in plaats van de losse
            diensten. Het geldt bij de volgende start.
          </p>
        </div>
      </div>
      <TextArea rows={8} className="font-mono text-xs" value={script ?? ""}
                onChange={(e) => setScript(e.target.value)}
                placeholder="leeg = volg het cluster" />
      <div className="mt-2 flex gap-2">
        <Button onClick={bewaar} disabled={bezig}>
          {bezig ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
          Bewaren
        </Button>
        <Button variant="ghost" onClick={() => setOpen(false)}>Dichtklappen</Button>
      </div>
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
 * Waarom een venster en geen knop die stil een bestand neerzet: je moet twee
 * dingen weten die nergens anders staan -- welk adres je daarna opent, en dat
 * de tunnel na een herstart weg is.
 *
 * De knop zelf staat sinds de opruiming in het menu van de branchregel: dit
 * is iets wat je één keer per branch doet, niet iets wat in beeld hoort te
 * staan terwijl je naar de status kijkt.
 */
function TunnelVenster(
  { lab, open, onClose }: {
    lab: { id: string; name: string; branch: string | null;
           poort_map?: Record<string, number> };
    open: boolean;
    onClose: () => void;
  },
) {
  const melding = useMelding();
  const [bezig, setBezig] = useState<TunnelOs | null>(null);

  const poorten = Object.keys(lab.poort_map || {}).map(Number);
  // Standaard ALLE poorten. Eén poort kiezen is de uitzondering, niet de
  // regel: een applicatie die vanuit de browser zijn eigen API aanspreekt
  // heeft er twee nodig, en met alleen de eerste laadt de pagina wel maar
  // blijft hij wit. Kiezen kan nog steeds -- twee branches tegelijk op 5173
  // kan immers niet.
  const [poort, setPoort] = useState<number | undefined>(undefined);

  async function haal(os: TunnelOs) {
    setBezig(os);
    try {
      const url = await clusterApi.branchTunnel(lab.id, os, poort);
      melding.ok("Tunnelscript gedownload",
                 `Start het en open daarna ${url}`);
      onClose();
    } catch (e) {
      melding.fout("Tunnel maken lukte niet",
                   e instanceof ApiError ? e.message : String(e));
    } finally {
      setBezig(null);
    }
  }

  return (
    <Modal open={open} onClose={onClose} klikBuitenSluit
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
            <Select value={poort ?? ""}
                    onChange={(e) => setPoort(e.target.value ? Number(e.target.value) : undefined)}>
              <option value="">alle poorten ({poorten.join(", ")})</option>
              {poorten.map((q) => (
                <option key={q} value={q}>
                  alleen {q} → wordt localhost:{q} op jouw machine
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

/**
 * De extra pakketten die elk branch-lab van dit cluster erbij krijgt.
 *
 * Git, bash, curl, jq en unzip komen in ELK lab, ongeacht het image — die
 * staan hier bewust niet tussen. Dit gaat over wat dít project extra nodig
 * heeft: node voor een npx-tool, build-essential voor een C-extensie.
 *
 * Twee bronnen: jij vinkt aan, en de verkenner mag voorstellen. Een voorstel
 * komt binnen als onbevestigd en staat pas hier zodra je het plan goedkeurt.
 */
function ExtrasKaart(
  { clusterId, gekozen, onWijzig }:
  { clusterId: number; gekozen: string[]; onWijzig: () => void },
) {
  const melding = useMelding();
  const [catalogus, setCatalogus] = useState<LabExtra[]>([]);
  const [keuze, setKeuze] = useState<string[]>(gekozen);
  const [bezig, setBezig] = useState(false);

  useEffect(() => setKeuze(gekozen), [gekozen]);
  useEffect(() => {
    labsApi.extras().then((rijen) => setCatalogus(rijen.filter((e) => e.is_enabled)))
      .catch(() => { /* geen catalogus is geen fout */ });
  }, []);

  const gewijzigd = keuze.slice().sort().join(",") !== gekozen.slice().sort().join(",");

  async function bewaar() {
    setBezig(true);
    try {
      await clusterApi.wijzig(clusterId, { lab_extras: keuze });
      melding.ok("Pakketten bijgewerkt",
                 "Branch-labs die hierna starten krijgen ze bij het inrichten.");
      onWijzig();
    } catch (e) {
      melding.fout("Opslaan lukte niet", e instanceof ApiError ? e.message : String(e));
    } finally {
      setBezig(false);
    }
  }

  return (
    <Card className="p-4">
      <div className="mb-3 flex items-start gap-2">
        <Package className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
        <div className="min-w-0">
          <h2 className="text-sm font-semibold">Extra pakketten</h2>
          <p className="text-xs text-muted-foreground">
            Elk branch-lab krijgt deze erbij bij het inrichten. Git, bash, curl, jq en
            unzip komen sowieso in elk lab — die hoef je hier niet te kiezen.
          </p>
        </div>
      </div>

      <div className="space-y-1 rounded-md border border-border p-2">
        {catalogus.length === 0 && (
          <p className="text-xs text-muted-foreground">Geen pakketten in de catalogus.</p>
        )}
        {catalogus.map((e) => (
          <label key={e.key} className="flex cursor-pointer items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-1"
              checked={keuze.includes(e.key)}
              onChange={() => setKeuze((o) =>
                o.includes(e.key) ? o.filter((x) => x !== e.key) : [...o, e.key])}
            />
            <span>
              {e.label}
              {e.description && (
                <span className="block text-xs text-muted-foreground">{e.description}</span>
              )}
            </span>
          </label>
        ))}
      </div>

      {gewijzigd && (
        <div className="mt-3 flex items-center gap-2">
          <Button onClick={() => void bewaar()} disabled={bezig}>
            {bezig ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                   : <Check className="h-3.5 w-3.5" />}
            Opslaan
          </Button>
          <Button variant="ghost" onClick={() => setKeuze(gekozen)}>Terugdraaien</Button>
          <span className="text-xs text-muted-foreground">
            Labs die al draaien houden wat ze hebben tot de volgende keer inrichten.
          </span>
        </div>
      )}
    </Card>
  );
}

/**
 * Op welke poorten de applicatie IN een branch-lab luistert.
 *
 * Dit is het verschil tussen een lab dat werkt en een lab dat draait maar
 * onbereikbaar is — en dat verschil is van buiten niet te zien. Daarom staat
 * hier uitleg en geen tabel.
 *
 * De verkenner haalt deze nummers uit de repo (meestal `EXPOSE` in een
 * Dockerfile). Dat gaat goed voor een backend en mis voor een PRODUCTIE-image:
 * `frontend/Dockerfile` van Slimmer-Politiek zegt `EXPOSE 80`, maar dat is de
 * nginx die in productie de SPA serveert — in een lab komt daar nooit iets.
 * LabX wachtte dan tot de tijd om was en noemde het mislukt, en het model
 * ging een tweede server op 80 zetten om eraan te voldoen.
 */
/** De repo van dit cluster — ook als die er nog niet is.
 *
 * Een cluster dat LEEG begon heeft geen URL: het eerste branch-lab kreeg een
 * verse `git init` en er is geen remote om naartoe te pushen. Zodra die er
 * wel is hoort hij hier, want vanaf dat moment hoort een volgend branch-lab
 * te KLONEN in plaats van opnieuw leeg te beginnen -- en zonder deze kaart
 * was daar geen weg naartoe behalve een nieuw cluster maken.
 */
function RepoKaart({ cluster, onWijzig }: { cluster: Cluster; onWijzig: () => void }) {
  const melding = useMelding();
  const [url, setUrl] = useState(cluster.repo_url || "");
  const [bezig, setBezig] = useState(false);

  async function bewaar() {
    setBezig(true);
    try {
      await clusterApi.wijzig(cluster.id, { repo_url: url.trim() } as Partial<Cluster>);
      melding.ok(url.trim() ? "Repo gekoppeld." : "Repo losgekoppeld.");
      onWijzig();
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Opslaan mislukte");
    } finally {
      setBezig(false);
    }
  }

  return (
    <Card className="p-4">
      <div className="mb-3 flex items-start gap-2">
        <GitBranch className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
        <div>
          <h2 className="text-sm font-semibold">Repo</h2>
          <p className="text-xs text-muted-foreground">
            {cluster.repo_url
              ? "Elk branch-lab kloont hieruit."
              : "Dit cluster begon leeg. Zet hier de URL zodra er een remote is — vanaf dan kloont een nieuw branch-lab in plaats van leeg te beginnen."}
          </p>
        </div>
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-0 flex-1">
          <Label>URL</Label>
          <Input value={url} onChange={(e) => setUrl(e.target.value)}
                 placeholder="https://github.com/jij/nieuw-project.git" />
        </div>
        <Button onClick={bewaar} disabled={bezig || url.trim() === (cluster.repo_url || "")}>
          {bezig ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
          Bewaren
        </Button>
      </div>
      {!cluster.repo_url && (
        <p className="mt-2 text-xs text-muted-foreground">
          Een nieuwe repo maak je bij je git-hoster; LabX doet dat niet voor je. Push vanuit
          het lab (<span className="font-mono">git remote add origin …</span>) en zet de URL
          daarna hier neer. Is hij privé, dan hoort er ook een deploy key of token bij —
          zie <span className="font-medium">Toegang &amp; geheimen</span>.
        </p>
      )}
    </Card>
  );
}

/** Op welke machine dit cluster draait.
 *
 * Als GEHEEL, en dat is de hele reden dat dit een eigen knop is en geen veld
 * dat je even wijzigt. Een cluster hangt aan één machine: daar staan de vaste
 * diensten met hun gegevens, daar bestaat het clusternetwerk, daar komen de
 * poorten naar buiten. Eén onderdeel achterlaten geeft geen half verhuisd
 * cluster maar een kapot cluster — een branch-lab dat de database niet meer
 * vindt, zonder dat ergens staat waarom.
 */
function MachineKaart({ cluster, hosts, onWijzig }: {
  cluster: Cluster;
  hosts: LabHost[];
  onWijzig: () => void;
}) {
  const melding = useMelding();
  const bevestig = useBevestiging();
  const [doel, setDoel] = useState("");
  const [bezig, setBezig] = useState(false);

  async function verhuis() {
    const naam = doel ? (hosts.find((h) => String(h.id) === doel)?.naam || doel) : "deze server";
    const ja = await bevestig.vraag({
      titel: `Het hele cluster naar ${naam}?`,
      tekst: "De vaste diensten gaan uit, hun gegevens verhuizen mee, en alle branch-labs "
        + "worden daarna opnieuw opgebouwd. Dat duurt een paar minuten en kan niet "
        + "halverwege terug.",
      bevestig: "Verplaatsen",
    });
    if (!ja) return;
    setBezig(true);
    try {
      await clusterApi.verplaats(cluster.id, doel ? Number(doel) : null);
      melding.ok(`Cluster staat nu op ${naam}.`);
      setDoel("");
      onWijzig();
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Verplaatsen mislukte");
    } finally {
      setBezig(false);
    }
  }

  return (
    <Card className="p-4">
      <div className="mb-3 flex items-start gap-2">
        <Server className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
        <div>
          <h2 className="text-sm font-semibold">Machine</h2>
          <p className="text-xs text-muted-foreground">
            Dit cluster draait in zijn geheel op <span className="font-medium">{cluster.machine}</span>.
            Verplaatsen neemt de vaste diensten, hun gegevens en alle branch-labs mee.
          </p>
        </div>
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-0 flex-1">
          <Label>Verplaatsen naar</Label>
          <Select value={doel} onChange={(e) => setDoel(e.target.value)}>
            <option value="">deze server</option>
            {hosts.map((h) => <option key={h.id} value={h.id}>{h.naam}</option>)}
          </Select>
        </div>
        <Button onClick={verhuis} disabled={bezig}>
          {bezig ? <Loader2 className="h-4 w-4 animate-spin" /> : <Server className="h-4 w-4" />}
          {bezig ? "Bezig met verhuizen…" : "Verplaatsen"}
        </Button>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        Kan niet terwijl er een agent werkt: dat zou de werkkopie onder zijn handen
        weghalen. Laat lopend werk eerst aflopen.
      </p>
    </Card>
  );
}

/** Het model en het beurtenplafond voor élk lab van dit cluster.
 *
 * Per CLUSTER en niet per lab, omdat het bij het WERK hoort en niet bij een
 * wegwerpcontainer: zet je Opus op dit cluster, dan hoort elke branch
 * eronder op Opus te draaien zonder dat je dat per lab herhaalt. Eén vergeten
 * lab is genoeg om een run op een ander model te laten lopen dan je dacht —
 * vandaar dat wijzigen hier ook de BESTAANDE labs omzet.
 *
 * Het beurtenplafond staat erbij omdat clusterwerk anders is dan rondkijken.
 * Gemeten op dit cluster: een ticket in een monorepo liep stuk op
 * `error_max_turns` bij de standaard van 250 — honderd shell-stappen alleen
 * om de code te vinden, en dan moest het bouwen nog beginnen.
 */
function AgentKaart({ cluster, onWijzig }: { cluster: Cluster; onWijzig: () => void }) {
  const melding = useMelding();
  const [model, setModel] = useState(cluster.lab_model || "");
  const [turns, setTurns] = useState(String(cluster.lab_max_turns || ""));
  const [bezig, setBezig] = useState(false);

  async function bewaar() {
    setBezig(true);
    try {
      await clusterApi.wijzig(cluster.id, {
        lab_model: model || null,
        lab_max_turns: turns.trim() ? Number(turns) : null,
      } as Partial<Cluster>);
      melding.ok("Opgeslagen. Dit geldt ook voor de labs die er al staan.");
      onWijzig();
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Opslaan mislukte");
    } finally {
      setBezig(false);
    }
  }

  const gewijzigd = model !== (cluster.lab_model || "")
    || turns.trim() !== String(cluster.lab_max_turns || "");

  return (
    <Card className="p-4">
      <div className="mb-3 flex items-start gap-2">
        <Bot className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
        <div>
          <h2 className="text-sm font-semibold">De agent in dit cluster</h2>
          <p className="text-xs text-muted-foreground">
            Geldt voor élk lab van dit cluster, ook de labs die er al staan. Een run die
            nu loopt houdt wat hij had. In een cluster geldt standaard géén beurtenplafond;
            een gewoon lab volgt onveranderd de instellingen.
          </p>
        </div>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <Label>Model</Label>
          <Select value={model} onChange={(e) => setModel(e.target.value)}>
            {MODEL_OPTIONS.map((m) => (
              <option key={m.value} value={m.value}>{m.label}</option>
            ))}
          </Select>
          <p className="mt-1 text-xs text-muted-foreground">
            Leeg = het model uit Instellingen.
          </p>
        </div>
        <div>
          <Label>Beurten per run</Label>
          <Input value={turns} onChange={(e) => setTurns(e.target.value.replace(/[^0-9]/g, ""))}
                 placeholder="geen limiet" inputMode="numeric" />
          <p className="mt-1 text-xs text-muted-foreground">
            {turns.trim()
              ? <>Harde grens: de run stopt na {turns} beurten, ook als hij nog bezig was.</>
              : <>Leeg = <span className="font-medium">geen limiet</span>. Een beurt is geen maat
                 voor werk — het zoeken naar de juiste code in een monorepo kost er al
                 tientallen. De run blijft begrensd door de tijdslimiet en het kostenplafond
                 uit Instellingen.</>}
          </p>
        </div>
      </div>
      <div className="mt-3">
        <Button onClick={bewaar} disabled={bezig || !gewijzigd}>
          {bezig ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
          Bewaren
        </Button>
      </div>
    </Card>
  );
}

/** Of een nieuw branch-lab de guards aan krijgt.
 *
 * Standaard allebei aan — dat hoort de veilige kant te zijn. Maar een cluster
 * waarvan de repo's geen klantdata bevatten betaalt er elke tool-aanroep
 * voor, en dan remt hij zonder dat er iets te remmen valt.
 *
 * Bewust een CLUSTER-instelling. Je weet dit van de repo, niet van een lab,
 * en per lab uitzetten betekent dat je het bij elk nieuw branch-lab opnieuw
 * moet doen — en één keer vergeet.
 */
function GuardKaart({ cluster, onWijzig }: { cluster: Cluster; onWijzig: () => void }) {
  const melding = useMelding();
  const bevestig = useBevestiging();

  async function zet(veld: "lab_data_guard" | "lab_llm_guard", waarde: boolean) {
    if (!waarde && veld === "lab_data_guard") {
      const ja = await bevestig.vraag({
        titel: "De data-guard uitzetten voor nieuwe labs?",
        tekst: "Doe dit alleen als je zeker weet dat er geen klantdata in deze repo's zit. "
          + "Zonder guard kan een agent gegevens naar buiten sturen zonder dat iets meekijkt.",
        variant: "danger",
        bevestig: "Uitzetten",
      });
      if (!ja) return;
    }
    try {
      await clusterApi.wijzig(cluster.id, { [veld]: waarde } as Partial<Cluster>);
      melding.ok("Opgeslagen. Dit geldt voor branch-labs die je hierna aanmaakt.");
      onWijzig();
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Opslaan mislukte");
    }
  }

  const dataAan = cluster.lab_data_guard !== false;
  const llmAan = cluster.lab_llm_guard !== false;

  return (
    <Card className="p-4">
      <div className="mb-3 flex items-start gap-2">
        <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
        <div>
          <h2 className="text-sm font-semibold">Guards voor nieuwe branch-labs</h2>
          <p className="text-xs text-muted-foreground">
            Geldt voor labs die je hierna aanmaakt. Labs die er al staan houden hun eigen
            stand — die zet je om bij het lab zelf, en dat werkt meteen.
          </p>
        </div>
      </div>
      <div className="space-y-3">
        <div>
          <Toggle checked={dataAan} onChange={(v) => zet("lab_data_guard", v)}
                  label="Data-egress-guard (regels)" />
          <p className="mt-1 text-xs text-muted-foreground">
            Kijkt bij elke tool-aanroep mee of er gegevens naar buiten gaan. Uit is sneller,
            en alleen verantwoord als er geen klantdata in deze repo's zit.
          </p>
        </div>
        <div>
          <Toggle checked={llmAan} onChange={(v) => zet("lab_llm_guard", v)}
                  label="Lokaal model als extra check" />
          <p className="mt-1 text-xs text-muted-foreground">
            Een tweede oordeel van een klein model bovenop de regels. Dit is het duurste
            deel: elke aanroep wacht erop.
          </p>
        </div>
      </div>
      {!dataAan && !llmAan && (
        <p className="mt-3 flex items-start gap-1.5 rounded-lg border border-amber-500/40 bg-amber-500/5 p-2 text-xs">
          <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-500" />
          Nieuwe branch-labs van dit cluster draaien zonder enige controle op wat er naar
          buiten gaat.
        </p>
      )}
    </Card>
  );
}

function PoortenKaart({ cluster, onWijzig }: { cluster: Cluster; onWijzig: () => void }) {
  const melding = useMelding();
  const [poorten, setPoorten] = useState<number[]>(cluster.lab_poorten || []);
  const [nieuw, setNieuw] = useState("");
  const [bezig, setBezig] = useState(false);

  useEffect(() => setPoorten(cluster.lab_poorten || []), [cluster.lab_poorten]);

  const gewijzigd = poorten.join(",") !== (cluster.lab_poorten || []).join(",");

  function voegToe() {
    const n = Number(nieuw.trim());
    if (!Number.isInteger(n) || n < 1 || n > 65535) {
      melding.fout("Dat is geen poort", "Een poortnummer ligt tussen 1 en 65535.");
      return;
    }
    if (poorten.includes(n)) { setNieuw(""); return; }
    setPoorten((o) => [...o, n]);
    setNieuw("");
  }

  async function bewaar() {
    setBezig(true);
    try {
      await clusterApi.wijzig(cluster.id, { lab_poorten: poorten });
      melding.ok("Poorten opgeslagen",
                 "Branch-labs die hierna starten publiceren deze poorten.");
      onWijzig();
    } catch (e) {
      melding.fout("Opslaan lukte niet", e instanceof ApiError ? e.message : String(e));
    } finally {
      setBezig(false);
    }
  }

  return (
    <Card className="p-4">
      <div className="mb-3 flex items-start gap-2">
        <Network className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
        <div className="min-w-0">
          <h2 className="text-sm font-semibold">Waarop de applicatie luistert</h2>
          <p className="text-xs text-muted-foreground">
            Poortnummers <em>binnen</em> de container. LabX publiceert ze naar buiten op
            een vrij nummer vanaf 18000, en wacht bij het starten tot ze allemaal
            antwoorden. Staat hier een nummer waar nooit iets komt, dan blijft het lab
            op &quot;mislukt&quot; staan terwijl de applicatie prima draait.
          </p>
        </div>
      </div>

      {poorten.length === 0 ? (
        <EmptyState>
          Nog geen poorten. Zonder deze gokt LabX op 3000 en 7071 — vul in waarop jouw
          applicatie echt luistert.
        </EmptyState>
      ) : (
        <ul className="space-y-2">
          {poorten.map((poort, i) => (
            <li key={poort} className="flex flex-wrap items-center gap-2 rounded-md border p-2">
              <span className="font-mono text-sm">{poort}</span>
              {i === 0 && <Badge tone="green">hoofdpoort</Badge>}
              <span className="min-w-0 flex-1 text-xs text-muted-foreground">
                {i === 0
                  ? "Hier wijst de knop \u201eOpenen\u201d naartoe, en hierop bouwt LabX het adres van het lab."
                  : "Wordt ook gepubliceerd; het starten slaagt pas als ook hier iets antwoordt."}
              </span>
              <Button variant="ghost" onClick={() => setPoorten((o) => o.filter((x) => x !== poort))}>
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Input
          className="w-32 font-mono text-sm"
          value={nieuw}
          placeholder="8000"
          onChange={(e) => setNieuw(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") voegToe(); }}
        />
        <Button variant="secondary" onClick={voegToe} disabled={!nieuw.trim()}>
          <Plus className="h-3.5 w-3.5" /> Poort toevoegen
        </Button>
        {gewijzigd && (
          <>
            <Button onClick={() => void bewaar()} disabled={bezig}>
              {bezig ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                     : <Check className="h-3.5 w-3.5" />}
              Opslaan
            </Button>
            <Button variant="ghost" onClick={() => setPoorten(cluster.lab_poorten || [])}>
              Terugdraaien
            </Button>
          </>
        )}
      </div>

      <p className="mt-2 text-xs text-muted-foreground">
        De verkenner haalt deze nummers meestal uit <code>EXPOSE</code> in een Dockerfile.
        Let op een <strong>productie</strong>-image: een <code>EXPOSE 80</code> in
        bijvoorbeeld <code>frontend/Dockerfile</code> is de nginx die later de gebouwde
        site serveert — in een lab komt daar nooit iets. Haal zo&apos;n nummer hier weg.
      </p>
    </Card>
  );
}
