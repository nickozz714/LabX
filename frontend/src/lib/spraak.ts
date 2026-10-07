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
  stemmen: () => api.get<string[]>("/voice/stemmen"),

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

/** Voorlezen met de browserstem. Gratis, direct, maar machinaal. */
function leesMetBrowser(tekst: string) {
  try {
    const synth = window.speechSynthesis;
    if (!synth) return;
    synth.cancel();
    const uiting = new SpeechSynthesisUtterance(tekst);
    uiting.lang = "nl-NL";
    uiting.rate = 1.05;
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
export function lees(tekst: string, aan = true,
                     opties: { tts?: string; sessie?: string } = {}) {
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
      huidigeAudio = audio;
      // De blob-URL weer vrijgeven; anders houdt elke zin geheugen vast.
      audio.onended = () => URL.revokeObjectURL(audio.src);
      await audio.play();
    } catch {
      leesMetBrowser(tekst);
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
export class Opname {
  private recorder: MediaRecorder | null = null;
  private brokken: Blob[] = [];
  private stream: MediaStream | null = null;

  async start(): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
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
}
