/**
 * lib/concept.ts — getypte tekst die een uitlog of een slapende pc overleeft.
 *
 * Een token is zestig minuten geldig. Loop je even weg, gaat de pc slapen, of
 * ben je lang met één verkenning bezig, dan val je terug op het inlogscherm —
 * en was alles weg wat je had ingetypt. Dat is geen datapunt maar een avond:
 * een opdracht voor de verkenner of een opmerking bij een plan is het werk
 * zelf, niet een bijzaak.
 *
 * **Waarom localStorage en niet de server.** Een concept is nog niet van de
 * server; het is van jou, op dit apparaat, en vaak nog half. Het bewaren
 * betekent niet dat het al ergens toe doet. Pas als je op verzenden drukt is
 * er iets gebeurd.
 *
 * **Alles valt stil terug.** Een privévenster, uitgezette opslag of een volle
 * schijf laat `localStorage` gooien. Dat mag een scherm nooit breken — dan
 * verlies je het concept, en dat is precies de toestand die we hadden.
 */

const VOORVOEGSEL = "labx_concept:";

/** Concepten ouder dan dit zijn geen concept meer maar rommel. */
const HOUDBAAR_MS = 7 * 24 * 60 * 60 * 1000;

type Bewaard = { waarde: unknown; op: number };

export function leesConcept<T>(sleutel: string): T | null {
  try {
    const ruw = localStorage.getItem(VOORVOEGSEL + sleutel);
    if (!ruw) return null;
    const bewaard = JSON.parse(ruw) as Bewaard;
    if (!bewaard || typeof bewaard.op !== "number") return null;
    if (Date.now() - bewaard.op > HOUDBAAR_MS) {
      localStorage.removeItem(VOORVOEGSEL + sleutel);
      return null;
    }
    return bewaard.waarde as T;
  } catch {
    return null;
  }
}

export function schrijfConcept(sleutel: string, waarde: unknown): void {
  try {
    const leeg = waarde === "" || waarde === null || waarde === undefined
      || (typeof waarde === "object" && Object.keys(waarde as object).length === 0);
    if (leeg) {
      localStorage.removeItem(VOORVOEGSEL + sleutel);
      return;
    }
    localStorage.setItem(VOORVOEGSEL + sleutel,
                         JSON.stringify({ waarde, op: Date.now() } satisfies Bewaard));
  } catch {
    /* geen opslag: dan geen concept. Nooit het scherm breken hiervoor. */
  }
}

export function wisConcept(sleutel: string): void {
  try {
    localStorage.removeItem(VOORVOEGSEL + sleutel);
  } catch {
    /* zie boven */
  }
}

/** Alles van één scherm tegelijk weg, nadat het verstuurd is. */
export function wisConceptGroep(voorvoegsel: string): void {
  try {
    const weg: string[] = [];
    for (let i = 0; i < localStorage.length; i += 1) {
      const sleutel = localStorage.key(i);
      if (sleutel && sleutel.startsWith(VOORVOEGSEL + voorvoegsel)) weg.push(sleutel);
    }
    weg.forEach((s) => localStorage.removeItem(s));
  } catch {
    /* zie boven */
  }
}

// ── waar je was toen je eruit vloog ─────────────────────────────────────────
//
// Terugkomen op /overzicht nadat je uit een halfafgemaakt plan bent gegooid,
// betekent zelf terugzoeken waar je was. Het pad is geen geheim en hoort bij
// het concept: samen zijn ze "waar ik gebleven was".

const BESTEMMING = "labx_na_inloggen";

export function onthoudBestemming(pad: string): void {
  try {
    if (pad && !pad.startsWith("/login")) sessionStorage.setItem(BESTEMMING, pad);
  } catch {
    /* zie boven */
  }
}

export function haalBestemming(): string | null {
  try {
    const pad = sessionStorage.getItem(BESTEMMING);
    if (pad) sessionStorage.removeItem(BESTEMMING);
    return pad;
  } catch {
    return null;
  }
}
