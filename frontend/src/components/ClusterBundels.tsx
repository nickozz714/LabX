/**
 * ClusterBundels.tsx — bestanden van buiten naar een cluster brengen.
 *
 * Een vaste dienst begint leeg, en de bron waaruit hij gevuld hoort te worden
 * is niet altijd bereikbaar: de Cosmos van IntelligentHive zit achter een
 * accountfirewall, dus `sync.sh` komt er niet bij. De export staat dan gewoon
 * op je schijf — en tot nu toe was er geen weg om die in het cluster te
 * krijgen, want de bestandsbrowser hoort bij een LAB en een vaste dienst is
 * geen lab.
 *
 * DRIE KEUZES DIE DIT SCHERM VERKLAREN:
 *
 * - **Archief plus opdracht, niet archief plus script.** Je zegt wat je wilt
 *   ("zet deze dump in de Cosmos-emulator"); het model schrijft het script.
 *   Wat erin zit verschilt per export, en jij hoort geen loader te schrijven.
 *
 * - **Het script komt terug in beeld.** Je ziet wat er gedraaid heeft en wat
 *   het zei. Ging het goed, dan draai je het de volgende keer ongewijzigd —
 *   zonder dat een model er opnieuw naar raadt.
 *
 * - **De inhoud gaat nooit naar het model.** Het ziet de bestandsnamen en
 *   hun omvang; de inhoud leest het script zelf, in de container. Een dump
 *   van een klant hoort niet in de context van een taalmodel.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  FileArchive, Loader2, Play, Trash2, Upload,
} from "lucide-react";
import { ApiError } from "@/lib/api";
import { clusterApi } from "@/lib/clusters";
import type { BundelUitslag, Cluster, ClusterBundel } from "@/lib/clusters";
import {
  Badge, Button, Card, EmptyState, Input, Label, Select, TextArea,
} from "@/components/ui";
import { useMelding } from "@/components/Meldingen";
import { useBevestiging } from "@/components/Bevestiging";

export function ClusterBundels({ cluster }: { cluster: Cluster }) {
  const melding = useMelding();
  const bevestig = useBevestiging();

  const [bundels, setBundels] = useState<ClusterBundel[]>([]);
  const [laadt, setLaadt] = useState(true);
  const [uploadt, setUploadt] = useState(false);
  const [naam, setNaam] = useState("");
  const [instructie, setInstructie] = useState("");
  const [bestand, setBestand] = useState<File | null>(null);
  const kiezer = useRef<HTMLInputElement | null>(null);

  const laad = useCallback(async () => {
    try {
      setBundels(await clusterApi.bundels(cluster.id));
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Bundels niet op te halen");
    } finally {
      setLaadt(false);
    }
  }, [cluster.id, melding]);

  useEffect(() => { void laad(); }, [laad]);

  async function upload() {
    if (!bestand) return;
    setUploadt(true);
    try {
      const b = await clusterApi.bundelUploaden(cluster.id, bestand, naam, instructie);
      melding.ok(`'${b.naam}' staat klaar — ${b.aantal_bestanden} bestanden, ${b.omvang}.`);
      setBundels((xs) => [b, ...xs]);
      setBestand(null);
      setNaam("");
      setInstructie("");
      if (kiezer.current) kiezer.current.value = "";
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Uploaden mislukte");
    } finally {
      setUploadt(false);
    }
  }

  async function weg(b: ClusterBundel) {
    const ja = await bevestig.vraag({
      titel: `Bundel '${b.naam}' weggooien?`,
      tekst: "De bestanden verdwijnen uit het cluster. Wat er al mee geladen is, blijft staan.",
      variant: "danger",
      bevestig: "Weggooien",
    });
    if (!ja) return;
    await clusterApi.bundelWeg(b.id);
    setBundels((xs) => xs.filter((x) => x.id !== b.id));
  }

  if (laadt) {
    return (
      <p className="flex items-center gap-2 py-2 text-xs text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Bundels ophalen…
      </p>
    );
  }

  return (
    <Card className="p-4">
      <div className="mb-3 flex items-start gap-2">
        <FileArchive className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
        <div>
          <h2 className="text-sm font-semibold">Bestanden van buiten</h2>
          <p className="text-xs text-muted-foreground">
            Een zip of tar met bijvoorbeeld een database-export, plus één zin over wat ermee
            moet. Het model schrijft het script en draait het tegen de dienst die je kiest.
          </p>
        </div>
      </div>

      <div className="space-y-3 rounded-lg border border-border p-3">
        <div>
          <Label>Archief</Label>
          <input
            ref={kiezer}
            type="file"
            accept=".zip,.tar,.tar.gz,.tgz"
            onChange={(e) => {
              const f = e.target.files?.[0] || null;
              setBestand(f);
              if (f && !naam) setNaam(f.name.replace(/\.(zip|tar|tar\.gz|tgz)$/i, ""));
            }}
            className="w-full text-sm file:mr-3 file:rounded-md file:border-0 file:bg-secondary
                       file:px-3 file:py-1.5 file:text-sm file:text-secondary-foreground"
          />
          <p className="mt-1 text-xs text-muted-foreground">
            .zip, .tar of .tar.gz, tot 512 MB. Hij wordt uitgepakt in een eigen volume van dit
            cluster en hangt bij een opdracht alleen-lezen in de container.
          </p>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <Label>Naam</Label>
            <Input value={naam} onChange={(e) => setNaam(e.target.value)}
                   placeholder="byom-dump" />
          </div>
          <div>
            <Label>Wat moet ermee gebeuren?</Label>
            <Input value={instructie} onChange={(e) => setInstructie(e.target.value)}
                   placeholder="Zet deze dump in de Cosmos-emulator." />
          </div>
        </div>
        <Button onClick={upload} disabled={!bestand || uploadt}>
          {uploadt ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
          {uploadt ? "Bezig met uitpakken…" : "Toevoegen"}
        </Button>
        {uploadt && (
          <p className="text-xs text-muted-foreground">
            Uploaden en uitpakken van tientallen megabytes duurt een halve tot twee minuten.
          </p>
        )}
      </div>

      {bundels.length === 0
        ? <div className="mt-3"><EmptyState>Nog geen bestanden toegevoegd.</EmptyState></div>
        : <ul className="mt-3 space-y-2">
            {bundels.map((b) => (
              <BundelRegel key={b.id} bundel={b} cluster={cluster} onWeg={() => weg(b)}
                           onBijgewerkt={(nieuw) =>
                             setBundels((xs) => xs.map((x) => (x.id === nieuw.id ? nieuw : x)))} />
            ))}
          </ul>}
    </Card>
  );
}

function BundelRegel({ bundel, cluster, onWeg, onBijgewerkt }: {
  bundel: ClusterBundel;
  cluster: Cluster;
  onWeg: () => void;
  onBijgewerkt: (b: ClusterBundel) => void;
}) {
  const melding = useMelding();
  const [dienstId, setDienstId] = useState(String(cluster.diensten[0]?.id || ""));
  const [opdracht, setOpdracht] = useState(bundel.instructie);
  const [bezig, setBezig] = useState(false);
  const [uitslag, setUitslag] = useState<BundelUitslag | null>(null);
  // Een script dat één keer goed ging wil je ongewijzigd kunnen herhalen --
  // anders raadt het model er de volgende keer opnieuw naar, en ben je een
  // werkende laadbeurt kwijt aan een variatie die net anders uitpakt.
  const [herhaal, setHerhaal] = useState(false);

  const uitslagScript = uitslag?.script || "";
  const uitslagImage = uitslag?.image || "";
  const dienst = cluster.diensten.find((d) => String(d.id) === dienstId);

  async function draai() {
    if (!dienstId) return;
    setBezig(true);
    setUitslag(null);
    try {
      const uit = await clusterApi.bundelToepassen(bundel.id, {
        dienst_id: Number(dienstId),
        instructie: opdracht,
        ...(herhaal && uitslagScript ? { script: uitslagScript, image: uitslagImage } : {}),
      });
      setUitslag(uit);
      if (uit.exit_code === 0) melding.ok("De opdracht is klaar.");
      else if (uit.exit_code !== null) melding.fout(`Gestopt met code ${uit.exit_code}.`);
      if (opdracht !== bundel.instructie) {
        onBijgewerkt(await clusterApi.bundelWijzigen(bundel.id, { instructie: opdracht }));
      }
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Dat lukte niet");
    } finally {
      setBezig(false);
    }
  }

  return (
    <li className="rounded-lg border border-border p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{bundel.naam}</span>
            <Badge tone="neutral">{bundel.aantal_bestanden} bestanden · {bundel.omvang}</Badge>
            {bundel.laatste_exitcode === 0 && <Badge tone="green">eerder geladen</Badge>}
            {typeof bundel.laatste_exitcode === "number" && bundel.laatste_exitcode !== 0 && (
              <Badge tone="red">liep vast</Badge>
            )}
          </div>
          <p className="mt-0.5 truncate font-mono text-xs text-muted-foreground">
            {bundel.bestandsnaam}
          </p>
        </div>
        <Button variant="ghost" onClick={onWeg} aria-label="Weggooien">
          <Trash2 className="h-4 w-4" />
        </Button>
      </div>

      <details className="mt-2">
        <summary className="cursor-pointer text-xs text-muted-foreground">
          Wat erin zit ({bundel.boom.length}{bundel.aantal_bestanden > bundel.boom.length
            ? ` van ${bundel.aantal_bestanden}` : ""} getoond)
        </summary>
        <ul className="mt-1 max-h-48 overflow-auto rounded bg-muted/40 p-2 font-mono text-xs">
          {bundel.boom.map((x) => (
            <li key={x.pad} className="truncate">
              {bundel.koppelpunt}/{x.pad}
            </li>
          ))}
        </ul>
      </details>

      <div className="mt-3 grid gap-2 sm:grid-cols-[1fr_auto]">
        <div>
          <Label>Opdracht</Label>
          <TextArea rows={2} value={opdracht} onChange={(e) => setOpdracht(e.target.value)}
                    placeholder="Zet deze dump in de Cosmos-emulator, met de juiste partition keys." />
        </div>
        <div>
          <Label>Tegen welke dienst?</Label>
          <Select value={dienstId} onChange={(e) => setDienstId(e.target.value)}>
            {cluster.diensten.length === 0 && <option value="">geen vaste diensten</option>}
            {cluster.diensten.map((d) => (
              <option key={d.id} value={d.id}>{d.naam}</option>
            ))}
          </Select>
          <p className="mt-1 text-xs text-muted-foreground">
            {dienst
              ? <>Bereikbaar als <span className="font-mono">{dienst.naam}</span> in het script.</>
              : "Dit cluster heeft nog geen vaste diensten."}
          </p>
        </div>
      </div>

      {uitslagScript && (
        <label className="mt-2 flex items-center gap-2 text-xs">
          <input type="checkbox" checked={herhaal} onChange={(e) => setHerhaal(e.target.checked)} />
          Draai precies het script hieronder opnieuw, zonder het model
        </label>
      )}

      <div className="mt-2">
        <Button onClick={draai} disabled={bezig || !dienstId || (!opdracht.trim() && !herhaal)}>
          {bezig ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
          {bezig ? "Bezig…" : "Uitvoeren"}
        </Button>
        {bezig && (
          <p className="mt-1 text-xs text-muted-foreground">
            Dit kan minuten duren: het model schrijft het script en de container draait het
            daarna echt tegen {dienst?.naam || "de dienst"}.
          </p>
        )}
      </div>

      {uitslag && (
        <div className="mt-3 space-y-2">
          {uitslag.toelichting && <p className="text-sm">{uitslag.toelichting}</p>}
          {uitslagScript && (
            <details className="rounded border border-border">
              <summary className="cursor-pointer px-2 py-1 text-xs text-muted-foreground">
                Het script dat gedraaid heeft{uitslagImage ? ` (${uitslagImage})` : ""}
              </summary>
              <pre className="max-h-56 overflow-auto border-t border-border p-2 font-mono text-xs">
                {uitslagScript}
              </pre>
            </details>
          )}
          {uitslag.uitvoer && (
            <pre className={`max-h-72 overflow-auto rounded p-2 font-mono text-xs leading-relaxed ${
              uitslag.exit_code === 0 ? "bg-muted/40" : "bg-destructive/5 text-destructive"}`}>
              {uitslag.uitvoer}
            </pre>
          )}
        </div>
      )}

      {!uitslag && bundel.laatste_uitvoer && (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs text-muted-foreground">
            Vorige keer ({bundel.laatst_toegepast
              ? new Date(bundel.laatst_toegepast).toLocaleString("nl-NL") : "eerder"},
            code {bundel.laatste_exitcode})
          </summary>
          <pre className="mt-1 max-h-56 overflow-auto rounded bg-muted/40 p-2 font-mono text-xs">
            {bundel.laatste_uitvoer}
          </pre>
        </details>
      )}
    </li>
  );
}
