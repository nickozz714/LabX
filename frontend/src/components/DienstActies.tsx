/**
 * DienstActies.tsx — bediening voor één vaste dienst in een cluster.
 *
 * Een vaste dienst is geen lab: er staat geen agent in, je kunt er niet in
 * chatten, en toch is hij juist het ding waar je wat MEE moet. Cosmos
 * leegmaken, opnieuw seeden, even kijken wat erin zit. Dat deed je tot nu toe
 * in een terminal naast LabX, met een commando dat je elke keer opzocht.
 *
 * DRIE KEUZES DIE DIT SCHERM VERKLAREN:
 *
 * - **Het model bedenkt de knoppen.** Welke handelingen zinnig zijn volgt uit
 *   het image: een Cosmos-emulator heeft andere knoppen dan een Redis. Een
 *   vaste lijst in LabX zou voor allebei verkeerd zijn.
 *
 * - **Het script staat in het volle zicht.** Een knop waarvan je niet kunt
 *   zien wat hij doet, druk je niet in — zeker niet als een model hem heeft
 *   geschreven. Vandaar "Toon script", en vandaar dat je hem mag bijstellen:
 *   een knop die JIJ hebt aangepast overschrijft het model daarna niet meer.
 *
 * - **De chat doet, hij adviseert niet.** Een antwoord van de vorm "voer dit
 *   even uit in je terminal" lost niets op — dan had je die terminal net zo
 *   goed meteen kunnen openen. Komt het model met een script, dan draait het,
 *   en de uitvoer staat eronder.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Loader2, Pencil, Play, Send, Sparkles, Terminal, Trash2, TriangleAlert, Wand2,
} from "lucide-react";
import { ApiError } from "@/lib/api";
import { clusterApi } from "@/lib/clusters";
import type { ClusterDienst, DienstActie } from "@/lib/clusters";
import { Badge, Button, EmptyState, Input, Label, Select, TextArea } from "@/components/ui";
import { useMelding } from "@/components/Meldingen";
import { useBevestiging } from "@/components/Bevestiging";

type Beurt = {
  rol: "mens" | "model";
  tekst: string;
  script?: string;
  uitvoer?: string;
  exitcode?: number | null;
  plek?: "in" | "naast";
  image?: string;
  label?: string;
  gevaarlijk?: boolean;
};

export function DienstActies({ dienst }: { dienst: ClusterDienst }) {
  const melding = useMelding();
  const bevestig = useBevestiging();

  const [acties, setActies] = useState<DienstActie[]>([]);
  const [laadt, setLaadt] = useState(true);
  const [denkt, setDenkt] = useState(false);
  const [bewerkt, setBewerkt] = useState<number | "nieuw" | null>(null);
  const [uitvoer, setUitvoer] = useState<{ id: number; tekst: string; exitcode: number } | null>(null);

  const laad = useCallback(async () => {
    try {
      setActies(await clusterApi.dienstActies(dienst.id));
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Knoppen niet op te halen");
    } finally {
      setLaadt(false);
    }
  }, [dienst.id, melding]);

  useEffect(() => { void laad(); }, [laad]);

  async function latenBedenken() {
    setDenkt(true);
    try {
      const uit = await clusterApi.dienstActiesVoorstellen(dienst.id);
      setActies(uit.acties);
      melding.ok(uit.toegevoegd > 0
        ? `${uit.toegevoegd} knop${uit.toegevoegd === 1 ? "" : "pen"} klaargezet.`
        : uit.toelichting || "Het model had geen knoppen voor deze dienst.");
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Het bedenken mislukte");
    } finally {
      setDenkt(false);
    }
  }

  async function draai(a: DienstActie) {
    if (a.gevaarlijk) {
      const ja = await bevestig.vraag({
        titel: `${a.label}?`,
        tekst: `${a.uitleg || "Dit draait het script hieronder."} Dit is niet terug te draaien.`,
        variant: "danger",
        bevestig: a.label,
      });
      if (!ja) return;
    }
    try {
      const uit = await clusterApi.dienstActieDraaien(a.id);
      setUitvoer({ id: a.id, tekst: uit.output || "(geen uitvoer)", exitcode: uit.exit_code });
      setActies((xs) => xs.map((x) => (x.id === a.id ? uit.actie : x)));
      if (uit.exit_code === 0) melding.ok(`'${a.label}' is gedraaid.`);
      else melding.fout(`'${a.label}' stopte met code ${uit.exit_code}. De uitvoer staat eronder.`);
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Dat lukte niet");
    }
  }

  async function weg(a: DienstActie) {
    const ja = await bevestig.vraag({
      titel: `Knop '${a.label}' weghalen?`,
      tekst: "Het script verdwijnt mee. Het model kan hem later opnieuw bedenken.",
      variant: "danger",
      bevestig: "Weghalen",
    });
    if (!ja) return;
    await clusterApi.dienstActieWeg(a.id);
    setActies((xs) => xs.filter((x) => x.id !== a.id));
  }

  if (laadt) {
    return (
      <p className="flex items-center gap-2 py-2 text-xs text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Knoppen ophalen…
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">
          Handelingen op deze dienst. Het model schrijft het script; je ziet het voordat je drukt.
        </p>
        <div className="flex gap-1">
          <Button variant="secondary" onClick={latenBedenken} disabled={denkt}>
            {denkt ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
            {denkt ? "Bezig met bedenken…" : "Laten bedenken"}
          </Button>
          <Button variant="ghost" onClick={() => setBewerkt("nieuw")}>
            <Pencil className="h-3.5 w-3.5" /> Zelf een knop
          </Button>
        </div>
      </div>

      {acties.length === 0 && bewerkt === null && (
        <EmptyState>
          Nog geen knoppen. Laat het model er bedenken bij <span className="font-mono">{dienst.image}</span>,
          of schrijf er zelf een.
        </EmptyState>
      )}

      {acties.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {acties.map((a) => (
            <Button
              key={a.id}
              variant={a.gevaarlijk ? "danger" : "secondary"}
              onClick={() => draai(a)}
              title={a.uitleg || a.label}
            >
              <Play className="h-3.5 w-3.5" /> {a.label}
            </Button>
          ))}
        </div>
      )}

      {uitvoer && (
        <Uitvoer
          tekst={uitvoer.tekst}
          exitcode={uitvoer.exitcode}
          onSluit={() => setUitvoer(null)}
        />
      )}

      {acties.length > 0 && (
        <details className="rounded-lg border border-border">
          <summary className="cursor-pointer px-3 py-2 text-xs text-muted-foreground">
            Wat deze knoppen doen ({acties.length})
          </summary>
          <ul className="space-y-2 border-t border-border p-3">
            {acties.map((a) => (
              <li key={a.id} className="rounded-lg border border-border p-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="text-sm font-medium">{a.label}</span>
                      <Badge tone={a.plek === "naast" ? "violet" : "neutral"}>
                        {a.plek === "naast" ? `naast · ${a.image}` : "in de dienst"}
                      </Badge>
                      {a.gevaarlijk && <Badge tone="red">onomkeerbaar</Badge>}
                      {a.bron === "zelf" && <Badge tone="green">van jou</Badge>}
                    </div>
                    {a.uitleg && <p className="mt-0.5 text-xs text-muted-foreground">{a.uitleg}</p>}
                    {a.laatst_gedraaid && (
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        laatst gedraaid: {new Date(a.laatst_gedraaid).toLocaleString("nl-NL")}
                        {a.laatste_exitcode !== null && ` — code ${a.laatste_exitcode}`}
                      </p>
                    )}
                  </div>
                  <div className="flex shrink-0 gap-1">
                    <Button variant="ghost" onClick={() => setBewerkt(a.id)} aria-label="Bijstellen">
                      <Pencil className="h-3.5 w-3.5" />
                    </Button>
                    <Button variant="ghost" onClick={() => weg(a)} aria-label="Weghalen">
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>
                <pre className="mt-2 max-h-40 overflow-auto rounded bg-muted/40 p-2 font-mono text-xs leading-relaxed">
                  {a.script}
                </pre>
              </li>
            ))}
          </ul>
        </details>
      )}

      {bewerkt !== null && (
        <KnopFormulier
          dienst={dienst}
          actie={bewerkt === "nieuw" ? null : acties.find((a) => a.id === bewerkt) || null}
          onKlaar={(a) => {
            setActies((xs) => (xs.some((x) => x.id === a.id)
              ? xs.map((x) => (x.id === a.id ? a : x))
              : [...xs, a]));
            setBewerkt(null);
          }}
          onAnnuleer={() => setBewerkt(null)}
        />
      )}

      <DienstChat dienst={dienst} onBewaard={(a) => setActies((xs) => [...xs, a])} />
    </div>
  );
}

/** De uitvoer van één actie. Een exitcode zonder uitleg zegt niets, dus staat
 *  erbij of het goed ging. */
