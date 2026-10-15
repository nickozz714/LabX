/**
 * lib/spraak.ts — API-client en browserhulp voor de spraakassistent.
 *
 * Twee dingen gebeuren hier bewust in de BROWSER en niet op de server:
 *
 * - **Voorlezen** met `speechSynthesis`. Gratis, direct, werkt op iOS, en het
 *   scheelt een TTS-model op een server die daar de kracht niet voor heeft.
 * - **Opnemen** met MediaRecorder. De audio gaat als één fragment naar de
 *   backend; die transcribeert hem.
 */
import { api, getToken } from "@/lib/api";

export type SpraakStatus = {
  aan: boolean;
  brein?: "realtime" | "pipeline";
  microfoon?: "ptt" | "open";
  bevestiging?: "klik" | "spraak" | "beide";
  woord?: string;
  meld_runs?: boolean;
  sessie_minuten?: number;
  dag_limiet_usd?: number;
  verval_seconden?: number;
  vandaag_usd?: number;
  tts?: "browser" | "openai";
};

/** Eén treffer in de geheugensteun: alleen naam, sleutel en toestand. */
export type SpraakVondst = {
  soort: "ticket" | "bord" | "lab" | "chat";
  sleutel: string | null;
  naam: string;
  detail: string | null;
  id: string;
};

export type SpraakGebeurtenis = {
  id: number;
  ts: string;
  soort: "gebruiker" | "assistent" | "actie" | "bevestiging" | "antwoord" | "fout" | "systeem";
  tekst: string;
  tool: string | null;
  parameters: Record<string, unknown> | null;
  resultaat: string | null;
  kosten_usd: number | null;
};

export type SpraakSessie = {
  id: string;
  brein: string;
  microfoon: string;
  status: string;
  kosten_usd: number;
  beurten: number;
  started_at: string;
  ended_at: string | null;
  einde_reden: string | null;
  tijdlijn?: SpraakGebeurtenis[];
  openstaand?: { id: string; zin: string; vervalt_op: string } | null;
};

export const spraakApi = {
  status: () => api.get<SpraakStatus>("/voice/status"),
  start: (payload?: { brein?: string; microfoon?: string }) =>
    api.post<SpraakSessie>("/voice/sessies", payload || {}),
  stop: (id: string) => api.post<SpraakSessie>(`/voice/sessies/${id}/stop`),
  haal: (id: string) => api.get<SpraakSessie>(`/voice/sessies/${id}`),
  zeg: (id: string, tekst: string) =>
    api.post<{ antwoord?: string; bevestiging?: unknown }>(
      `/voice/sessies/${id}/zeg`, { tekst }),
  bevestig: (id: string, akkoord: boolean) =>
    api.post<{ status: string; melding?: string }>(
      `/voice/sessies/${id}/bevestig`, { akkoord }),
  transcribeer: (audioBase64: string, mime: string) =>
    api.post<{ tekst: string }>("/voice/transcribeer",
                                { audio_base64: audioBase64, mime }),
  realtimeToken: () =>
    api.post<{ client_secret: string; model: string }>("/voice/realtime-token"),
  opzoeken: (q: string) =>
    api.get<SpraakVondst[]>(`/voice/opzoeken?q=${encodeURIComponent(q)}`),

  /** Voorgelezen audio. Bewust buiten `api` om: die verwacht JSON terug. */
  spreek: async (tekst: string, sessie?: string): Promise<Blob> => {
    const res = await fetch("/api/voice/spreek", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}),
      },
      body: JSON.stringify({ tekst, sessie }),
    });
    if (!res.ok) throw new Error(`Voorlezen mislukt (${res.status})`);
    return res.blob();
  },
};

/**
 * De beste Nederlandse stem die deze browser te bieden heeft.
 *
 * `getVoices()` pakte eerder gewoon de eerste Nederlandse stem, en dat is op
 * macOS meestal de compacte variant die als een antwoordapparaat klinkt. De
 * betere stemmen staan er wel bij, maar verderop in de lijst: ze heten
 * "Enhanced" of "Premium", of ze komen van het netwerk (`localService`
 * onwaar) zoals de Siri-stemmen.
 */
function besteStem(synth: SpeechSynthesis): SpeechSynthesisVoice | undefined {
  const nl = synth.getVoices().filter((v) => v.lang?.toLowerCase().startsWith("nl"));
  if (!nl.length) return undefined;
  const punten = (v: SpeechSynthesisVoice) => {
    const naam = v.name.toLowerCase();
    let p = 0;
    if (naam.includes("premium")) p += 4;
    if (naam.includes("enhanced")) p += 3;
    if (naam.includes("siri")) p += 3;
    if (!v.localService) p += 2;
    if (naam.includes("compact")) p -= 4;
    if (v.lang.toLowerCase() === "nl-nl") p += 1;   // niet nl-BE
    return p;
  };
  return [...nl].sort((a, b) => punten(b) - punten(a))[0];
}

