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
  AlertTriangle, ArrowDown, ArrowUp, Check, GitBranchPlus, GitCommitHorizontal,
  Loader2, RefreshCw, Trash2, Upload,
} from "lucide-react";
import { ApiError } from "@/lib/api";
import { clusterApi } from "@/lib/clusters";
import type { BranchGitStatus, PushUitslag } from "@/lib/clusters";
import { Badge, Button, EmptyState, Input, Label, TextArea } from "@/components/ui";
import { useMelding } from "@/components/Meldingen";
import { useBevestiging } from "@/components/Bevestiging";

const KLEUR: Record<string, "neutral" | "green" | "red" | "yellow"> = {
  nieuw: "green", gewijzigd: "neutral", verwijderd: "red",
  toegevoegd: "green", hernoemd: "yellow",
};

export function BranchGit({ labId, titel, onGewisseld }: {
  labId: string;
  titel?: string;
  /** Het lab staat nu op een andere branch — het scherm eromheen moet
   *  herladen, want LabX gebruikt die branch om tickets aan labs te koppelen. */
  onGewisseld?: () => void;
}) {
  const melding = useMelding();
  const bevestig = useBevestiging();

  const [status, setStatus] = useState<BranchGitStatus | null>(null);
  const [laadt, setLaadt] = useState(true);
  const [gekozen, setGekozen] = useState<Set<string>>(new Set());
  const [bericht, setBericht] = useState("");
  const [bezig, setBezig] = useState(false);
  const [uitslag, setUitslag] = useState<PushUitslag | null>(null);
  // Van branch wisselen hoort hier, bij de status: je kijkt naar wat er
  // openstaat en beslist dán of je weg kunt.
  const [branches, setBranches] = useState<{ naam: string; lokaal: boolean; op_remote: boolean }[]>([]);
  const [doel, setDoel] = useState("");
  const [wisselt, setWisselt] = useState(false);

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

  useEffect(() => {
    // De branchlijst doet een `git fetch` en kost dus even; apart ophalen,
    // zodat de status er al staat terwijl dit nog loopt.
    clusterApi.branchLijst(labId)
      .then((b) => setBranches(b.branches.filter((x) => !x.huidig)))
      .catch(() => { /* geen lijst is geen fout; je kunt nog steeds typen */ });
  }, [labId]);

  async function wisselBranch() {
    if (!status || !doel) return;
    const vuil = status.bestanden.length;
    let weggooien = false;
    if (vuil > 0) {
      const ja = await bevestig.vraag({
        titel: `${vuil} wijziging(en) weggooien?`,
        tekst: `Je staat op ${status.branch} met ongecommit werk: `
          + status.bestanden.slice(0, 6).map((b) => b.pad).join(", ")
          + (vuil > 6 ? ` en nog ${vuil - 6}` : "")
          + ". Wisselen van branch kan alleen door dit weg te gooien. Daarna is het "
          + "echt weg — ook de nieuwe bestanden.",
        variant: "danger",
        bevestig: "Weggooien en wisselen",
      });
      if (!ja) return;
      weggooien = true;
    }
    setWisselt(true);
    try {
      const uit = await clusterApi.branchCheckout(labId, { branch: doel, weggooien });
      if (uit.ok) {
        melding.ok(`Nu op ${uit.branch}.`);
        setDoel("");
        onGewisseld?.();
        await laad();
      } else {
        melding.fout("Wisselen lukte niet", uit.uitvoer.slice(0, 300));
      }
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Wisselen lukte niet");
    } finally {
      setWisselt(false);
    }
  }

  async function gooiWeg() {
    if (!status) return;
    const ja = await bevestig.vraag({
      titel: `${status.bestanden.length} wijziging(en) weggooien?`,
      tekst: status.bestanden.map((b) => b.pad).slice(0, 10).join(", ")
        + ". Dit zet de werkkopie terug op de laatste commit. Nieuwe bestanden "
        + "verdwijnen mee en zijn niet terug te halen.",
      variant: "danger",
      bevestig: "Alles weggooien",
    });
    if (!ja) return;
    try {
      const uit = await clusterApi.branchWeggooien(labId);
      melding.ok(`${uit.weggegooid.length} wijziging(en) weggegooid.`);
      await laad();
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Weggooien lukte niet");
    }
  }

  /**
   * Bijwerken vanaf JE EIGEN branch, niet vanaf main.
   *
   * Dat was eerst andersom en dat is de verkeerde kant op: aan een
   * feature-branch werkt meestal meer dan één mens. Gemeten geval: een lab
   * stond 2 commits vooruit en 65 achter op zijn eigen branch, de push werd
   * geweigerd, en bijwerken vanaf main gaf 41 conflicten. Met de eigen
   * branch waren het er nul.
   */
  async function werkBij(basis?: string) {
    try {
      const uit = await clusterApi.branchBijwerken(labId, basis);
      setUitslag({ ok: uit.ok, exit_code: uit.ok ? 0 : 1,
                   branch: status?.branch || "", uitvoer: uit.uitvoer });
      await laad();
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Bijwerken lukte niet");
    }
  }

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
        <Button variant="ghost" onClick={() => void werkBij()} className="ml-auto"
                title={status.achter > 0
                  ? `${status.achter} commit(s) van origin/${status.branch} ophalen en mergen`
                  : `Ophalen en mergen vanaf origin/${status.branch}`}>
          <ArrowDown className="h-3.5 w-3.5" />
          {status.achter > 0 ? `Bijwerken (${status.achter})` : "Bijwerken"}
        </Button>
        {/* Main blijft bereikbaar, want soms wil je dat juist — maar als
            losse keuze, niet als wat er gebeurt als je niets kiest. */}
        <Button variant="ghost" onClick={() => void werkBij("main")}
                title="De basis main in deze branch mergen">
          vanaf main
        </Button>
        <Button variant="ghost" onClick={laad} aria-label="Verversen">
          <RefreshCw className={`h-3.5 w-3.5 ${laadt ? "animate-spin" : ""}`} />
        </Button>
      </div>

      {/* Van branch wisselen staat hier, vlak onder de status: je kijkt naar
          wat er openstaat en beslist dán pas of je weg kunt. */}
      <div className="flex flex-wrap items-end gap-2 rounded-lg border border-border p-2">
        <div className="min-w-0 flex-1">
          <Label>Naar een andere branch</Label>
          {/* Een invoerveld met suggesties, geen keuzelijst. Gemeten op
              IntelligentHive: 790 branches. Daar scrol je niet doorheen — je
              typt een stuk van de naam. En je mag een branch intypen die er
              nog niet is; dan wordt hij aangemaakt. */}
          <Input list={`branches-${labId}`} value={doel} placeholder="typ of kies een branch…"
                 onChange={(e) => setDoel(e.target.value)} />
          <datalist id={`branches-${labId}`}>
            {branches.map((b) => (
              <option key={b.naam} value={b.naam}>
                {b.lokaal ? "hier" : "alleen op de remote"}
              </option>
            ))}
          </datalist>
          <p className="mt-1 text-xs text-muted-foreground">
            {branches.length > 0
              ? <>{branches.length} branch{branches.length === 1 ? "" : "es"} beschikbaar.
                  Een naam die nog niet bestaat, wordt aangemaakt.</>
              : "De branchlijst wordt opgehaald…"}
          </p>
        </div>
        <Button variant="secondary" onClick={wisselBranch} disabled={!doel || wisselt}>
          {wisselt ? <Loader2 className="h-4 w-4 animate-spin" /> : <GitBranchPlus className="h-4 w-4" />}
          Omzetten
        </Button>
        {status.bestanden.length > 0 && (
          <Button variant="danger" onClick={gooiWeg}
                  title="Zet de werkkopie terug op de laatste commit">
            <Trash2 className="h-3.5 w-3.5" /> Wijzigingen weggooien
          </Button>
        )}
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