function Uitvoer({ tekst, exitcode, onSluit }: {
  tekst: string; exitcode: number | null; onSluit: () => void;
}) {
  const goed = exitcode === 0;
  return (
    <div className={`rounded-lg border p-2 ${goed ? "border-border" : "border-destructive/50 bg-destructive/5"}`}>
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 text-xs font-medium">
          <Terminal className="h-3.5 w-3.5" />
          {goed ? "Gelukt" : `Gestopt met code ${exitcode}`}
        </span>
        <button onClick={onSluit} className="text-xs text-muted-foreground hover:text-foreground">
          sluiten
        </button>
      </div>
      <pre className="max-h-64 overflow-auto rounded bg-muted/40 p-2 font-mono text-xs leading-relaxed">
        {tekst}
      </pre>
    </div>
  );
}

/** Een knop zelf schrijven of een voorstel van het model bijstellen.
 *
 * Opslaan zet `bron` op "zelf", en dat is het hele punt: vanaf dat moment laat
 * een volgende modelronde hem met rust. Anders is je correctie één klik later
 * weer weg. */
function KnopFormulier({ dienst, actie, onKlaar, onAnnuleer }: {
  dienst: ClusterDienst;
  actie: DienstActie | null;
  onKlaar: (a: DienstActie) => void;
  onAnnuleer: () => void;
}) {
  const melding = useMelding();
  const [label, setLabel] = useState(actie?.label || "");
  const [uitleg, setUitleg] = useState(actie?.uitleg || "");
  const [plek, setPlek] = useState<"in" | "naast">(actie?.plek || "in");
  const [image, setImage] = useState(actie?.image || "python:3.12-slim");
  const [script, setScript] = useState(actie?.script || "");
  const [gevaarlijk, setGevaarlijk] = useState(actie?.gevaarlijk || false);
  const [bezig, setBezig] = useState(false);

  async function bewaar() {
    setBezig(true);
    try {
      const uit = await clusterApi.dienstActieZetten(dienst.id, {
        id: actie?.id, label, uitleg, plek, script, gevaarlijk,
        image: plek === "naast" ? image : "",
        bron: "zelf",
      });
      melding.ok("Knop bewaard.");
      onKlaar(uit);
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Bewaren mislukte");
    } finally {
      setBezig(false);
    }
  }

  return (
    <div className="space-y-3 rounded-lg border border-border p-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <Label>Naam op de knop</Label>
          <Input value={label} onChange={(e) => setLabel(e.target.value)}
                 placeholder="Cosmos leegmaken" />
        </div>
        <div>
          <Label>Waar draait het script?</Label>
          <Select value={plek} onChange={(e) => setPlek(e.target.value as "in" | "naast")}>
            <option value="in">In de dienst zelf</option>
            <option value="naast">In een container ernaast</option>
          </Select>
        </div>
      </div>
      <div>
        <Label>Eén zin uitleg</Label>
        <Input value={uitleg} onChange={(e) => setUitleg(e.target.value)}
               placeholder="Verwijdert alle databases in de emulator." />
      </div>
      {plek === "naast" ? (
        <div>
          <Label>Image van de hulpcontainer</Label>
          <Input value={image} onChange={(e) => setImage(e.target.value)}
                 placeholder="python:3.12-slim" />
          <p className="mt-1 text-xs text-muted-foreground">
            Hij staat op het netwerk van het cluster en bereikt deze dienst op naam. In het
            script zijn <span className="font-mono">$DIENST_HOST</span> en{" "}
            <span className="font-mono">$DIENST_POORT</span> al gevuld.
          </p>
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">
          Het script draait met <span className="font-mono">sh -c</span> in{" "}
          <span className="font-mono">{dienst.naam}</span> zelf — dus alleen gereedschap dat in{" "}
          <span className="font-mono">{dienst.image}</span> zit, werkt.
        </p>
      )}
      <div>
        <Label>Script</Label>
        <TextArea rows={8} value={script} onChange={(e) => setScript(e.target.value)}
                  className="font-mono text-xs"
                  placeholder="curl -s $DIENST_HOST:$DIENST_POORT/health" />
      </div>
      <label className="flex items-center gap-2 text-xs">
        <input type="checkbox" checked={gevaarlijk}
               onChange={(e) => setGevaarlijk(e.target.checked)} />
        Gooit data weg of overschrijft iets — vraag eerst om bevestiging
      </label>
      <div className="flex gap-2">
        <Button onClick={bewaar} disabled={bezig || !label.trim() || !script.trim()}>
          {bezig && <Loader2 className="h-4 w-4 animate-spin" />} Bewaren
        </Button>
        <Button variant="secondary" onClick={onAnnuleer}>Annuleren</Button>
      </div>
    </div>
  );
}

