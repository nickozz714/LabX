/**
 * spraakRealtime.ts — het realtime-brein, vanuit de browser.
 *
 * De browser praat rechtstreeks met OpenAI over WebRTC; dat is wat realtime
 * snel maakt. De server geeft daar een kortlevend token voor af, zodat de
 * echte sleutel nooit in de paginabron komt.
 *
 * Twee dingen zijn hier bewust anders dan in een gewone realtime-opzet:
 *
 * 1. **Toolaanroepen gaan naar LabX, niet naar OpenAI.** Het model vraagt om
 *    een actie, de server voert die uit (of vraagt bevestiging) en het
 *    resultaat gaat terug het gesprek in. Daarmee loopt ook dit brein langs
 *    dezelfde bevestigingslus als het pijplijn-brein.
 *
 * 2. **Het model bevestigt niet.** Hoort de gebruiker iets te bevestigen, dan
 *    stuurt de BROWSER de letterlijke transcriptie naar de server, die hem
 *    tegen het bevestigingswoord legt. Zou het model mogen beslissen of er
 *    bevestigd is, dan was het bevestigingswoord een formaliteit.
 */
import { getToken } from "@/lib/api";

const OPENAI_CALLS = "https://api.openai.com/v1/realtime/calls";

/** Korter dan dit is een tik, geen zin. OpenAI weigert zulke korte beurten. */
const MINIMALE_BEURT_MS = 350;

export type RealtimeMeldingen = {
  onStatus?: (status: string) => void;
  /** Er is iets gebeurd dat de tijdlijn moet verversen. */
  onVeranderd?: () => void;
  onFout?: (fout: unknown) => void;
  /** Wat de gebruiker zei, zodra het verstaan is. */
  onGezegd?: (tekst: string) => void;
};

export type RealtimeSessie = {
  stop: () => void;
  demp: (uit: boolean) => void;
  /** Push-to-talk: ingedrukt. Alleen zinvol als de microfoon niet openstaat. */
  beginBeurt: () => void;
  /** Push-to-talk: losgelaten — sluit de beurt af en vraagt om antwoord. */
  eindBeurt: () => void;
  /** Staat de microfoon continu open (dan bepaalt OpenAI de beurten)? */
  readonly openMicrofoon: boolean;
};

/** Een zin uit het realtime-gesprek vastleggen, zodat de tijdlijn klopt. */
async function labxTranscript(sessieId: string, rol: "gebruiker" | "assistent",
                              tekst: string) {
  if (!tekst.trim()) return;
  try {
    await fetch(`/api/voice/sessies/${sessieId}/transcript`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}),
      },
      body: JSON.stringify({ rol, tekst }),
    });
  } catch {
    /* de tijdlijn mag het gesprek niet laten vastlopen */
  }
}

async function labxTool(sessieId: string, naam: string, args: unknown) {
  const res = await fetch(`/api/voice/sessies/${sessieId}/tool`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}),
    },
    body: JSON.stringify({ tool: naam, args: args || {} }),
  });
  const tekst = await res.text();
  const data = tekst ? JSON.parse(tekst) : null;
  if (!res.ok) {
    const detail = (data && (data.detail || data.error)) || res.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

/** De gesproken reactie op een openstaande bevestiging, naar de server. */
async function labxAntwoord(sessieId: string, tekst: string) {
  const res = await fetch(`/api/voice/sessies/${sessieId}/zeg`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}),
    },
    body: JSON.stringify({ tekst }),
  });
  const ruw = await res.text();
  return ruw ? JSON.parse(ruw) : null;
}

function stuur(dc: RTCDataChannel, bericht: unknown) {
  if (dc.readyState === "open") dc.send(JSON.stringify(bericht));
}

/** Een toolresultaat terug het gesprek in, en laat het model erop reageren. */
function stuurToolUitkomst(dc: RTCDataChannel, callId: string, uitkomst: unknown) {
  stuur(dc, {
    type: "conversation.item.create",
    item: { type: "function_call_output", call_id: callId, output: JSON.stringify(uitkomst) },
  });
  stuur(dc, { type: "response.create" });
}

