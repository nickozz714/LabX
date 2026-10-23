/**
 * De pull request van een branch-lab: maken, en de reacties erop lezen.
 *
 * Waarom het voorstel eerst in beeld komt en niet meteen verstuurd wordt:
 * een titel en een beschrijving zijn het enige wat een reviewer van je krijgt
 * voordat hij de diff opent. Die wil je kunnen bijschaven — en zien wat LabX
 * eruit haalde, want als het ticket niet gevonden is, staat dat er dan ook
 * meteen.
 */
import { useState } from "react";
import {
  Check, ExternalLink, GitPullRequest, Loader2, MessageSquare, Send, ShieldCheck, X,
} from "lucide-react";

import { ApiError } from "@/lib/api";
import { clusterApi } from "@/lib/clusters";
import { Button, Input, Label, TextArea } from "@/components/ui";
import { useMelding } from "@/components/Meldingen";

type Voorstel = Awaited<ReturnType<typeof clusterApi.prVoorstel>>;
type Comments = Awaited<ReturnType<typeof clusterApi.prComments>>;

export const PullRequest = ({ labId }: { labId: string }) => {
  const melding = useMelding();
  const [voorstel, setVoorstel] = useState<Voorstel | null>(null);
  const [titel, setTitel] = useState("");
  const [tekst, setTekst] = useState("");
  const [bezig, setBezig] = useState(false);
  const [laadt, setLaadt] = useState(false);
  const [fout, setFout] = useState("");

  const [rechten, setRechten] = useState<Awaited<
    ReturnType<typeof clusterApi.prRechten>> | null>(null);
  const [reacties, setReacties] = useState<Comments | null>(null);
  const [gekozen, setGekozen] = useState<Set<string>>(new Set());

  async function laad() {
    setLaadt(true);
    setFout("");
    try {
      const v = await clusterApi.prVoorstel(labId);
      setVoorstel(v);
      setTitel(v.titel);
      setTekst(v.beschrijving);
      if (v.pr_nummer) await laadReacties();
    } catch (e) {
      setFout(e instanceof ApiError ? e.message : String(e));
    } finally {
      setLaadt(false);
    }
  }

  /** Waar zit het vast? Een 403 kan drie dingen betekenen en die worden elk
   *  ergens anders opgelost — dus zeggen we welke het is. */
  async function kijkRechten() {
    setBezig(true);
    try {
      setRechten(await clusterApi.prRechten(labId));
    } catch (e) {
      melding.fout("Rechten nakijken lukte niet",
                   e instanceof ApiError ? e.message : String(e));
    } finally {
      setBezig(false);
    }
  }

  async function laadReacties() {
    try {
      setReacties(await clusterApi.prComments(labId));
    } catch (e) {
      melding.fout("Reacties ophalen lukte niet",
                   e instanceof ApiError ? e.message : String(e));
    }
  }

  // Niet bij het openen van het Git-paneel: dit gaat bij GitHub langs (de
  // diff, de open PR's, de standaardbranch) en dat hoort te gebeuren omdat
  // JIJ een pull request wilt, niet omdat je op Git klikte.

  async function maak(concept: boolean) {
    setBezig(true);
    try {
      const uit = await clusterApi.prMaken(labId, { titel, beschrijving: tekst, concept });
      melding.ok(uit.melding, uit.url);
      await laad();
    } catch (e) {
      melding.fout("Pull request maken lukte niet",
                   e instanceof ApiError ? e.message : String(e));
    } finally {
      setBezig(false);
    }
  }

  async function naarTicket() {
    setBezig(true);
    try {
      const uit = await clusterApi.prNaarTicket(labId, [...gekozen]);
      melding.ok(`${uit.aantal} reactie(s) op ${uit.ticket} gezet`);
      setGekozen(new Set());
    } catch (e) {
      melding.fout("Doorzetten lukte niet",
                   e instanceof ApiError ? e.message : String(e));
    } finally {
      setBezig(false);
    }
  }

  if (!voorstel && !fout) {
    return (
      <div className="flex items-center gap-2">
        <GitPullRequest className="h-4 w-4 text-muted-foreground" />
        <span className="text-sm">Pull request</span>
        <Button variant="ghost" className="ml-auto" disabled={laadt}
                onClick={() => void laad()}>
          {laadt ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
          {laadt ? "Ophalen…" : "Bekijken"}
        </Button>
      </div>
    );
  }
  if (fout) {
    return (
      <div className="space-y-2 p-3">
        <p className="text-sm text-destructive">{fout}</p>
        <Button variant="secondary" disabled={bezig} onClick={() => void kijkRechten()}>
          <ShieldCheck className="h-3.5 w-3.5" /> Rechten controleren
        </Button>
        {rechten && <Rechten uitslag={rechten} />}
      </div>
    );
  }
  if (!voorstel) return null;

  return (
    <div className="space-y-3 p-1">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <GitPullRequest className="h-4 w-4 text-muted-foreground" />
        <span className="font-mono text-xs">{voorstel.branch}</span>
        <span className="text-muted-foreground">naar</span>
        <span className="font-mono text-xs">{voorstel.naar}</span>
        {voorstel.ticket
          ? <span className="rounded bg-muted px-1.5 py-0.5 text-xs">{voorstel.ticket}</span>
          : <span className="text-xs text-warning">geen ticket met deze branch</span>}
      </div>

      {voorstel.bestaat_al ? (
        <div className="rounded-md border border-border bg-muted/40 p-3 text-sm">
          Er staat al een pull request open voor deze branch:{" "}
          <a className="underline" href={voorstel.pr_url} target="_blank" rel="noreferrer">
            #{voorstel.pr_nummer} <ExternalLink className="inline h-3 w-3" />
          </a>
        </div>
      ) : (
        <>
          <div>
            <Label>Titel</Label>
            <Input value={titel} onChange={(e) => setTitel(e.target.value)} />
          </div>
          <div>
            <Label>Beschrijving</Label>
            <TextArea rows={10} value={tekst} onChange={(e) => setTekst(e.target.value)}
                      className="font-mono text-xs" />
            <p className="mt-1 text-xs text-muted-foreground">
              Gevuld uit het ticket en de {voorstel.commits.length} commit(s) in deze
              branch. Pas gerust aan — dit is wat de reviewer als eerste leest.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" disabled={bezig} onClick={() => void maak(false)}>
              {bezig ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                     : <GitPullRequest className="h-3.5 w-3.5" />} Pull request maken
            </Button>
            <Button variant="secondary" disabled={bezig} onClick={() => void maak(true)}>
              Als concept
            </Button>
            <Button variant="ghost" disabled={bezig} onClick={() => void kijkRechten()}
                    title="Wat mag het token van dit cluster bij deze repo?">
              <ShieldCheck className="h-3.5 w-3.5" /> Rechten controleren
            </Button>
          </div>
          {rechten && <Rechten uitslag={rechten} />}
        </>
      )}

      {reacties && reacties.comments.length > 0 && (
        <div className="mt-2 rounded-md border border-border p-2">
          <div className="mb-2 flex items-center gap-2 text-sm">
            <MessageSquare className="h-3.5 w-3.5 text-muted-foreground" />
            {reacties.comments.length} reactie(s) op PR #{reacties.pr_nummer}
            <Button variant="ghost" className="ml-auto" onClick={() => void laadReacties()}>
              Verversen
            </Button>
          </div>
          <div className="max-h-72 space-y-2 overflow-auto">
            {reacties.comments.map((k) => (
              <label key={k.id}
                     className="flex cursor-pointer gap-2 rounded border border-border p-2 text-xs">
                <input type="checkbox" checked={gekozen.has(k.id)}
                       onChange={() => setGekozen((s) => {
                         const n = new Set(s);
                         if (n.has(k.id)) n.delete(k.id); else n.add(k.id);
                         return n;
                       })} />
                <span className="min-w-0">
                  <span className="text-muted-foreground">
                    {k.auteur} · {k.soort}{k.plek ? ` · ${k.plek}` : ""}
                  </span>
                  <span className="mt-0.5 block whitespace-pre-wrap break-words">{k.tekst}</span>
                </span>
              </label>
            ))}
          </div>
          <Button variant="secondary" className="mt-2"
                  disabled={bezig || gekozen.size === 0 || !reacties.ticket}
                  title={reacties.ticket
                    ? `Als opmerking op ${reacties.ticket} zetten`
                    : "Er is geen ticket met deze branch om ze op te zetten"}
                  onClick={() => void naarTicket()}>
            <Send className="h-3.5 w-3.5" />
            Naar {reacties.ticket || "ticket"} ({gekozen.size})
          </Button>
        </div>
      )}
    </div>
  );
};


/**
 * De uitslag van de rechtencontrole.
 *
 * Drie regels en één advies, want een 403 van GitHub zegt niet waar het aan
 * ligt: het token ziet de repo niet, het mag niet pushen, of de permissie
 * Pull requests staat uit — en dat laatste kan ook nog "wacht op goedkeuring
 * van de organisatie" betekenen.
 */
const Rechten = ({ uitslag }: {
  uitslag: { repo: string; ziet_repo: boolean; mag_pushen: boolean;
             pr_lezen: boolean; reden: string; advies: string };
}) => (
  <div className="mt-2 rounded-md border border-border bg-muted/40 p-3 text-xs">
    <p className="mb-2 font-mono text-muted-foreground">{uitslag.repo}</p>
    <Regel aan={uitslag.ziet_repo} tekst="Het token ziet deze repository" />
    <Regel aan={uitslag.mag_pushen} tekst="Het mag pushen" />
    <Regel aan={uitslag.pr_lezen} tekst="Het komt bij pull requests" />
    {uitslag.reden && (
      <p className="mt-2 text-muted-foreground">{uitslag.reden}</p>
    )}
    {uitslag.advies && <p className="mt-2">{uitslag.advies}</p>}
  </div>
);

const Regel = ({ aan, tekst }: { aan: boolean; tekst: string }) => (
  <p className="flex items-center gap-1.5">
    {aan ? <Check className="h-3.5 w-3.5 text-success" />
         : <X className="h-3.5 w-3.5 text-destructive" />}
    {tekst}
  </p>
);
