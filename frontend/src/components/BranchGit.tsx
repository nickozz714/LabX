/**
 * BranchGit.tsx — je werk uit een branch-lab krijgen.
 *
 * Een branch-lab IS een werkkopie van een echte branch, maar je kon er alles
 * mee behalve het enige waar het uiteindelijk om gaat: committen en pushen.
 * Dat moest via de terminal in het lab, met een token dat daar niet hoort te
 * staan.
 *
 * DRIE KEUZES DIE DIT SCHERM VERKLAREN:
 *
 * - **Vinkjes, geen `git add -A`.** Een lab bouwt: er liggen logbestanden,
 *   een `.env` die je net hebt ingevuld, rommel van een afgebroken poging.
 *   Alles meesturen is bij een `.env` niet "ruis in de diff" maar een geheim
 *   in een repo waar het nooit meer uit gaat. Vandaar dat zo'n bestand hier
 *   standaard UIT staat, met een waarschuwing erbij.
 *
 * - **Het aantal staat op de knop.** "Push" zegt niet hoeveel er meegaat;
 *   "Committen en pushen (7)" wel. Dat is het laatste moment waarop je ziet
 *   dat je er één te veel of één te weinig hebt aangevinkt.
 *
 * - **De uitvoer van git komt terug zoals hij is.** Een `! [rejected]
 *   non-fast-forward` is informatie, geen foutmelding om weg te vertalen.
 */
import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle, ArrowDown, ArrowUp, Check, GitCommitHorizontal, Loader2,
  RefreshCw, Upload,
} from "lucide-react";
import { ApiError } from "@/lib/api";
import { clusterApi } from "@/lib/clusters";
import type { BranchGitStatus, PushUitslag } from "@/lib/clusters";
import { Badge, Button, EmptyState, Label, TextArea } from "@/components/ui";
import { useMelding } from "@/components/Meldingen";
import { useBevestiging } from "@/components/Bevestiging";

const KLEUR: Record<string, "neutral" | "green" | "red" | "yellow"> = {
  nieuw: "green", gewijzigd: "neutral", verwijderd: "red",
  toegevoegd: "green", hernoemd: "yellow",
};