/** Een mededeling van het systeem, die het model moet voorlezen. */
function stuurMededeling(dc: RTCDataChannel, tekst: string) {
  stuur(dc, {
    type: "conversation.item.create",
    item: {
      type: "message",
      role: "system",
      content: [{ type: "input_text", text: `Zeg dit tegen de gebruiker: ${tekst}` }],
    },
  });
  stuur(dc, { type: "response.create" });
}

export async function startRealtime(
  sessieId: string,
  openMicrofoon: boolean,
  meld: RealtimeMeldingen = {},
): Promise<RealtimeSessie> {
  meld.onStatus?.("Microfoon aanvragen…");
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

  // Bij push-to-talk staat de server-VAD uit: OpenAI beslist dan niet zelf
  // wanneer je uitgesproken bent, dus moet de browser elke beurt afsluiten.
  // Tot die tijd gaat het spoor op stil, anders stroomt de hele kamer mee.
  const zetMicrofoon = (aan: boolean) => {
    for (const t of stream.getAudioTracks()) t.enabled = aan;
  };
  if (!openMicrofoon) zetMicrofoon(false);
  let begonnenOp = 0;

  const opruimen = () => {
    for (const t of stream.getTracks()) t.stop();
  };

  meld.onStatus?.("Sessie aanvragen…");
  let token: { client_secret?: string };
  try {
    const res = await fetch("/api/voice/realtime-token", {
      method: "POST",
      headers: { ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}) },
    });
    const ruw = await res.text();
    token = ruw ? JSON.parse(ruw) : {};
    if (!res.ok) throw new Error((token as any)?.detail || res.statusText);
  } catch (fout) {
    opruimen();
    throw fout;
  }
  if (!token.client_secret) {
    opruimen();
    throw new Error("De server gaf geen realtime-token terug.");
  }

  const pc = new RTCPeerConnection();
  const audio = document.createElement("audio");
  audio.autoplay = true;
  audio.style.display = "none";
  document.body.appendChild(audio);
  pc.ontrack = (e) => { audio.srcObject = e.streams[0]; };
  for (const track of stream.getAudioTracks()) pc.addTrack(track, stream);

  const dc = pc.createDataChannel("oai-events");

  // Eén toolaanroep kan via twee events binnenkomen; twee keer uitvoeren van
  // een schrijfactie is precies wat je niet wilt.
  const gedaan = new Set<string>();
  const nogNiet = (id?: string) => {
    if (!id) return true;
    if (gedaan.has(id)) return false;
    gedaan.add(id);
    return true;
  };

  // Staat er een bevestiging open, dan is de volgende zin van de gebruiker
  // daarvoor bedoeld en gaat hij naar de server, niet naar het model.
  let wachtOpBevestiging = false;

  async function voerUit(naam: string, args: unknown, callId: string) {
    try {
      const uit = await labxTool(sessieId, naam, args);
      if (uit && uit.wacht_op_bevestiging) wachtOpBevestiging = true;
      meld.onVeranderd?.();
      stuurToolUitkomst(dc, callId, uit);
    } catch (fout) {
      meld.onFout?.(fout);
      stuurToolUitkomst(dc, callId, {
        ok: false, fout: fout instanceof Error ? fout.message : String(fout),
      });
    }
  }

  async function gebruikerZei(tekst: string) {
    meld.onGezegd?.(tekst);
    // Eerst vastleggen: ook een zin die nergens toe leidt hoort terug te
    // lezen te zijn. Zonder dit staat er in de tijdlijn alleen wat er
    // uitgevoerd is, en niet wat je gevraagd hebt.
    await labxTranscript(sessieId, "gebruiker", tekst);
    meld.onVeranderd?.();
    if (!wachtOpBevestiging || !tekst.trim()) return;
    wachtOpBevestiging = false;
    try {
      const uit = await labxAntwoord(sessieId, tekst);
      meld.onVeranderd?.();
      if (uit?.antwoord) stuurMededeling(dc, uit.antwoord);
    } catch (fout) {
      meld.onFout?.(fout);
    }
  }

  dc.addEventListener("open", () => meld.onStatus?.("Luistert"));
  dc.addEventListener("close", () => meld.onStatus?.("Verbroken"));
  dc.addEventListener("message", (bericht) => {
    let e: any;
    try {
      e = JSON.parse(bericht.data);
    } catch {
      return;   // geen JSON: niets aan te doen
    }
    if (e.type === "session.created") meld.onStatus?.("Luistert");
    if (e.type === "response.created") meld.onStatus?.("Spreekt");
    if (e.type === "response.done") meld.onStatus?.("Luistert");
    if (e.type === "error") meld.onFout?.(new Error(e.error?.message || "Realtime-fout"));

    if (e.type === "conversation.item.input_audio_transcription.completed" && e.transcript) {
      void gebruikerZei(String(e.transcript));
    }
    // Wat de assistent zei komt als losse stukjes binnen; pas bij 'done' is
    // het een hele zin die in de tijdlijn thuishoort.
    if ((e.type === "response.output_audio_transcript.done"
         || e.type === "response.audio_transcript.done") && e.transcript) {
      void labxTranscript(sessieId, "assistent", String(e.transcript))
        .then(() => meld.onVeranderd?.());
    }
    if (e.type === "response.output_item.done" && e.item?.type === "function_call") {
      if (nogNiet(e.item.call_id)) {
        void voerUit(e.item.name, lees(e.item.arguments), e.item.call_id);
      }
    } else if (e.type === "response.function_call_arguments.done") {
      if (nogNiet(e.call_id)) {
        void voerUit(e.name, lees(e.arguments), e.call_id);
      }
    }
  });

  pc.addEventListener("connectionstatechange", () => {
    if (pc.connectionState === "failed") meld.onStatus?.("Fout");
    if (pc.connectionState === "disconnected") meld.onStatus?.("Verbroken");
  });

  const aanbod = await pc.createOffer();
  await pc.setLocalDescription(aanbod);

  const sdp = await fetch(OPENAI_CALLS, {
    method: "POST",
    body: aanbod.sdp,
    headers: {
      Authorization: `Bearer ${token.client_secret}`,
      "Content-Type": "application/sdp",
    },
  });
  if (!sdp.ok) {
    try { dc.close(); } catch { /* al dicht */ }
    try { pc.close(); } catch { /* al dicht */ }
    opruimen();
    audio.remove();
    throw new Error(`OpenAI weigerde de verbinding (${sdp.status})`);
  }
  await pc.setRemoteDescription({ type: "answer", sdp: await sdp.text() });

  return {
    openMicrofoon,
    stop: () => {
      try { dc.close(); } catch { /* al dicht */ }
      try { pc.close(); } catch { /* al dicht */ }
      opruimen();
      audio.remove();
      meld.onStatus?.("Gestopt");
    },
    demp: (uit: boolean) => zetMicrofoon(!uit),

    beginBeurt: () => {
      if (openMicrofoon) return;
      // Leeggooien voordat je begint: anders zit de stilte van daarvoor nog
      // in de buffer en wordt die als jouw beurt meegestuurd.
      stuur(dc, { type: "input_audio_buffer.clear" });
      begonnenOp = Date.now();
      zetMicrofoon(true);
      meld.onStatus?.("Luistert");
    },
    eindBeurt: () => {
      if (openMicrofoon) return;
      zetMicrofoon(false);
      // Een tik in plaats van vasthouden levert te weinig audio op; OpenAI
      // weigert zo'n commit met een foutmelding. Dan liever niets versturen.
      if (Date.now() - begonnenOp < MINIMALE_BEURT_MS) {
        stuur(dc, { type: "input_audio_buffer.clear" });
        meld.onStatus?.("Te kort — houd de knop even vast");
        return;
      }
      // Zonder commit blijft OpenAI wachten tot er iets gebeurt -- en dat
      // gebeurt nooit, want de server-VAD staat bij push-to-talk uit.
      stuur(dc, { type: "input_audio_buffer.commit" });
      stuur(dc, { type: "response.create" });
      meld.onStatus?.("Denkt na…");
    },
  };
}

function lees(ruw: unknown): Record<string, unknown> {
  if (ruw && typeof ruw === "object") return ruw as Record<string, unknown>;
  try {
    return JSON.parse(String(ruw || "{}"));
  } catch {
    return {};
  }
}
