/**
 * lib/useConcept.ts — `useState` dat onthoudt wat je typte.
 *
 * Zelfde vorm als `useState`, zodat een scherm er niet omheen gebouwd hoeft
 * te worden: je vervangt één regel en de tekst overleeft een uitlog, een
 * slapende pc en een herladen tabblad.
 *
 * De sleutel hoort bij het SCHERM en niet bij de sessie: kom je terug op
 * hetzelfde plan, dan staat je opmerking er weer. Kom je op een ander plan,
 * dan niet — dat zou andermans woorden in jouw veld zetten.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { leesConcept, schrijfConcept, wisConcept } from "@/lib/concept";

export function useConcept<T>(sleutel: string | null, begin: T) {
  const [waarde, zetWaarde] = useState<T>(() =>
    (sleutel ? leesConcept<T>(sleutel) : null) ?? begin);

  // Niet bij elke toetsaanslag schrijven: dat is een schrijfronde per letter.
  const timer = useRef<number | null>(null);
  useEffect(() => {
    if (!sleutel) return;
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => schrijfConcept(sleutel, waarde), 400);
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [sleutel, waarde]);

  /** Na verzenden: het concept is geen concept meer. */
  const vergeet = useCallback(() => {
    if (sleutel) wisConcept(sleutel);
  }, [sleutel]);

  return [waarde, zetWaarde, vergeet] as const;
}