export function BranchGit({ labId, titel }: { labId: string; titel?: string }) {
  const melding = useMelding();
  const bevestig = useBevestiging();

  const [status, setStatus] = useState<BranchGitStatus | null>(null);
  const [laadt, setLaadt] = useState(true);
  const [gekozen, setGekozen] = useState<Set<string>>(new Set());
  const [bericht, setBericht] = useState("");
  const [bezig, setBezig] = useState(false);
  const [uitslag, setUitslag] = useState<PushUitslag | null>(null);

  const laad = useCallback(async () => {
    setLaadt(true);
    try {
      const s = await clusterApi.branchGit(labId);
      setStatus(s);
      // De voorselectie komt van de server: die weet welke bestanden
      // gevoelig zijn, en dat oordeel hoort niet op twee plekken te staan.
      setGekozen(new Set(s.bestanden.filter((b) => b.standaard_aan).map((b) => b.pad)));
      setBericht((b) => b || s.titel || titel || "");
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Git-status niet op te halen");
    } finally {
      setLaadt(false);
    }
  }, [labId, melding, titel]);

  useEffect(() => { void laad(); }, [laad]);

  function wissel(pad: string) {
    setGekozen((s) => {
      const n = new Set(s);
      if (n.has(pad)) n.delete(pad); else n.add(pad);
      return n;
    });
  }

  async function push() {
    if (!status) return;
    const lijst = [...gekozen];
    const gevoelig = status.bestanden.filter((b) => b.gevoelig && gekozen.has(b.pad));
    if (gevoelig.length > 0) {
      const ja = await bevestig.vraag({
        titel: "Een geheim meesturen?",
        tekst: `${gevoelig.map((b) => b.pad).join(", ")} ${gevoelig.length === 1 ? "hoort" : "horen"} `
          + "meestal niet in een repo. Eenmaal gepusht staat de inhoud er ook na een revert nog in.",
        variant: "danger",
        bevestig: "Toch meesturen",
      });
      if (!ja) return;
    }
    setBezig(true);
    setUitslag(null);
    try {
      const uit = await clusterApi.branchGitPush(labId, {
        bericht: bericht.trim(),
        bestanden: lijst,
      });
      setUitslag(uit);
      if (uit.ok) melding.ok(`Gepusht naar ${uit.branch}.`);
      else melding.fout("Git kwam er niet doorheen — de uitvoer staat eronder.");
      await laad();
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Pushen mislukte");
    } finally {
      setBezig(false);
    }
  }

  if (laadt && !status) {
    return (
      <p className="flex items-center gap-2 py-2 text-xs text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Git-status ophalen…
      </p>
    );
  }
  if (!status) return null;

  const teKiezen = status.bestanden.length;
  const aantal = gekozen.size;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Badge tone="neutral">{status.branch}</Badge>
        {status.op_remote
          ? <Badge tone="green">staat op de remote</Badge>
          : <Badge tone="yellow">nog niet gepusht</Badge>}
        {status.vooruit > 0 && (
          <span className="inline-flex items-center gap-1 text-muted-foreground">
            <ArrowUp className="h-3 w-3" />{status.vooruit} commit{status.vooruit === 1 ? "" : "s"} vooruit
          </span>
        )}
        {status.achter > 0 && (
          <span className="inline-flex items-center gap-1 text-amber-500">
            <ArrowDown className="h-3 w-3" />{status.achter} achter
          </span>
        )}
        <Button variant="ghost" onClick={laad} aria-label="Verversen" className="ml-auto">
          <RefreshCw className={`h-3.5 w-3.5 ${laadt ? "animate-spin" : ""}`} />
        </Button>
      </div>

      {status.laatste_commit && (
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <GitCommitHorizontal className="h-3.5 w-3.5" /> {status.laatste_commit}
        </p>
      )}

      {status.schoon ? (
        <EmptyState>
          Niets gewijzigd in de werkkopie.
          {status.vooruit > 0 && " Er staan wel commits klaar om te pushen."}
        </EmptyState>
      ) : (
        <div className="rounded-lg border border-border">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2">
            <span className="text-xs font-medium">
              {aantal} van {teKiezen} bestand{teKiezen === 1 ? "" : "en"} geselecteerd
            </span>
            <div className="flex gap-1">
              <button className="text-xs text-muted-foreground hover:text-foreground"
                      onClick={() => setGekozen(new Set(status.bestanden.map((b) => b.pad)))}>
                alles
              </button>
              <span className="text-xs text-muted-foreground">·</span>
              <button className="text-xs text-muted-foreground hover:text-foreground"
                      onClick={() => setGekozen(new Set())}>
                niets
              </button>
            </div>
          </div>
          <ul className="max-h-72 overflow-auto p-2">
            {status.bestanden.map((b) => (
              <li key={b.pad}>
                <label className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 hover:bg-muted/50">
                  <input type="checkbox" checked={gekozen.has(b.pad)}
                         onChange={() => wissel(b.pad)} />
                  <Badge tone={KLEUR[b.soort] || "neutral"}>{b.soort}</Badge>
                  <span className="min-w-0 flex-1 truncate font-mono text-xs">{b.pad}</span>
                  {b.gevoelig && (
                    <span className="inline-flex shrink-0 items-center gap-1 text-xs text-amber-500"
                          title="Dit lijkt een geheim of een bestand van LabX zelf — standaard uitgevinkt">
                      <AlertTriangle className="h-3.5 w-3.5" /> let op
                    </span>
                  )}
                </label>
              </li>
            ))}
          </ul>
          {status.afgekapt && (
            <p className="border-t border-border px-3 py-2 text-xs text-muted-foreground">
              De lijst is afgekapt. Zoveel gewijzigde bestanden wijst meestal op een map die
              in <span className="font-mono">.gitignore</span> hoort.
            </p>
          )}
        </div>
      )}

      <div>
        <Label>Commitbericht</Label>
        <TextArea rows={2} value={bericht} onChange={(e) => setBericht(e.target.value)}
                  placeholder={status.titel || "Wat heb je gedaan?"} />
        <p className="mt-1 text-xs text-muted-foreground">
          Voorgevuld met de titel van deze branch. Dit komt in de repo terecht — de
          beschrijving van het lab niet.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button onClick={push} disabled={bezig || (aantal === 0 && status.vooruit === 0)}>
          {bezig ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
          {aantal > 0 ? `Committen en pushen (${aantal})` : "Pushen"}
        </Button>
        {aantal === 0 && status.vooruit > 0 && (
          <span className="text-xs text-muted-foreground">
            Niets aangevinkt — dit pusht alleen wat er al gecommit is.
          </span>
        )}
      </div>

      {uitslag && (
        <div className={`rounded-lg border p-2 ${
          uitslag.ok ? "border-border" : "border-destructive/50 bg-destructive/5"}`}>
          <p className="mb-1 flex items-center gap-1.5 text-xs font-medium">
            {uitslag.ok ? <Check className="h-3.5 w-3.5" /> : <AlertTriangle className="h-3.5 w-3.5" />}
            {uitslag.ok ? `Op ${uitslag.branch}` : `Git stopte met code ${uitslag.exit_code}`}
          </p>
          <pre className="max-h-56 overflow-auto rounded bg-muted/40 p-2 font-mono text-xs leading-relaxed">
            {uitslag.uitvoer || "(geen uitvoer)"}
          </pre>
        </div>
      )}
    </div>
  );
}