let huidigeAudio: HTMLAudioElement | null = null;

/** Hoe snel de browserstem praat.
 *
 *  1.0 is het tempo van een voorleesstem en dat is te traag voor een
 *  assistent waar je een gesprek mee voert: je zit te wachten tot hij klaar
 *  is met iets wat je al weet. 1.25 leest als iemand die een beetje haast
 *  heeft; daarboven gaat de Nederlandse stem brouwen op samengestelde
 *  woorden ("achtergrondtaken", "beveiligingsprofiel"). */
const SPREEKTEMPO = 1.25;

/** Voorlezen met de browserstem. Gratis, direct, maar machinaal. */
function leesMetBrowser(tekst: string) {
  try {
    const synth = window.speechSynthesis;
    if (!synth) return;
    synth.cancel();
    const uiting = new SpeechSynthesisUtterance(tekst);
    uiting.lang = "nl-NL";
    uiting.rate = SPREEKTEMPO;
    const stem = besteStem(synth);
    if (stem) uiting.voice = stem;
    synth.speak(uiting);
  } catch {
    /* geen stem beschikbaar — de tekst staat op het scherm */
  }
}

/**
 * Voorlezen. Stil falen mag: niet kunnen praten is vervelend, maar het mag de
 * werking niet blokkeren — de tekst staat ook op het scherm.
 *
 * Staat de OpenAI-stem aan, dan haalt dit de audio bij de server op. Gaat dat
 * mis (geen sleutel, dagplafond, netwerk), dan valt hij terug op de
 * browserstem in plaats van te zwijgen.
 */
//: Of we de terugval al gemeld hebben. Eén keer per laadbeurt: elke zin zou
//: dezelfde melding geven en dan lees je hem niet meer.
let terugvalGemeld = false;

export function lees(tekst: string, aan = true,
                     opties: {
                       tts?: string; sessie?: string;
                       /** Wordt aangeroepen als de echte stem het niet deed en
                        *  hij terugvalt op de browserstem. */
                       opTerugval?: (reden: string) => void;
                     } = {}) {
  if (!aan || !tekst || typeof window === "undefined") return;
  if (opties.tts !== "openai") {
    leesMetBrowser(tekst);
    return;
  }
  void (async () => {
    try {
      const blob = await spraakApi.spreek(tekst, opties.sessie);
      zwijg();
      const audio = new Audio(URL.createObjectURL(blob));
      // Dezelfde reden als bij de browserstem, maar voorzichtiger: dit is een
      // echte stem en die gaat eerder klinken als een opgenomen bandje dat te
      // snel wordt afgespeeld.
      audio.playbackRate = 1.15;
      huidigeAudio = audio;
      // De blob-URL weer vrijgeven; anders houdt elke zin geheugen vast.
      audio.onended = () => URL.revokeObjectURL(audio.src);
      await audio.play();
    } catch (fout) {
      // Hier zat een stille verslechtering: de echte stem mislukte en je
      // kreeg de browserstem terug. Dat klinkt als "de assistent is ineens
      // machinaal geworden" en niet als "je sleutel is verlopen" -- terwijl
      // dat laatste is wat er aan de hand is, en het enige wat je kunt
      // oplossen. Eén keer melden is genoeg.
      leesMetBrowser(tekst);
      if (!terugvalGemeld) {
        terugvalGemeld = true;
        const reden = fout instanceof Error ? fout.message : String(fout);
        opties.opTerugval?.(
          /verlopen|expired|401|sleutel|api key/i.test(reden)
            ? "De OpenAI-stem doet het niet (de sleutel lijkt verlopen), dus je "
              + "hoort nu de browserstem. Zet een nieuwe sleutel bij Instellingen."
            : `De OpenAI-stem doet het niet, dus je hoort de browserstem. ${reden}`);
      }
    }
  })();
}

export function zwijg() {
  try {
    window.speechSynthesis?.cancel();
  } catch {
    /* niets aan de hand */
  }
  if (huidigeAudio) {
    try {
      huidigeAudio.pause();
    } catch {
      /* al gestopt */
    }
    huidigeAudio = null;
  }
}

/**
 * Opnemen zolang je de knop vasthoudt.
 *
 * Expres geen continue stream: één fragment per beurt is simpeler, goedkoper
 * en past bij push-to-talk. Voor de open microfoon doet het realtime-brein
 * zijn eigen ding via WebRTC.
 */
