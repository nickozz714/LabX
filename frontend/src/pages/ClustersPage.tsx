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
import { labHostsApi, labsApi } from "@/lib/labs";
import { secretApi } from "@/lib/secrets";
import type { SecretDto } from "@/lib/secrets";
import type { LabExtra, LabHost } from "@/lib/types";
import {
  Badge, Button, Card, EmptyState, Input, Label, Modal, Select, TextArea,
} from "@/components/ui";
import { useMelding } from "@/components/Meldingen";
import { useConcept } from "@/lib/useConcept";
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

  // Een verkenning begin je vaak met een URL die je ergens vandaan moet
  // halen en een opdracht die je formuleert. Dat twee keer typen omdat je
  // sessie verliep, is de meest overbodige vorm van werk die er is.
  const [repoUrl, setRepoUrl, vergeetRepo] = useConcept<string>("verkennen:repo", "");
  const [branch, setBranch, vergeetBranch] = useConcept<string>("verkennen:branch", "main");
  const [tokenGeheim, setTokenGeheim] = useState("");
  const [hostId, setHostId] = useState("");
  const [instructie, setInstructie, vergeetInstructie] =
    useConcept<string>("verkennen:instructie", "");
  // Env-bestanden en extra pakketten gaan BEWUST niet door useConcept: de
  // inhoud van een .env hoort niet in de opslag van een browser te blijven
  // hangen nadat je het venster sluit.
  const [envBestanden, setEnvBestanden] = useState<{ pad: string; inhoud: string }[]>([]);
  const [extras, setExtras] = useState<string[]>([]);
  const [catalogus, setCatalogus] = useState<LabExtra[]>([]);

  useEffect(() => {
    labsApi.extras().then((rijen) => setCatalogus(rijen.filter((e) => e.is_enabled)))
      .catch(() => { /* geen catalogus is geen fout */ });
  }, []);

  async function kiesEnv(bestanden: FileList) {
    const nieuw: { pad: string; inhoud: string }[] = [];
    for (const f of Array.from(bestanden).slice(0, 20)) {
      // `webkitRelativePath` is gevuld als je een MAP koos; dan weten we het
      // pad echt. Kies je losse bestanden, dan geeft de browser alleen de
      // naam — en in een monorepo heten ze allemaal `.env`. Die gok is dus
      // een startpunt, geen antwoord; daarom staat het veld ernaast en
      // waarschuwt het scherm bij dubbele paden.
      const relatief = (f as File & { webkitRelativePath?: string }).webkitRelativePath || "";
      // De eerste map van een mapkeuze is de map zelf; die hoort er niet bij.
      const pad = relatief ? relatief.split("/").slice(1).join("/") || f.name : f.name;
      nieuw.push({ pad, inhoud: await f.text() });
    }
    setEnvBestanden((o) => [...o, ...nieuw]);
  }

  // Twee bestanden op hetzelfde pad betekent dat er een stil verdwijnt: de
  // server bewaart er één per pad. In een monorepo gebeurt dat meteen, want
  // daar heet elk bestand `.env`.
  const dubbelePaden = new Set(
    envBestanden.map((b) => b.pad.trim())
      .filter((pad, i, alle) => pad && alle.indexOf(pad) !== i),
  );
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
        env_bestanden: envBestanden.length ? envBestanden : undefined,
        lab_extras: extras.length ? extras : undefined,
      });
      vergeetRepo();
      vergeetBranch();
      vergeetInstructie();
      setEnvBestanden([]);
      setExtras([]);
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

          {/* Env-bestanden NU al meegeven. Geef je ze pas na goedkeuring, dan
              moet het model raden naar sleutels die het had kunnen weten — en
              dan verzint het startscript een naam waar jij er al een had. */}
          <Label>Env-bestanden (optioneel)</Label>
          <input
            type="file"
            multiple
            className="text-xs"
            onChange={(e) => { if (e.target.files?.length) void kiesEnv(e.target.files); }}
          />
          {envBestanden.length > 0 && (
            <ul className="mt-2 space-y-1">
              {envBestanden.map((b, i) => (
                <li key={i} className="flex items-center gap-2">
                  <Input
                    className={`flex-1 font-mono text-xs ${
                      dubbelePaden.has(b.pad.trim()) ? "border-red-500" : ""}`}
                    value={b.pad}
                    onChange={(e) => setEnvBestanden((o) =>
                      o.map((x, j) => (j === i ? { ...x, pad: e.target.value } : x)))}
                  />
                  <Badge tone="neutral">
                    {(b.inhoud.match(/^\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*\s*=/gm) || []).length} sleutels
                  </Badge>
                  <Button variant="ghost"
                          onClick={() => setEnvBestanden((o) => o.filter((_, j) => j !== i))}>
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </li>
              ))}
            </ul>
          )}
          {dubbelePaden.size > 0 && (
            <p className="mt-1 text-xs text-red-400">
              Twee bestanden op hetzelfde pad: er blijft er één over. Geef ze allebei
              hun eigen plek.
            </p>
          )}
          <p className="mt-1 text-xs text-muted-foreground">
            Het pad is waar het bestand in de repo hoort, gerekend vanaf de wortel.
            In een monorepo heet elk bestand <code>.env</code>, en de browser geeft
            alleen die naam door — zet er dus zelf de map bij:
            <code> components/api/.env</code> en <code> components/app/.env</code>.
            Kies je een hele MAP in plaats van losse bestanden, dan weet LabX het pad
            wel en vult hij het voor je in.
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            Het model ziet alleen de NAMEN van de sleutels, nooit de waarden, en weet
            dan dat het er niet om hoeft te vragen. Vindt het toch een sleutel die niet
            in jouw bestanden staat, dan zegt het plan erbij dat die wél in de code
            voorkomt maar niet in wat je gaf.
          </p>

          {/* Pakketten die elk branch-lab erbij krijgt. Git, curl, jq en unzip
              komen sowieso in elk lab; die staan hier bewust niet tussen. */}
          <Label>Extra pakketten (optioneel)</Label>
          <div className="space-y-1 rounded-md border border-border p-2">
            {catalogus.length === 0 && (
              <p className="text-xs text-muted-foreground">Geen pakketten in de catalogus.</p>
            )}
            {catalogus.map((e) => (
              <label key={e.key} className="flex cursor-pointer items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={extras.includes(e.key)}
                  onChange={() => setExtras((o) =>
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
          <p className="mt-1 text-xs text-muted-foreground">
            Git, bash, curl, jq en unzip komen in elk lab, ongeacht het image — die hoef
            je hier niet te kiezen. De verkenner mag zelf ook pakketten voorstellen; die
            zie je dan in het plan staan.
          </p>
        </div>

        <div className="mt-5 flex items-center gap-2">
          <Button onClick={verken} disabled={verkent || !repoUrl.trim() || dubbelePaden.size > 0}>
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
