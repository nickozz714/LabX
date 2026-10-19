/**
 * GrillMeister.tsx — je ticket op de rooster.
 *
 * Een ticket dat je zelf schrijft lijkt altijd duidelijk. Wat erin ontbreekt
 * zie je pas als iemand anders het leest — of erger, pas als het half gebouwd
 * is en de vraag "en wat als er al iets gepubliceerd is?" alsnog gesteld
 * wordt. Dat moment haal je hiermee naar voren.
 *
 * DRIE KEUZES DIE DIT SCHERM VERKLAREN:
 *
 * - **Grillen levert vragen op, geen nieuwe tekst.** Een agent die meteen je
 *   ticket herschrijft haalt juist het denkwerk weg dat het ticket nodig
 *   heeft. Pas als jij om een voorstel vraagt, komt er tekst — en die staat
 *   er dan nog steeds niet op tot je op "Overnemen" drukt.
 *
 * - **De klantversie is een aparte knop.** Dezelfde inhoud, maar voor iemand
 *   van buiten en zonder één verwijzing naar wat hier draait. Dat is een
 *   ander stuk schrijfwerk dan een ticket, geen variant erop.
 *
 * - **De controle staat in beeld, ook als hij goed gaat.** "Schoon" naast de
 *   klanttekst is het bewijs dat er gekeken is. Zonder dat zie je alleen bij
 *   een fout dat de controle bestaat, en dan vertrouw je hem niet.
 */
import { useRef, useState } from "react";
import {
  AlertTriangle, Check, Copy, Flame, Loader2, Send, ShieldCheck, Sparkles, UserRound,
} from "lucide-react";
import { ApiError } from "@/lib/api";
import { boardApi } from "@/lib/boards";
import type { GrillBeurt } from "@/lib/boards";
import type { TicketDto } from "@/lib/types";
import { Badge, Button, EmptyState, TextArea } from "@/components/ui";
import { useMelding } from "@/components/Meldingen";

type Beurt = { rol: "mens" | "model"; tekst: string; uitslag?: GrillBeurt };

/** Waar de grillmeister naar kijkt.
 *
 * Een bestaand ticket (`ticket`) of een concept dat nog in het formulier
 * staat (`concept`). Eén component voor allebei, want het verschil zit alleen
 * in wáár de tekst vandaan komt en wat "overnemen" betekent: bij een ticket
 * een PATCH, bij een concept het formulier vullen. Twee componenten zouden
 * langzaam uit elkaar lopen -- de ene met de klantknop, de andere zonder.
 */
export type GrillDoel =
  | { soort: "ticket"; ticket: TicketDto }
  | {
      soort: "concept";
      titel: string;
      description: string;
      acceptance_criteria: string;
      /** Het voorstel in het formulier zetten. */
      vul: (v: { titel: string; description: string; acceptance_criteria: string }) => void;
    };

