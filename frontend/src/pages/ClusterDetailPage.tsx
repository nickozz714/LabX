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
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft, Box, Check, Copy, ExternalLink, GitBranch, KeyRound, Loader2,
  Play, PlayCircle, Plus, RefreshCw, Search, Server, Square, Terminal, Trash2,
  TriangleAlert,
} from "lucide-react";
import { clusterApi } from "@/lib/clusters";
import type {
  BenodigdGeheim, Cluster, ClusterDienst, ClusterGeheim, ClusterPlan, Toegang,
} from "@/lib/clusters";
import {
  Badge, Button, Card, EmptyState, Input, Label, Modal, TextArea,
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

      {adres && (
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <a href={adres} target="_blank" rel="noreferrer"
             className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 font-mono text-xs hover:bg-secondary">
            <ExternalLink size={12} /> {adres}
          </a>
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
        {c.proef_status === "wacht_op_fiat" ? (
          <>
            <Button onClick={() => doe(clusterApi.proefBevestigen(c.id), "Cluster klaar")}
                    disabled={bezig}>
              <Check className="h-4 w-4" /> Dit werkt — cluster is klaar
            </Button>
            <Button variant="secondary" onClick={() => setAfkeurOpen(true)} disabled={bezig}>
              Nog niet goed
            </Button>
          </>
        ) : (
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
  const regels = [
    ...nodig.map((n) => ({ naam: n.naam, waarvoor: n.waarvoor, gevonden: n.gevonden,
                           eigen: n.in_eigen_kluis, verplicht: true })),
    ...los.map((g) => ({ naam: g.naam, waarvoor: g.omschrijving || "", gevonden: true,
                         eigen: true, verplicht: false })),
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

      {regels.length === 0 ? (
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
                  : r.gevonden
                    ? <Badge tone="neutral">uit de gedeelde kluis</Badge>
                    : <Badge tone="red">ontbreekt</Badge>}
                {!r.verplicht && <span className="text-xs text-muted-foreground">extra</span>}
              </div>
              {r.waarvoor && (
                <p className="mt-0.5 text-xs text-muted-foreground">{r.waarvoor}</p>
              )}
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
            </li>
          ))}
        </ul>
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

      {/* ── de proefbouw ────────────────────────────────────────────────── */}
      <ProefKaart c={c} onWijziging={laad} />

      {/* ── toegang en geheimen ─────────────────────────────────────────── */}
      <SleutelKaart c={c} onWijziging={laad} />

      {/* ── de kluis ────────────────────────────────────────────────────── */}
      <KluisKaart clusterId={c.id} benodigd={c.benodigde_geheimen || {}} />

      {/* ── de vaste laag ────────────────────────────────────────────────── */}
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
                <li key={l.id} className="flex items-center justify-between rounded-lg border border-border p-3">
                  <Link to={`/labs/${l.id}`} className="min-w-0">
                    <span className="font-medium">{l.name}</span>
                    {l.branch && <span className="ml-2 font-mono text-xs text-muted-foreground">{l.branch}</span>}
                  </Link>
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
