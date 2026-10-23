/**
 * Een feature-branch verdelen over kleinere pull requests.
 *
 * Waarom dit een voorstel toont in plaats van het meteen te doen: wat bij
 * elkaar hoort is een oordeel. Een groep die niet zonder een andere kan
 * bouwen, is geen eigen pull request — en of dat zo is, weet jij beter dan
 * het model. Je ziet dus eerst de verdeling mét de reden per groep, en kunt
 * groepen uitvinken voordat er branches ontstaan die iemand moet opruimen.
 *
 * Bestanden die het model niet kon plaatsen komen apart in beeld. Ze
 * verdwijnen bewust niet in een restgroep: dan zou je pas merken dat er iets
 * ontbreekt als de pull requests al open staan.
 */
import { useState } from "react";
import { GitBranch, Loader2, Scissors, TriangleAlert } from "lucide-react";

import { ApiError } from "@/lib/api";
import { clusterApi } from "@/lib/clusters";
import { Button } from "@/components/ui";
import { BasisKiezer } from "@/components/BasisKiezer";
import { useMelding } from "@/components/Meldingen";

type Voorstel = Awaited<ReturnType<typeof clusterApi.splitsVoorstel>>;
type Uitslag = Awaited<ReturnType<typeof clusterApi.splitsMaken>>;

export const BranchOpsplitsen = ({ labId }: { labId: string }) => {
  const melding = useMelding();
  const [voorstel, setVoorstel] = useState<Voorstel | null>(null);
  const [uit, setUit] = useState<Uitslag | null>(null);
  const [aan, setAan] = useState<Set<string>>(new Set());
  const [bezig, setBezig] = useState(false);
  const [basis, setBasis] = useState("");

  async function haal(welke?: string) {
    setBezig(true);
    setUit(null);
    try {
      const v = await clusterApi.splitsVoorstel(labId, welke ?? basis);
      setVoorstel(v);
      setBasis(v.basis);
      setAan(new Set(v.groepen.map((g) => g.branch)));
    } catch (e) {
      melding.fout("Verdeling opstellen lukte niet",
                   e instanceof ApiError ? e.message : String(e));
    } finally {
      setBezig(false);
    }
  }

  async function maak() {
    if (!voorstel) return;
    setBezig(true);
    try {
      const gekozen = voorstel.groepen.filter((g) => aan.has(g.branch));
      setUit(await clusterApi.splitsMaken(labId, gekozen, basis));
      melding.ok(`${gekozen.length} branch(es) aangemaakt`);
    } catch (e) {
      melding.fout("Branches maken lukte niet",
                   e instanceof ApiError ? e.message : String(e));
    } finally {
      setBezig(false);
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <Scissors className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="text-sm">Opsplitsen in kleinere pull requests</span>
        <Button variant="ghost" className="ml-auto" disabled={bezig}
                onClick={() => void haal()}>
          {bezig ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
          {voorstel ? "Opnieuw verdelen" : "Verdeling voorstellen"}
        </Button>
      </div>

      {voorstel && (
        <div className="rounded-md border border-border p-2 text-xs">
          <div className="mb-2">
            <BasisKiezer labId={labId} basis={voorstel.basis}
                         bron={voorstel.basis_bron} zeker={voorstel.basis_zeker}
                         onKies={(b) => { setBasis(b); void haal(b); }} />
          </div>
          <p className="mb-2 text-muted-foreground">
            {voorstel.bestanden.length} gewijzigde bestanden tussen{" "}
            <code className="font-mono">{voorstel.basis}</code> en{" "}
            <code className="font-mono">{voorstel.branch}</code>. Elke branch
            begint bij de basis, dus ze staan naast elkaar en niet op elkaar.
          </p>

          {voorstel.groepen.map((g) => (
            <label key={g.branch}
                   className="mb-2 flex cursor-pointer gap-2 rounded border border-border p-2">
              <input type="checkbox" checked={aan.has(g.branch)}
                     onChange={() => setAan((s) => {
                       const n = new Set(s);
                       if (n.has(g.branch)) n.delete(g.branch); else n.add(g.branch);
                       return n;
                     })} />
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-1.5 font-medium">
                  <GitBranch className="h-3 w-3" />
                  <code className="font-mono">{g.branch}</code>
                  <span className="text-muted-foreground">
                    · {g.bestanden.length} bestand(en)
                  </span>
                </span>
                <span className="mt-0.5 block">{g.titel}</span>
                <span className="mt-0.5 block text-muted-foreground">{g.reden}</span>
                <span className="mt-1 block font-mono text-[11px] text-muted-foreground">
                  {g.bestanden.join("  ·  ")}
                </span>
              </span>
            </label>
          ))}

          {voorstel.niet_toegewezen.length > 0 && (
            <p className="mb-2 flex items-start gap-1.5 text-warning">
              <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>
                {voorstel.niet_toegewezen.length} bestand(en) zijn aan geen enkele
                groep toegewezen en gaan dus in géén van deze branches mee:{" "}
                <span className="font-mono">{voorstel.niet_toegewezen.join(", ")}</span>
              </span>
            </p>
          )}

          <Button variant="primary" disabled={bezig || aan.size === 0}
                  onClick={() => void maak()}>
            {bezig ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                   : <Scissors className="h-3.5 w-3.5" />}
            {aan.size} branch(es) maken en pushen
          </Button>
        </div>
      )}

      {uit && (
        <div className="rounded-md border border-border p-2 text-xs">
          {uit.resultaat.map((r) => (
            <p key={r.branch} className={r.ok ? "" : "text-destructive"}>
              <code className="font-mono">{r.branch}</code> — {r.melding}
            </p>
          ))}
        </div>
      )}
    </div>
  );
};
