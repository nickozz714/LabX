/**
 * SpraakPage — praten tegen LabX.
 *
 * Mobiel eerst: op een telefoon is de praatknop het grootste element op het
 * scherm en staat hij onderaan, binnen duimbereik. Het typveld staat erboven
 * als gelijkwaardige invoer, niet als noodoplossing — in een volle ruimte wil
 * je niet hardop vragen hoe het met een klantticket staat.
 *
 * Wat de opzet verder stuurt:
 * - Alles komt in één tijdlijn, zodat je achteraf kunt teruglezen wat er
 *   namens jou gebeurd is.
 * - Een schrijfactie vraagt altijd bevestiging, met een aftelbalk die je ook
 *   vanuit een ooghoek ziet leeglopen.
 * - Zolang je nog niets gevraagd hebt, staan er voorbeeldvragen om aan te
 *   tikken. Een leeg scherm met een microfoon vertelt je niet wat kan.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Mic, MicOff, Square, Send, Volume2, VolumeX, Loader2 } from "lucide-react";

import { Badge, Button, Card, EmptyState, Input } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { Opname, lees, spraakApi, zwijg } from "@/lib/spraak";
import { startRealtime } from "@/lib/spraakRealtime";
import type { RealtimeSessie } from "@/lib/spraakRealtime";
import type { SpraakGebeurtenis, SpraakSessie, SpraakStatus } from "@/lib/spraak";

/** Voorbeeldvragen voor een leeg gesprek. Bewust alleen dingen die altijd
 *  werken, dus niets dat van een bepaald ticket of bord uitgaat. */
