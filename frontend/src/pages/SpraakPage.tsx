/**
 * SpraakPage — praten tegen LabX.
 *
 * De opzet volgt wat we hebben afgesproken: je klikt bewust om een sessie te
 * starten, alles komt in één tijdlijn, en een schrijfactie vraagt altijd
 * bevestiging met een zichtbare aftelklok.
 *
 * Het typveld staat er niet als noodoplossing maar als gelijkwaardige invoer.
 * Je kunt de hele assistent gebruiken zonder microfoon — handig in een stille
 * ruimte, en het maakt de functie bruikbaar zonder OpenAI-sleutel.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Mic, MicOff, Square, Send, Volume2, VolumeX, Loader2 } from "lucide-react";

import { Badge, Button, Card, EmptyState, Input } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { Opname, lees, spraakApi, zwijg } from "@/lib/spraak";
import { startRealtime } from "@/lib/spraakRealtime";
import type { RealtimeSessie } from "@/lib/spraakRealtime";
import type { SpraakGebeurtenis, SpraakSessie, SpraakStatus } from "@/lib/spraak";

export function SpraakPage() {
  const [status, setStatus] = useState<SpraakStatus | null>(null);
  const [sessie, setSessie] = useState<SpraakSessie | null>(null);
  const [tekst, setTekst] = useState("");
  const [bezig, setBezig] = useState(false);
  const [opnemen, setOpnemen] = useState(false);
  const [geluid, setGeluid] = useState(true);
  const [melding, setMelding] = useState<string | null>(null);
  const [resterend, setResterend] = useState<number | null>(null);

  const opname = useRef(new Opname());
  const onder = useRef<HTMLDivElement | null>(null);

  // Het realtime-brein houdt een eigen verbinding open; die hoort bij deze
  // pagina en moet dus ook met de pagina mee verdwijnen.
  const realtime = useRef<RealtimeSessie | null>(null);
  const [luistert, setLuistert] = useState(false);
  const [rtStatus, setRtStatus] = useState<string | null>(null);
  const isRealtime = sessie?.brein === "realtime";

  useEffect(() => {
    spraakApi.status().then(setStatus).catch(() => setStatus({ aan: false }));
  }, []);

  const verversen = useCallback(async (id: string) => {
    const s = await spraakApi.haal(id);
    setSessie(s);
    return s;
  }, []);

  // De aftelklok van een openstaande bevestiging. Twintig seconden is kort
  // genoeg dat je moet kúnnen zien dat de klok loopt.
  useEffect(() => {
    const vervalt = sessie?.openstaand?.vervalt_op;
    if (!vervalt) {
      setResterend(null);
      return;
    }
    const tik = () => {
      const over = Math.max(0, Math.round(
        (new Date(vervalt).getTime() - Date.now()) / 1000));
      setResterend(over);
      if (over === 0 && sessie) verversen(sessie.id).catch(() => {});
    };
    tik();
    const t = setInterval(tik, 500);
    return () => clearInterval(t);
  }, [sessie?.openstaand?.vervalt_op, sessie, verversen]);

  useEffect(() => {
    onder.current?.scrollIntoView({ behavior: "smooth" });
  }, [sessie?.tijdlijn?.length]);

  // Een open microfoon die blijft luisteren nadat je weg navigeert is precies
  // wat je niet wilt.
  useEffect(() => () => {
    realtime.current?.stop();
    realtime.current = null;
  }, []);

  async function startSessie() {
    setBezig(true);
    setMelding(null);
    try {
      const s = await spraakApi.start({});
      await verversen(s.id);
    } catch (err) {
      setMelding(err instanceof ApiError ? err.message : "Sessie starten mislukt");
    } finally {
      setBezig(false);
    }
  }

  async function stopSessie() {
    if (!sessie) return;
    realtime.current?.stop();
    realtime.current = null;
    setLuistert(false);
    setRtStatus(null);
    zwijg();
    await spraakApi.stop(sessie.id).catch(() => {});
    setSessie(null);
  }

  async function stuur(wat: string) {
    if (!sessie || !wat.trim()) return;
    setBezig(true);
    setMelding(null);
    try {
      const uit = await spraakApi.zeg(sessie.id, wat.trim());
      setTekst("");
      const s = await verversen(sessie.id);
      if (uit.antwoord) lees(uit.antwoord, geluid);
      // Een bevestiging die net verlopen is, hoort niet stil te blijven.
      if (!s.openstaand && uit.bevestiging) await verversen(sessie.id);
    } catch (err) {
      setMelding(err instanceof ApiError ? err.message : "Er ging iets mis");
    } finally {
      setBezig(false);
    }
  }

  async function knopIngedrukt() {
    if (!sessie) return;
    try {
      await opname.current.start();
      setOpnemen(true);
      zwijg();   // niet tegen jezelf in praten
    } catch {
      setMelding("Ik kan de microfoon niet gebruiken. Staat die toegang aan?");
    }
  }

  async function knopLosgelaten() {
    if (!opname.current.loopt) return;
    setOpnemen(false);
    setBezig(true);
    try {
      const fragment = await opname.current.stop();
      if (!fragment) {
        setMelding("Dat was te kort om te verstaan.");
        return;
      }
      const { tekst: gezegd } = await spraakApi.transcribeer(
        fragment.base64, fragment.mime);
      if (!gezegd.trim()) {
        setMelding("Ik heb niets verstaan.");
        return;
      }
      await stuur(gezegd);
    } catch (err) {
      setMelding(err instanceof ApiError ? err.message : "Verstaan mislukt");
    } finally {
      setBezig(false);
    }
  }

  async function luisterenAanUit() {
    if (!sessie) return;
    if (realtime.current) {
      realtime.current.stop();
      realtime.current = null;
      setLuistert(false);
      setRtStatus(null);
      return;
    }
    setBezig(true);
    setMelding(null);
    try {
      zwijg();   // het realtime-model praat zelf; niet er doorheen lezen
      realtime.current = await startRealtime(sessie.id, {
        onStatus: setRtStatus,
        onVeranderd: () => { void verversen(sessie.id); },
        onFout: (f) => setMelding(f instanceof Error ? f.message : String(f)),
      });
      setLuistert(true);
    } catch (err) {
      setMelding(err instanceof ApiError || err instanceof Error
        ? err.message : "De verbinding kwam niet tot stand");
    } finally {
      setBezig(false);
    }
  }

  async function bevestig(akkoord: boolean) {
    if (!sessie) return;
    setBezig(true);
    try {
      const uit = await spraakApi.bevestig(sessie.id, akkoord);
      await verversen(sessie.id);
      if (uit.melding) lees(uit.melding, geluid);
    } finally {
      setBezig(false);
    }
  }

  if (status && !status.aan) {
    return (
      <div className="veilig-onder p-4 sm:p-6">
        <EmptyState>
          De spraakassistent staat uit. Zet hem aan bij Instellingen — je hebt er
          een OpenAI-sleutel voor nodig.
        </EmptyState>
      </div>
    );
  }

  const openstaand = sessie?.openstaand;
  const kanSpreken = Boolean(status?.sleutel_aanwezig);

  return (
    <div className="veilig-onder flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-3 sm:px-6">
        <h1 className="text-lg font-semibold">Spraak</h1>
        {sessie && <Badge tone="green">sessie loopt</Badge>}
        {status?.brein && <Badge>{status.brein === "realtime" ? "realtime" : "pijplijn"}</Badge>}
        {rtStatus && <Badge tone={luistert ? "green" : "neutral"}>{rtStatus}</Badge>}
        <div className="flex-1" />
        {sessie && (
          <span className="text-xs text-muted-foreground">
            ${sessie.kosten_usd.toFixed(3)} deze sessie
            {status?.vandaag_usd !== undefined && ` · $${status.vandaag_usd.toFixed(2)} vandaag`}
          </span>
        )}
        <Button variant="ghost" className="text-xs" onClick={() => setGeluid((g) => !g)}
                title={geluid ? "Voorlezen uitzetten" : "Voorlezen aanzetten"}>
          {geluid ? <Volume2 size={15} /> : <VolumeX size={15} />}
        </Button>
        {sessie
          ? <Button variant="danger" className="text-xs" onClick={stopSessie}>Stoppen</Button>
          : <Button className="text-xs" onClick={startSessie} disabled={bezig} busy={bezig}>
              Sessie starten
            </Button>}
      </div>

      {melding && (
        <div className="mx-3 mt-3 rounded-md border border-border bg-muted/40 p-2 text-xs sm:mx-6">
          {melding}
        </div>
      )}

      {!sessie ? (
        <div className="flex flex-1 items-center justify-center p-4">
          <EmptyState>
            Start een sessie en vraag bijvoorbeeld “wat loopt er nu” of “hoe staat
            het met KRI-114”. Je kunt praten of typen.
          </EmptyState>
        </div>
      ) : (
        <div className="flex-1 space-y-2 overflow-y-auto p-3 sm:p-6">
          {(sessie.tijdlijn || []).map((e) => <Regel key={e.id} e={e} />)}
          <div ref={onder} />
        </div>
      )}

      {sessie && openstaand && (
        <div className="mx-3 mb-2 rounded-md border border-warning/50 bg-warning/10 p-3 sm:mx-6">
          <div className="text-sm font-medium">{openstaand.zin}</div>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {status?.bevestiging !== "spraak" && (
              <>
                <Button className="text-xs" onClick={() => bevestig(true)} disabled={bezig}>
                  Ja, doen
                </Button>
                <Button variant="secondary" className="text-xs"
                        onClick={() => bevestig(false)} disabled={bezig}>
                  Nee
                </Button>
              </>
            )}
            {status?.bevestiging !== "klik" && (
              <span className="text-xs text-muted-foreground">
                of zeg “{status?.woord}”
              </span>
            )}
            <span className="ml-auto text-xs font-semibold tabular-nums text-warning">
              {resterend ?? 0}s
            </span>
          </div>
        </div>
      )}

      {sessie && (
        <div className="veilig-onder border-t border-border p-3 sm:px-6">
          <div className="flex flex-col gap-2 sm:flex-row">
            <Input
              value={tekst}
              placeholder="Typ wat je wilt vragen…"
              onChange={(e) => setTekst(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") stuur(tekst); }}
              disabled={bezig}
            />
            <div className="flex items-stretch gap-2">
              <Button onClick={() => stuur(tekst)} disabled={bezig || !tekst.trim()}>
                <Send size={15} /> Stuur
              </Button>
              {/* Bij het realtime-brein luistert de microfoon continu, dus
                  daar hoort een schakelaar. Bij de pijplijn houd je de knop
                  ingedrukt: dan weet je zeker wanneer hij meeluistert. */}
              {isRealtime ? (
                <Button
                  variant={luistert ? "danger" : "secondary"}
                  onClick={luisterenAanUit}
                  disabled={bezig || !kanSpreken}
                  title={kanSpreken
                    ? "Open microfoon aan- of uitzetten"
                    : "Er staat geen OpenAI-sleutel ingesteld"}
                >
                  {bezig
                    ? <Loader2 size={15} className="animate-spin" />
                    : luistert ? <MicOff size={15} /> : <Mic size={15} />}
                  {luistert ? "Stop luisteren" : "Luisteren"}
                </Button>
              ) : (
              <Button
                variant={opnemen ? "danger" : "secondary"}
                disabled={bezig && !opnemen}
                title={kanSpreken
                  ? "Houd ingedrukt om te praten"
                  : "Er staat geen OpenAI-sleutel ingesteld; typen werkt wel"}
                onMouseDown={kanSpreken ? knopIngedrukt : undefined}
                onMouseUp={kanSpreken ? knopLosgelaten : undefined}
                onMouseLeave={opnemen ? knopLosgelaten : undefined}
                onTouchStart={kanSpreken ? knopIngedrukt : undefined}
                onTouchEnd={kanSpreken ? knopLosgelaten : undefined}
              >
                {bezig && !opnemen
                  ? <Loader2 size={15} className="animate-spin" />
                  : opnemen ? <Square size={15} /> : <Mic size={15} />}
                {opnemen ? "Loslaten" : "Praten"}
              </Button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Regel({ e }: { e: SpraakGebeurtenis }) {
  const tijd = e.ts.slice(11, 19);

  if (e.soort === "gebruiker") {
    return (
      <div className="flex justify-end">
        <div className="min-w-0 max-w-[92%] break-words rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground sm:max-w-2xl">
          {e.tekst}
        </div>
      </div>
    );
  }
  if (e.soort === "assistent") {
    return (
      <div className="flex justify-start">
        <div className="min-w-0 max-w-[92%] break-words rounded-lg bg-secondary px-3 py-2 text-sm sm:max-w-2xl">
          {e.tekst}
        </div>
      </div>
    );
  }
  if (e.soort === "bevestiging") {
    return (
      <Card className="border-warning/40 bg-warning/5 p-2 text-xs">
        <span className="font-medium">Gevraagd om bevestiging:</span> {e.tekst}
      </Card>
    );
  }
  if (e.soort === "actie") {
    return (
      <div className="flex items-start gap-2 px-1 text-[11px] text-muted-foreground">
        <span className="tabular-nums">{tijd}</span>
        <span className="font-mono">{e.tool}</span>
        {e.resultaat && <span className="min-w-0 flex-1 break-words">— {e.resultaat.slice(0, 160)}</span>}
      </div>
    );
  }
  return (
    <div className="px-1 text-[11px] text-muted-foreground">
      <span className="tabular-nums">{tijd}</span> {e.tekst}
    </div>
  );
}