/**
 * De microfoon opvragen, en bij weigering zeggen WAAROM.
 *
 * Hier zat de val. `navigator.mediaDevices` bestaat helemaal niet op een
 * pagina die niet als veilig geldt -- en dat is elke http-pagina op een
 * IP-adres of hostnaam. De aanroep gooide dan een TypeError, die werd gevangen
 * als "de microfoon doet het niet; staat de toegang aan?", en dan sta je in de
 * browserinstellingen te zoeken naar een toestemming die er niet eens is.
 *
 * Alle vier de oorzaken hebben een andere oplossing, dus ze horen ook een
 * andere zin te krijgen.
 */
export async function vraagMicrofoon(): Promise<MediaStream> {
  const onveilig = (typeof window !== "undefined" && window.isSecureContext === false)
    || !navigator.mediaDevices?.getUserMedia;
  if (onveilig) {
    // Een browserregel, geen instelling van ons: zonder beveiligde context
    // bestaat `navigator.mediaDevices` niet eens. Dat is terecht op het
    // openbare web en onhandig op je eigen netwerk, dus hoort de melding te
    // zeggen wat je eraan DOET -- "gebruik https" alleen is een doodlopend
    // spoor op een server die nooit publiek wordt.
    const hier = typeof window !== "undefined" ? window.location.origin : "";
    throw new Error(
      `De browser staat de microfoon niet toe op ${hier}, omdat dat geen `
      + "beveiligde verbinding is. Drie manieren om dat op te lossen, van snel "
      + "naar netjes:\n"
      + `1. In Chrome: open chrome://flags/#unsafely-treat-insecure-origin-as-secure, `
      + `zet er ${hier} in, schakel het aan en herstart de browser. `
      + "Dat geldt alleen voor deze ene site op jouw computer.\n"
      + "2. Gebruik de LabX-desktopapp; die heeft dit probleem niet.\n"
      + "3. Zet https voor LabX (een certificaat van je eigen netwerk is genoeg).");
  }
  try {
    return await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    const naam = (err as DOMException)?.name;
    if (naam === "NotAllowedError" || naam === "SecurityError") {
      throw new Error(
        "De browser houdt de microfoon tegen. Geef deze site toegang via het "
        + "slotje in de adresbalk en probeer opnieuw.");
    }
    if (naam === "NotFoundError" || naam === "OverconstrainedError") {
      throw new Error("Ik vind hier geen microfoon.");
    }
    if (naam === "NotReadableError") {
      throw new Error(
        "De microfoon is in gebruik door een ander programma.");
    }
    throw err instanceof Error ? err : new Error(String(err));
  }
}

export class Opname {
  private recorder: MediaRecorder | null = null;
  private brokken: Blob[] = [];
  private stream: MediaStream | null = null;

  async start(): Promise<void> {
    this.stream = await vraagMicrofoon();
    this.brokken = [];
    // Safari kent webm niet; laat de browser zelf kiezen als dat zo is.
    const type = MediaRecorder.isTypeSupported("audio/webm")
      ? "audio/webm"
      : MediaRecorder.isTypeSupported("audio/mp4")
        ? "audio/mp4"
        : "";
    this.recorder = new MediaRecorder(this.stream, type ? { mimeType: type } : undefined);
    this.recorder.ondataavailable = (e) => {
      if (e.data.size > 0) this.brokken.push(e.data);
    };
    this.recorder.start();
  }

  /** Stopt en geeft het fragment als base64 terug. */
  stop(): Promise<{ base64: string; mime: string } | null> {
    return new Promise((resolve) => {
      const rec = this.recorder;
      if (!rec || rec.state === "inactive") return resolve(null);
      rec.onstop = () => {
        const mime = rec.mimeType || "audio/webm";
        const blob = new Blob(this.brokken, { type: mime });
        this.stream?.getTracks().forEach((t) => t.stop());
        this.stream = null;
        this.recorder = null;
        if (blob.size < 1200) return resolve(null);   // te kort: geen commando
        const lezer = new FileReader();
        lezer.onloadend = () => {
          const uit = String(lezer.result || "");
          resolve({ base64: uit.split(",")[1] || "", mime });
        };
        lezer.readAsDataURL(blob);
      };
      rec.stop();
    });
  }

  get loopt() {
    return this.recorder?.state === "recording";
  }

  /** De lopende stream, zodat de orb kan meebewegen met wat je zegt. */
  get bron(): MediaStream | null {
    return this.stream;
  }
}