const VOORBEELDEN = [
  "Wat loopt er nu?",
  "Wat staat er open?",
  "Wat staat er gepland?",
  "Welke labs draaien er?",
];

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
  const [rtOpnemen, setRtOpnemen] = useState(false);
  const [rtStatus, setRtStatus] = useState<string | null>(null);
  const isRealtime = sessie?.brein === "realtime";
  const openMicrofoon = status?.microfoon === "open";
  // Welke stem er voorleest, en bij welke sessie de kosten horen.
  const stemOpties = { tts: status?.tts, sessie: sessie?.id };

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
      const vol = await verversen(s.id);
      // Hardop, zodat meteen duidelijk is dat de sessie leeft. Voorlezen gaat
      // via de browser en kost niets.
      const groet = (vol.tijdlijn || []).find((e) => e.soort === "assistent");
      if (groet) lees(groet.tekst, geluid, { tts: status?.tts, sessie: s.id });
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
    setRtOpnemen(false);
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
      if (uit.antwoord) lees(uit.antwoord, geluid, stemOpties);
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
      setRtOpnemen(false);
      setRtStatus(null);
      return;
    }
    setBezig(true);
    setMelding(null);
    try {
      zwijg();   // het realtime-model praat zelf; niet er doorheen lezen
      realtime.current = await startRealtime(
        sessie.id,
        status?.microfoon === "open",
        {
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
      if (uit.melding) lees(uit.melding, geluid, stemOpties);
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
  const tijdlijn = sessie?.tijdlijn || [];
  // Alleen de begroeting: dan weet je nog niet wat je kunt vragen.
  const nogNietsGevraagd = !tijdlijn.some((e) => e.soort === "gebruiker");
  const vervalt = status?.verval_seconden || 20;

  return (
    <div className="flex h-full flex-col">
      {/* ── Kop ─────────────────────────────────────────────────────────── */}
      <div className="flex items-center gap-2 border-b border-border px-3 py-2.5 sm:px-6 sm:py-3">
        <h1 className="text-base font-semibold sm:text-lg">Spraak</h1>
        {sessie && (
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <span className="h-1.5 w-1.5 rounded-full bg-green-500" />
            <span className="hidden sm:inline">
              {status?.brein === "realtime" ? "realtime" : "pijplijn"}
            </span>
          </span>
        )}
        {rtStatus && <Badge tone={luistert ? "green" : "neutral"}>{rtStatus}</Badge>}

        <div className="flex-1" />

        {sessie && (
          <span className="hidden text-xs tabular-nums text-muted-foreground sm:inline">
            ${sessie.kosten_usd.toFixed(3)}
            {status?.vandaag_usd !== undefined
              && ` · $${status.vandaag_usd.toFixed(2)} vandaag`}
          </span>
        )}
        <Button variant="ghost" className="h-9 w-9 p-0"
                onClick={() => setGeluid((g) => !g)}
                aria-label={geluid ? "Voorlezen uitzetten" : "Voorlezen aanzetten"}
                title={geluid ? "Voorlezen uitzetten" : "Voorlezen aanzetten"}>
          {geluid ? <Volume2 size={16} /> : <VolumeX size={16} />}
        </Button>
        {sessie
          ? <Button variant="danger" className="h-9 text-xs" onClick={stopSessie}>
              Stoppen
            </Button>
          : <Button className="h-9 text-xs" onClick={startSessie}
                    disabled={bezig} busy={bezig}>
              Starten
            </Button>}
      </div>

      {melding && (
        <div className="mx-3 mt-2 rounded-md border border-border bg-muted/40 p-2 text-xs sm:mx-6">
          {melding}
        </div>
      )}

      {/* ── Gesprek ─────────────────────────────────────────────────────── */}
      {!sessie ? (
        <div className="flex flex-1 items-center justify-center p-4">
          <EmptyState>
            Start een sessie en vraag bijvoorbeeld “wat loopt er nu” of “hoe
            staat het met KRI-114”. Je kunt praten of typen.
          </EmptyState>
        </div>
      ) : (
        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto px-3 py-3 sm:px-6">
          {tijdlijn.map((e) => <Regel key={e.id} e={e} />)}

          {nogNietsGevraagd && (
            <div className="flex flex-wrap gap-2 pt-2">
              {VOORBEELDEN.map((v) => (
                <button
                  key={v}
                  onClick={() => stuur(v)}
                  disabled={bezig}
                  className="rounded-full border border-border px-3 py-1.5 text-xs
                             text-muted-foreground transition-colors
                             hover:bg-muted disabled:opacity-50"
                >
                  {v}
                </button>
              ))}
            </div>
          )}
          <div ref={onder} />
        </div>
      )}

      {/* ── Bevestiging ─────────────────────────────────────────────────── */}
      {sessie && openstaand && (
        <div className="mx-3 mb-2 overflow-hidden rounded-lg border border-warning/50 bg-warning/10 sm:mx-6">
          {/* De balk loopt leeg: dat zie je ook als je niet op de seconden let. */}
          <div className="h-1 bg-warning transition-[width] duration-500 ease-linear"
               style={{ width: `${Math.min(100, ((resterend ?? 0) / vervalt) * 100)}%` }} />
          <div className="p-3">
            <div className="text-sm font-medium">{openstaand.zin}</div>
            <div className="mt-3 flex items-center gap-2">
              {status?.bevestiging !== "spraak" && (
                <>
                  <Button className="h-11 flex-1 sm:h-9 sm:flex-none"
                          onClick={() => bevestig(true)} disabled={bezig}>
                    Ja, doen
                  </Button>
                  <Button variant="secondary" className="h-11 flex-1 sm:h-9 sm:flex-none"
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
              <span className="ml-auto text-sm font-semibold tabular-nums text-warning">
                {resterend ?? 0}s
              </span>
            </div>
          </div>
        </div>
      )}

      {/* ── Invoer ──────────────────────────────────────────────────────── */}
      {sessie && (
        <div className="veilig-onder border-t border-border px-3 py-3 sm:px-6">
          <div className="flex items-center gap-2">
            <Input
              value={tekst}
              placeholder="Typ je vraag…"
              onChange={(e) => setTekst(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") stuur(tekst); }}
              disabled={bezig}
            />
            <Button className="h-11 w-11 shrink-0 p-0"
                    onClick={() => stuur(tekst)}
                    disabled={bezig || !tekst.trim()}
                    aria-label="Versturen">
              <Send size={16} />
            </Button>
          </div>

          {/* De praatknop krijgt een hele regel: op een telefoon is dit het
              element dat je blind moet kunnen raken. */}
          <div className="mt-2">
            {isRealtime && (!luistert || openMicrofoon) ? (
              /* Open microfoon: een schakelaar, want hij blijft luisteren.
                 Nog niet verbonden: eerst de verbinding opzetten. */
              <Button
                variant={luistert ? "danger" : "secondary"}
                className={`h-12 w-full ${luistert ? "praat-puls" : ""}`}
                onClick={luisterenAanUit}
                disabled={bezig}
              >
                {bezig
                  ? <Loader2 size={18} className="animate-spin" />
                  : luistert ? <MicOff size={18} /> : <Mic size={18} />}
                {luistert ? "Stop met luisteren" : "Microfoon aanzetten"}
              </Button>
            ) : isRealtime ? (
              /* Verbonden met push-to-talk: OpenAI beslist dan niet zelf
                 wanneer je uitgesproken bent, dus houd je de knop vast. */
              <Button
                variant={rtOpnemen ? "danger" : "secondary"}
                className={`praatknop h-12 w-full ${rtOpnemen ? "praat-puls" : ""}`}
                onMouseDown={() => { setRtOpnemen(true); realtime.current?.beginBeurt(); }}
                onMouseUp={() => { setRtOpnemen(false); realtime.current?.eindBeurt(); }}
                onMouseLeave={rtOpnemen
                  ? () => { setRtOpnemen(false); realtime.current?.eindBeurt(); }
                  : undefined}
                onTouchStart={(e) => {
                  e.preventDefault(); setRtOpnemen(true); realtime.current?.beginBeurt();
                }}
                onTouchEnd={(e) => {
                  e.preventDefault(); setRtOpnemen(false); realtime.current?.eindBeurt();
                }}
                onTouchCancel={() => { setRtOpnemen(false); realtime.current?.eindBeurt(); }}
                onContextMenu={(e) => e.preventDefault()}
              >
                {rtOpnemen ? <Square size={18} /> : <Mic size={18} />}
                {rtOpnemen ? "Laat los om te versturen" : "Houd ingedrukt om te praten"}
              </Button>
            ) : (
              <Button
                variant={opnemen ? "danger" : "secondary"}
                className={`praatknop h-12 w-full ${opnemen ? "praat-puls" : ""}`}
                disabled={bezig && !opnemen}
                onMouseDown={knopIngedrukt}
                onMouseUp={knopLosgelaten}
                onMouseLeave={opnemen ? knopLosgelaten : undefined}
                // Op een telefoon moet het standaardgebaar (tekst selecteren,
                // deelmenu) wijken voor de knop, anders laat hij halverwege los.
                onTouchStart={(e) => { e.preventDefault(); void knopIngedrukt(); }}
                onTouchEnd={(e) => { e.preventDefault(); void knopLosgelaten(); }}
                onTouchCancel={() => { void knopLosgelaten(); }}
                onContextMenu={(e) => e.preventDefault()}
              >
                {bezig && !opnemen
                  ? <Loader2 size={18} className="animate-spin" />
                  : opnemen ? <Square size={18} /> : <Mic size={18} />}
                {opnemen ? "Laat los om te versturen" : "Houd ingedrukt om te praten"}
              </Button>
            )}
          </div>

          {/* Op een smal scherm past de kostenregel niet in de kop. */}
          <div className="mt-2 text-center text-[11px] tabular-nums text-muted-foreground sm:hidden">
            ${sessie.kosten_usd.toFixed(3)} deze sessie
            {status?.vandaag_usd !== undefined
              && ` · $${status.vandaag_usd.toFixed(2)} vandaag`}
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
        <div className="min-w-0 max-w-[85%] break-words rounded-2xl rounded-br-md bg-primary px-3.5 py-2 text-sm text-primary-foreground sm:max-w-2xl">
          {e.tekst}
        </div>
      </div>
    );
  }
  if (e.soort === "assistent") {
    return (
      <div className="flex justify-start">
        <div className="min-w-0 max-w-[85%] break-words rounded-2xl rounded-bl-md bg-secondary px-3.5 py-2 text-sm sm:max-w-2xl">
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
