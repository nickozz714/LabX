/**
 * lib/thema.ts — licht, donker of meebewegen met het systeem.
 *
 * De keuze staat op een KLASSE (`.dark` op <html>) en niet op de media-query
 * alleen. Met alleen `prefers-color-scheme` kun je namelijk niet tegen je
 * systeem in kiezen, en dat is precies wat "licht" en "donker" betekenen.
 * "Auto" zet de klasse op basis van het systeem en blijft daarnaar luisteren,
 * zodat de app meegaat als je Mac 's avonds omschakelt.
 *
 * De keuze wordt meteen toegepast, ook vóór React draait (zie index.html):
 * anders zie je bij elke herlaadbeurt een witte flits voordat het donker
 * wordt.
 */
export type Thema = "licht" | "donker" | "auto";

export const THEMA_SLEUTEL = "labx_thema";

export function gekozenThema(): Thema {
  try {
    const w = localStorage.getItem(THEMA_SLEUTEL);
    if (w === "licht" || w === "donker" || w === "auto") return w;
  } catch {
    /* privémodus: dan is het deze sessie auto */
  }
  return "auto";
}

function systeemIsDonker(): boolean {
  try {
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
  } catch {
    return false;
  }
}

export function pasToe(thema: Thema): void {
  const donker = thema === "donker" || (thema === "auto" && systeemIsDonker());
  document.documentElement.classList.toggle("dark", donker);
}

export function zetThema(thema: Thema): void {
  try {
    localStorage.setItem(THEMA_SLEUTEL, thema);
  } catch {
    /* niet kunnen onthouden is vervelend, niet blokkerend */
  }
  pasToe(thema);
}

/** Blijf luisteren zolang de stand "auto" is. Geeft een opruimfunctie terug. */
export function volgSysteem(thema: Thema, bij: () => void): () => void {
  if (thema !== "auto") return () => {};
  try {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", bij);
    return () => mq.removeEventListener("change", bij);
  } catch {
    return () => {};
  }
}
