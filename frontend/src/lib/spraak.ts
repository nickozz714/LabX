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
import { api } from "@/lib/api";

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
  sleutel_aanwezig?: boolean;
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
};

/** Voorlezen via de browser. Stil falen mag: niet kunnen praten is vervelend,
 *  maar het mag de werking niet blokkeren — de tekst staat ook op het scherm. */
export function lees(tekst: string, aan = true) {
  if (!aan || !tekst || typeof window === "undefined") return;
  try {
    const synth = window.speechSynthesis;
    if (!synth) return;
    synth.cancel();
    const uiting = new SpeechSynthesisUtterance(tekst);
    uiting.lang = "nl-NL";
    uiting.rate = 1.05;
    const stem = synth.getVoices().find((v) => v.lang?.startsWith("nl"));
    if (stem) uiting.voice = stem;
    synth.speak(uiting);
  } catch {
    /* geen stem beschikbaar — de tekst staat op het scherm */
  }
}

export function zwijg() {
  try {
    window.speechSynthesis?.cancel();
  } catch {
    /* niets aan de hand */
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