export function GrillMeister({ boardId, doel, onBijgewerkt }: {
  boardId: number;
  doel: GrillDoel;
  onBijgewerkt?: () => void;
}) {
  const melding = useMelding();
  const [beurten, setBeurten] = useState<Beurt[]>([]);
  const [bericht, setBericht] = useState("");
  const [bezig, setBezig] = useState<"grillen" | "voorstel" | "klantversie" | null>(null);
  const [gekopieerd, setGekopieerd] = useState(false);
  const onder = useRef<HTMLDivElement | null>(null);

  async function vraag(modus: "grillen" | "voorstel" | "klantversie") {
    const tekst = bericht.trim();
    const geschiedenis = beurten.map((b) => ({ rol: b.rol, tekst: b.tekst }));
    if (tekst) setBeurten((xs) => [...xs, { rol: "mens", tekst }]);
    setBericht("");
    setBezig(modus);
    try {
      const uit = doel.soort === "ticket"
        ? await boardApi.grill(boardId, doel.ticket.id, {
            bericht: tekst, modus, geschiedenis,
          })
        : await boardApi.grillConcept(boardId, {
            titel: doel.titel,
            description: doel.description,
            acceptance_criteria: doel.acceptance_criteria,
            bericht: tekst, modus, geschiedenis,
          });
      setBeurten((xs) => [...xs, {
        rol: "model",
        tekst: uit.antwoord || (uit.klanttekst ? "" : "(geen antwoord)"),
        uitslag: uit,
      }]);
      setTimeout(() => onder.current?.scrollIntoView({ behavior: "smooth" }), 50);
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "De grillmeister liep vast");
    } finally {
      setBezig(null);
    }
  }

  async function neemOver(v: NonNullable<GrillBeurt["voorstel"]>) {
    // Bij een concept bestaat er niets om naar te schrijven: het voorstel
    // gaat het formulier in, en jij drukt daarna zelf op Aanmaken.
    if (doel.soort === "concept") {
      doel.vul({ titel: v.titel, description: v.description,
                 acceptance_criteria: v.acceptance_criteria });
      melding.ok("Het voorstel staat in het formulier.");
      return;
    }
    try {
      await boardApi.grillOvernemen(boardId, doel.ticket.id, {
        titel: v.titel, description: v.description,
        acceptance_criteria: v.acceptance_criteria,
      });
      melding.ok("Het ticket is bijgewerkt.");
      onBijgewerkt?.();
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Overnemen mislukte");
    }
  }

  // Een concept zonder titel én zonder omschrijving valt niets over te
  // zeggen. De knoppen uit zetten is eerlijker dan een foutmelding na een
  // modelaanroep die een halve minuut duurde.
  const genoeg = doel.soort === "ticket"
    || !!(doel.titel.trim() || doel.description.trim());

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Flame className="h-4 w-4 text-amber-500" />
        <span className="text-sm font-semibold">Grillmeister</span>
        <span className="text-xs text-muted-foreground">
          Stelt de vragen die dit ticket nu nog niet beantwoordt.
        </span>
      </div>

      {beurten.length === 0 && (
        <EmptyState>
          {genoeg
            ? <>Nog niets gevraagd. Begin met <span className="font-medium">Grillen</span> — dan
                krijg je de lastigste vragen terug, zonder dat er iets aan verandert.</>
            : "Typ eerst een titel of een omschrijving; dan valt er wat te vragen."}
        </EmptyState>
      )}

      {beurten.map((b, i) => (
        <div key={i} className="space-y-2">
          {b.rol === "mens" ? (
            <p className="rounded-lg bg-muted/60 px-3 py-2 text-sm">{b.tekst}</p>
          ) : (
            <div className="space-y-2">
              {b.tekst && <p className="text-sm">{b.tekst}</p>}

              {!!b.uitslag?.vragen?.length && (
                <ol className="list-decimal space-y-1 rounded-lg border border-border p-3 pl-7 text-sm">
                  {b.uitslag.vragen.map((v, j) => <li key={j}>{v}</li>)}
                </ol>
              )}

              {!!b.uitslag?.gaten?.length && (
                <p className="flex flex-wrap items-center gap-1.5 text-xs">
                  <span className="text-muted-foreground">Ontbreekt:</span>
                  {b.uitslag.gaten.map((g, j) => <Badge key={j} tone="yellow">{g}</Badge>)}
                </p>
              )}

              {b.uitslag?.voorstel && (
                <div className="rounded-lg border border-border">
                  <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
                    <span className="text-xs font-medium">Voorstel</span>
                    <Button onClick={() => neemOver(b.uitslag!.voorstel!)}>
                      <Check className="h-3.5 w-3.5" />
                      {doel.soort === "concept" ? "In het formulier zetten" : "Overnemen"}
                    </Button>
                  </div>
                  <div className="space-y-2 p-3">
                    {b.uitslag.voorstel.titel && (
                      <p className="text-sm font-medium">{b.uitslag.voorstel.titel}</p>
                    )}
                    <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded bg-muted/40 p-2 text-xs leading-relaxed">
                      {b.uitslag.voorstel.description}
                    </pre>
                    {b.uitslag.voorstel.acceptance_criteria && (
                      <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded bg-muted/40 p-2 text-xs leading-relaxed">
                        {b.uitslag.voorstel.acceptance_criteria}
                      </pre>
                    )}
                  </div>
                </div>
              )}

              {b.uitslag?.klanttekst && (
                <div className={`rounded-lg border ${
                  b.uitslag.controle?.schoon ? "border-border" : "border-destructive/50"}`}>
                  <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2">
                    <span className="flex items-center gap-1.5 text-xs font-medium">
                      <UserRound className="h-3.5 w-3.5" /> Voor de klant
                    </span>
                    {b.uitslag.controle?.schoon ? (
                      <span className="flex items-center gap-1 text-xs text-green-600 dark:text-green-400">
                        <ShieldCheck className="h-3.5 w-3.5" /> niets interns gevonden
                      </span>
                    ) : (
                      <span className="flex items-center gap-1 text-xs text-destructive">
                        <AlertTriangle className="h-3.5 w-3.5" />
                        {b.uitslag.controle?.bevindingen.length} interne verwijzing(en)
                      </span>
                    )}
                  </div>
                  <p className="whitespace-pre-wrap p-3 text-sm">{b.uitslag.klanttekst}</p>
                  {!b.uitslag.controle?.schoon && (
                    <ul className="border-t border-border px-3 py-2 text-xs text-destructive">
                      {b.uitslag.controle?.bevindingen.map((x, j) => (
                        <li key={j}>{x.soort}: <span className="font-mono">{x.gevonden}</span></li>
                      ))}
                    </ul>
                  )}
                  <div className="border-t border-border px-3 py-2">
                    <Button
                      variant="secondary"
                      disabled={!b.uitslag.controle?.schoon}
                      title={b.uitslag.controle?.schoon
                        ? "Naar het klembord"
                        : "Eerst de interne verwijzingen eruit — vraag een nieuwe versie"}
                      onClick={async () => {
                        await navigator.clipboard.writeText(b.uitslag!.klanttekst);
                        setGekopieerd(true);
                        setTimeout(() => setGekopieerd(false), 2000);
                      }}
                    >
                      {gekopieerd ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                      {gekopieerd ? "Gekopieerd" : "Kopiëren"}
                    </Button>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      ))}
      <div ref={onder} />

      <TextArea
        rows={2}
        value={bericht}
        onChange={(e) => setBericht(e.target.value)}
        placeholder="Antwoord op zijn vragen, of zeg waar hij op moet letten."
      />
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => vraag("grillen")} disabled={bezig !== null || !genoeg}>
          {bezig === "grillen" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Flame className="h-4 w-4" />}
          Grillen
        </Button>
        <Button variant="secondary" onClick={() => vraag("voorstel")} disabled={bezig !== null || !genoeg}>
          {bezig === "voorstel" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
          Voorstel schrijven
        </Button>
        <Button variant="secondary" onClick={() => vraag("klantversie")} disabled={bezig !== null || !genoeg}>
          {bezig === "klantversie" ? <Loader2 className="h-4 w-4 animate-spin" /> : <UserRound className="h-4 w-4" />}
          Versie voor de klant
        </Button>
        {bericht.trim() && bezig === null && (
          <Button variant="ghost" onClick={() => vraag("grillen")}>
            <Send className="h-4 w-4" /> Alleen sturen
          </Button>
        )}
      </div>
      {bezig && (
        <p className="text-xs text-muted-foreground">
          Dit duurt een halve tot twee minuten — hij leest het ticket en de rest van het bord.
        </p>
      )}
    </div>
  );
}
