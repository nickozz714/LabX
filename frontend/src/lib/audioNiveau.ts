/**
 * audioNiveau.ts — hoe hard er op dit moment geluid is, als getal tussen 0 en 1.
 *
 * De orb moet meebewegen met wat er werkelijk gezegd wordt; een animatie die
 * alleen maar "er gebeurt iets" uitbeeldt voelt na twee keer kijken als een
 * laadbalkje. Daarvoor is een echte meting nodig.
 *
 * Twee bronnen, allebei nodig:
 * - De MICROFOON (een MediaStream) terwijl jij praat.
 * - De LUIDSPREKER (een <audio>) terwijl de assistent praat. Dat kan alleen
 *   omdat die audio van onze eigen oorsprong komt (een blob-URL); bij een
 *   bestand van een ander domein zou de browser de analyse blokkeren.
 *
 * Voor `speechSynthesis` bestaat geen stream — die spreekt buiten de Web Audio
 * API om. Daar valt de orb terug op zijn eigen beweging; zie Orb.tsx.
 */

/** Eén AudioContext voor de hele pagina. Browsers staan er maar een handvol
 *  toe, en een nieuwe per zin loopt binnen een gesprek al vast. */
let context: AudioContext | null = null;

function geefContext(): AudioContext | null {
  try {
    if (!context) {
      const Ctor = window.AudioContext
        || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!Ctor) return null;
      context = new Ctor();
    }
    // Safari start hem opgeschort tot er een gebruikersgebaar is geweest.
    if (context.state === "suspended") void context.resume();
    return context;
  } catch {
    return null;
  }
}

export type NiveauMeter = {
  /** Huidig niveau, 0..1. Leest zonder te wachten; bedoeld voor een rAF-lus. */
  lees: () => number;
  /** Vult `uit` met het frequentiebeeld (0..255 per band).
   *  Daarmee kan de visualisatie de VORM van je stem laten zien en niet
   *  alleen de luidheid -- dat is het verschil tussen een meter en iets dat
   *  eruitziet alsof het luistert. */
  spectrum: (uit: Uint8Array) => void;
  /** Aantal banden, zodat de aanroeper de juiste buffer kan maken. */
  readonly banden: number;
  stop: () => void;
};

function maakMeter(bron: AudioNode, ctx: AudioContext,
                   opruimen?: () => void): NiveauMeter {
  const analyser = ctx.createAnalyser();
  // Klein genoeg om goedkoop te zijn, groot genoeg om niet te flikkeren.
  analyser.fftSize = 512;
  analyser.smoothingTimeConstant = 0.75;
  bron.connect(analyser);

  const buffer = new Uint8Array(analyser.frequencyBinCount);
  let gestopt = false;

  return {
    banden: analyser.frequencyBinCount,
    spectrum: (uit: Uint8Array) => {
      if (gestopt) {
        uit.fill(0);
        return;
      }
      // De cast is nodig sinds TypeScript de getypeerde arrays generiek maakte
      // over hun buffer: lib.dom vraagt hier Uint8Array<ArrayBuffer>, terwijl
      // een gewone `Uint8Array` als ArrayBufferLike wordt gelezen.
      analyser.getByteFrequencyData(uit as Uint8Array<ArrayBuffer>);
    },
    lees: () => {
      if (gestopt) return 0;
      analyser.getByteFrequencyData(buffer);
      // Effectieve waarde (RMS) in plaats van het gemiddelde: die volgt de
      // luidheid zoals je hem hoort, en springt niet op één uitschieter.
      let som = 0;
      for (let i = 0; i < buffer.length; i++) som += buffer[i] * buffer[i];
      const rms = Math.sqrt(som / buffer.length) / 255;
      // Spraak zit in een smalle band laag in het bereik; zonder deze curve
      // beweegt de orb nauwelijks bij normaal praten.
      return Math.min(1, Math.pow(rms, 0.6) * 2.2);
    },
    stop: () => {
      gestopt = true;
      try {
        bron.disconnect(analyser);
      } catch {
        /* al losgekoppeld */
      }
      opruimen?.();
    },
  };
}

/** Meet wat er de microfoon in gaat. */
export function meetStream(stream: MediaStream): NiveauMeter | null {
  const ctx = geefContext();
  if (!ctx) return null;
  try {
    const bron = ctx.createMediaStreamSource(stream);
    return maakMeter(bron, ctx, () => {
      try {
        bron.disconnect();
      } catch {
        /* al losgekoppeld */
      }
    });
  } catch {
    return null;
  }
}

// Een <audio> mag maar ÉÉN keer aan de Web Audio API gekoppeld worden; een
// tweede createMediaElementSource op hetzelfde element gooit. Daarom onthouden
// we welke we al hebben.
const gekoppeld = new WeakMap<HTMLMediaElement, MediaElementAudioSourceNode>();

/** Meet wat er uit de luidspreker komt. */
export function meetElement(el: HTMLMediaElement): NiveauMeter | null {
  const ctx = geefContext();
  if (!ctx) return null;
  try {
    let bron = gekoppeld.get(el);
    if (!bron) {
      bron = ctx.createMediaElementSource(el);
      // Zonder deze verbinding hoor je niets meer: zodra een element aan de
      // Web Audio API hangt, loopt het geluid niet langer vanzelf naar de
      // luidspreker.
      bron.connect(ctx.destination);
      gekoppeld.set(el, bron);
    }
    return maakMeter(bron, ctx);
  } catch {
    return null;
  }
}