/** Een chat die wél iets doet.
 *
 * Alles wat geen knop verdient komt hier langs: "hoeveel documenten staan er
 * in?", "maak alleen die ene collectie leeg". Het model krijgt dezelfde
 * context als bij de knoppen, schrijft een script, LabX voert het uit, en de
 * uitvoer staat in het antwoord. Werkte het? Dan is het één klik om er een
 * knop van te maken. */
function DienstChat({ dienst, onBewaard }: {
  dienst: ClusterDienst;
  onBewaard: (a: DienstActie) => void;
}) {
  const melding = useMelding();
  const [beurten, setBeurten] = useState<Beurt[]>([]);
  const [vraag, setVraag] = useState("");
  const [bezig, setBezig] = useState(false);
  const onder = useRef<HTMLDivElement | null>(null);

  useEffect(() => { onder.current?.scrollIntoView({ behavior: "smooth" }); }, [beurten.length]);

  async function stuur() {
    const tekst = vraag.trim();
    if (!tekst || bezig) return;
    const geschiedenis = beurten.map((b) => ({ rol: b.rol, tekst: b.tekst }));
    setBeurten((xs) => [...xs, { rol: "mens", tekst }]);
    setVraag("");
    setBezig(true);
    try {
      const uit = await clusterApi.dienstChat(dienst.id, tekst, geschiedenis);
      setBeurten((xs) => [...xs, {
        rol: "model", tekst: uit.antwoord || "(geen antwoord)",
        script: uit.script, uitvoer: uit.uitvoer, exitcode: uit.exit_code,
        plek: uit.plek, image: uit.image, label: uit.label, gevaarlijk: uit.gevaarlijk,
      }]);
    } catch (e) {
      const melding_ = e instanceof ApiError ? e.message : "De chat liep vast";
      setBeurten((xs) => [...xs, { rol: "model", tekst: melding_ }]);
    } finally {
      setBezig(false);
    }
  }

  async function bewaarAlsKnop(b: Beurt) {
    if (!b.script) return;
    try {
      const a = await clusterApi.dienstActieZetten(dienst.id, {
        label: b.label || "Uit de chat",
        uitleg: b.tekst.slice(0, 200),
        plek: b.plek || "in",
        image: b.image || "",
        script: b.script,
        gevaarlijk: !!b.gevaarlijk,
        bron: "chat",
      });
      melding.ok(`'${a.label}' staat nu als knop.`);
      onBewaard(a);
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Bewaren mislukte");
    }
  }

  return (
    <div className="rounded-lg border border-border">
      <div className="flex items-center gap-1.5 border-b border-border px-3 py-2 text-xs font-medium">
        <Wand2 className="h-3.5 w-3.5 text-muted-foreground" />
        Vraag iets, of laat iets doen
      </div>

      {beurten.length > 0 && (
        <div className="max-h-96 space-y-3 overflow-auto p-3">
          {beurten.map((b, i) => (
            <div key={i} className={b.rol === "mens" ? "text-sm" : "space-y-2 text-sm"}>
              <p className={b.rol === "mens"
                ? "rounded-lg bg-muted/60 px-3 py-2"
                : ""}>
                {b.tekst}
              </p>
              {b.script && (
                <details className="rounded border border-border">
                  <summary className="cursor-pointer px-2 py-1 text-xs text-muted-foreground">
                    Dit is uitgevoerd {b.plek === "naast" ? `naast de dienst (${b.image})` : "in de dienst"}
                  </summary>
                  <pre className="max-h-40 overflow-auto border-t border-border p-2 font-mono text-xs">
                    {b.script}
                  </pre>
                </details>
              )}
              {b.uitvoer && (
                <pre className={`max-h-56 overflow-auto rounded p-2 font-mono text-xs leading-relaxed ${
                  b.exitcode === 0 ? "bg-muted/40" : "bg-destructive/5 text-destructive"}`}>
                  {b.uitvoer}
                </pre>
              )}
              {b.script && (
                <Button variant="ghost" onClick={() => bewaarAlsKnop(b)}>
                  <Pencil className="h-3.5 w-3.5" /> Hier een knop van maken
                </Button>
              )}
            </div>
          ))}
          <div ref={onder} />
        </div>
      )}

      <div className="flex items-start gap-2 border-t border-border p-2">
        <TextArea
          rows={2}
          value={vraag}
          onChange={(e) => setVraag(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void stuur(); }
          }}
          placeholder={`Bijvoorbeeld: maak ${dienst.naam} helemaal leeg`}
        />
        <Button onClick={stuur} disabled={bezig || !vraag.trim()}>
          {bezig ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
        </Button>
      </div>
      {bezig && (
        <p className="flex items-center gap-1.5 px-3 pb-2 text-xs text-muted-foreground">
          <TriangleAlert className="h-3.5 w-3.5" />
          Het model schrijft een script en LabX voert het meteen uit tegen {dienst.naam}.
        </p>
      )}
    </div>
  );
}
