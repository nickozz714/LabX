/**
 * pages/ClustersPage.tsx — repo's met branch-labs.
 *
 * Een cluster is een groep labs die bij elkaar horen omdat ze dezelfde
 * applicatie draaien: één vaste laag die blijft staan (de emulator, de
 * database) en branch-labs die komen en gaan.
 *
 * Het beginpunt is hier met opzet een REPO-URL en geen formulier met velden.
 * Een cluster inrichten door image, poorten, startscript en diensten in te
 * typen werkt precies één keer — voor het cluster dat je met de hand opbouwt.
 * Daarom begint dit scherm met "verkennen": LabX leest de repo en stelt voor
 * hoe het cluster eruit hoort te zien, en jij kijkt dat na.
 *
 * Verkennen maakt niets aan. Het kloont ondiep in een wegwerpcontainer en
 * gooit die weg. Pas als je het voorstel goedkeurt bestaat er een cluster.
 */
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  Boxes, GitBranch, KeyRound, Loader2, Play, Search, Server, Trash2, TriangleAlert,
} from "lucide-react";
import { clusterApi } from "@/lib/clusters";
import type { Cluster } from "@/lib/clusters";
import { labHostsApi } from "@/lib/labs";
import { secretApi } from "@/lib/secrets";
import type { SecretDto } from "@/lib/secrets";
import type { LabHost } from "@/lib/types";
import {
  Badge, Button, Card, EmptyState, Input, Label, Modal, Select, TextArea,
} from "@/components/ui";
import { useMelding } from "@/components/Meldingen";
import { useBevestiging } from "@/components/Bevestiging";
import { ApiError } from "@/lib/api";

function statusTint(s: Cluster["status"]) {
  return s === "draait" ? "green" : s === "fout" ? "red" : "neutral";
}

export function ClustersPage() {
  const navigate = useNavigate();
  const melding = useMelding();
  const bevestig = useBevestiging();

  const [clusters, setClusters] = useState<Cluster[]>([]);
  const [bezig, setBezig] = useState(true);
  const [open, setOpen] = useState(false);
  const [verkent, setVerkent] = useState(false);

  const [repoUrl, setRepoUrl] = useState("");
  const [branch, setBranch] = useState("main");
  const [tokenGeheim, setTokenGeheim] = useState("");
  const [hostId, setHostId] = useState("");
  const [instructie, setInstructie] = useState("");
  const [hosts, setHosts] = useState<LabHost[]>([]);
  const [geheimen, setGeheimen] = useState<SecretDto[]>([]);

  const laad = useCallback(async () => {
    setBezig(true);
    try {
      setClusters(await clusterApi.lijst());
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Clusters niet op te halen");
    } finally {
      setBezig(false);
    }
  }, [melding]);

  useEffect(() => { void laad(); }, [laad]);
  useEffect(() => {
    labHostsApi.list().then((h) => setHosts(h.filter((x) => x.status === "gekoppeld"))).catch(() => {});
    secretApi.list().then(setGeheimen).catch(() => {});
  }, []);

  async function verken() {
    if (!repoUrl.trim()) return;
    setVerkent(true);
    try {
      const plan = await clusterApi.verkennen({
        repo_url: repoUrl.trim(),
        branch: branch.trim() || "main",
        token_geheim: tokenGeheim || null,
        host_id: hostId ? Number(hostId) : null,
        instructie: instructie.trim() || undefined,
      });
      setOpen(false);
      navigate(`/clusters/plannen/${plan.id}`);
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Verkennen mislukte");
    } finally {
      setVerkent(false);
    }
  }

  async function weg(c: Cluster) {
    const ja = await bevestig.vraag({
      titel: `Cluster '${c.naam}' verwijderen?`,
      tekst: c.labs.length
        // De backend weigert dit: branch-labs zonder hun vaste diensten zijn
        // kapot zonder dat je het ziet. Het hier alvast zeggen scheelt een
        // klik die op een foutmelding uitloopt.
        ? `Er ${c.labs.length === 1 ? "hangt nog 1 branch-lab" : `hangen nog ${c.labs.length} branch-labs`} aan dit cluster. ` +
          "Ruim die eerst op — anders blijven ze achter zonder vaste diensten om mee te praten."
        : "De containers van de vaste diensten en het clusternetwerk worden ook verwijderd.",
      variant: "danger",
      bevestig: "Verwijderen",
    });
    if (!ja) return;
    try {
      const uit = await clusterApi.verwijder(c.id);
      if (uit.niet_opgeruimd?.length) {
        melding.info(`Cluster verwijderd, maar dit bleef staan: ${uit.niet_opgeruimd.join("; ")}`);
      } else {
        melding.ok("Cluster verwijderd");
      }
      void laad();
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Verwijderen mislukte");
    }
  }

  return (
    <div className="mx-auto w-full max-w-5xl space-y-4 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">Clusters</h1>
          <p className="text-sm text-muted-foreground">
            Een repo met een vaste laag eronder en een lab per branch erboven.
          </p>
        </div>
        <Button onClick={() => setOpen(true)}>
          <Search className="h-4 w-4" /> Repo verkennen
        </Button>
      </div>

      {bezig && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> Ophalen…
        </div>
      )}

      {!bezig && clusters.length === 0 && (
        <EmptyState>
          Nog geen clusters. Geef een repo-URL op en LabX stelt voor hoe het cluster eruit
          hoort te zien — welke vaste diensten erbij horen, waarin een branch-lab draait,
          en wat er uit de kluis moet komen. Er wordt niets aangemaakt tot je dat voorstel
          goedkeurt.
        </EmptyState>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        {clusters.map((c) => (
          <Card key={c.id} className="p-4">
            <div className="flex items-start justify-between gap-2">
              <Link to={`/clusters/${c.id}`} className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-semibold">{c.naam}</span>
                  <Badge tone={statusTint(c.status)}>{c.status}</Badge>
                </div>
                {c.repo_url && (
                  <p className="mt-1 truncate font-mono text-xs text-muted-foreground">{c.repo_url}</p>
                )}
              </Link>
              <Button variant="ghost" onClick={() => weg(c)} aria-label="Verwijderen">
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>

            <dl className="mt-3 grid grid-cols-2 gap-2 text-xs text-muted-foreground">
              <div className="flex items-center gap-1.5">
                <Server className="h-3.5 w-3.5" /> {c.machine}
              </div>
              <div className="flex items-center gap-1.5">
                <Boxes className="h-3.5 w-3.5" />
                {c.diensten.filter((d) => d.draait).length}/{c.diensten.length} vaste diensten
              </div>
              <div className="flex items-center gap-1.5">
                <GitBranch className="h-3.5 w-3.5" /> {c.labs.length} branch-labs
              </div>
              {c.geheimen_ontbreken.length > 0 && (
                <div className="flex items-center gap-1.5 text-amber-400">
                  <KeyRound className="h-3.5 w-3.5" /> {c.geheimen_ontbreken.length} geheim
                  {c.geheimen_ontbreken.length === 1 ? "" : "en"} open
                </div>
              )}
            </dl>

            {c.fout && (
              <p className="mt-2 flex items-start gap-1.5 text-xs text-destructive">
                <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {c.fout}
              </p>
            )}
          </Card>
        ))}
      </div>

      <Modal open={open} onClose={() => setOpen(false)} title="Repo verkennen">
        <p className="text-sm text-muted-foreground">
          LabX kloont deze repo ondiep in een wegwerpcontainer, leest hem, en stelt een
          cluster voor. Er wordt niets uit de repo uitgevoerd en er wordt niets aangemaakt —
          je krijgt eerst het voorstel te zien.
        </p>

        <div className="mt-4 space-y-3">
          <div>
            <Label>Repo-URL</Label>
            <Input
              value={repoUrl}
              onChange={(e) => setRepoUrl(e.target.value)}
              placeholder="https://github.com/org/repo.git"
              autoFocus
            />
            <p className="mt-1 text-xs text-muted-foreground">
              Een SSH-adres (<span className="font-mono">git@github.com:org/repo.git</span>) mag ook —
              dat wordt omgezet naar HTTPS, want zo kloont LabX met een token uit de kluis.
            </p>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <Label>Branch</Label>
              <Input value={branch} onChange={(e) => setBranch(e.target.value)} placeholder="main" />
            </div>
            <div>
              <Label>Machine</Label>
              <Select value={hostId} onChange={(e) => setHostId(e.target.value)}>
                <option value="">deze server</option>
                {hosts.map((h) => <option key={h.id} value={h.id}>{h.naam}</option>)}
              </Select>
            </div>
          </div>
          <div>
            <Label>Toegangstoken (uit de kluis)</Label>
            <Select value={tokenGeheim} onChange={(e) => setTokenGeheim(e.target.value)}>
              <option value="">geen — alleen publieke repo's</option>
              {geheimen.map((g) => <option key={g.name} value={g.name}>{g.name}</option>)}
            </Select>
            <p className="mt-1 text-xs text-muted-foreground">
              Het cluster onthoudt alleen de NAAM van het geheim. De waarde blijft in de kluis.
            </p>
          </div>
        </div>

        <div className="mt-3">
          {/* Vooraf iets kunnen zeggen scheelt een hele ronde: zonder dit kun
              je pas corrigeren als er al een plan ligt, en dat kost een minuut
              plus een modelaanroep. */}
          <Label>Instructies vooraf (optioneel)</Label>
          <TextArea
            rows={3}
            value={instructie}
            onChange={(e) => setInstructie(e.target.value)}
            placeholder="Bijvoorbeeld: de agent in components/agent hoort er niet bij, en gebruik node 22 in plaats van de devcontainer."
          />
          <p className="mt-1 text-xs text-muted-foreground">
            Wat je hier zet weegt zwaarder dan wat de repo suggereert. Je kunt achteraf
            nog bijsturen, maar dat kost een nieuwe verkenning.
          </p>
        </div>

        <div className="mt-5 flex items-center gap-2">
          <Button onClick={verken} disabled={verkent || !repoUrl.trim()}>
            {verkent ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            {verkent ? "Bezig met lezen…" : "Verkennen"}
          </Button>
          <Button variant="secondary" onClick={() => setOpen(false)}>Annuleren</Button>
        </div>
        {verkent && (
          <p className="mt-2 text-xs text-muted-foreground">
            Dit duurt een halve tot anderhalve minuut: klonen, lezen, en het model laten
            meekijken.
          </p>
        )}
      </Modal>
    </div>
  );
}
