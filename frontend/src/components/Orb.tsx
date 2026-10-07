/**
 * Orb.tsx — de visuele toestand van de orchestrator.
 *
 * Opzet geleend van voiceorbs (github.com/amunozdev/voiceorbs, MIT): één
 * toestandscontract waar elke visualisatie op reageert. De uitvoering is van
 * onszelf en met opzet puur CSS — de bundel is al 1,3 MB, en een WebGL-orb
 * (three.js, ~600 kB) is op een telefoon de verkeerde ruil voor iets dat je
 * tijdens het praten in je ooghoek ziet.
 *
 * De zeven toestanden:
 *   rust       — niets aan de hand, rustig ademen
 *   verbinden  — bezig met opzetten, ongeduldig pulseren
 *   luisteren  — beweegt mee met JOUW stem (echt gemeten, niet nagedaan)
 *   denken     — wachten op het model, ronddraaiende glans
 *   spreken    — beweegt mee met wat er uit de luidspreker komt
 *   bevestigen — wacht op jouw akkoord; valt op, want hier moet je wat doen
 *   fout       — er ging iets mis
 *
 * `niveau` is 0..1 uit audioNiveau.ts. Is er geen meting (de browserstem gaat
 * buiten de Web Audio API om), dan maakt de orb zijn eigen beweging, zodat hij
 * nooit stilvalt terwijl er wél gepraat wordt.
 */
import { useEffect, useRef, useState } from "react";

export type OrbToestand =
  | "rust" | "verbinden" | "luisteren" | "denken"
  | "spreken" | "bevestigen" | "fout";

const KLEUREN: Record<OrbToestand, { kern: string; rand: string }> = {
  rust:       { kern: "oklch(0.72 0.11 250)", rand: "oklch(0.55 0.13 265)" },
  verbinden:  { kern: "oklch(0.78 0.13 230)", rand: "oklch(0.58 0.14 250)" },
  luisteren:  { kern: "oklch(0.80 0.16 200)", rand: "oklch(0.60 0.17 220)" },
  denken:     { kern: "oklch(0.78 0.14 300)", rand: "oklch(0.56 0.16 290)" },
  spreken:    { kern: "oklch(0.82 0.15 160)", rand: "oklch(0.60 0.16 175)" },
  bevestigen: { kern: "oklch(0.84 0.16 85)",  rand: "oklch(0.64 0.17 70)" },
  fout:       { kern: "oklch(0.72 0.19 25)",  rand: "oklch(0.54 0.20 25)" },
};

const BIJSCHRIFT: Record<OrbToestand, string> = {
  rust: "Klaar",
  verbinden: "Verbinden…",
  luisteren: "Ik luister",
  denken: "Even denken…",
  spreken: "Aan het woord",
  bevestigen: "Wacht op jou",
  fout: "Er ging iets mis",
};

/** Toestanden waarin de orb uit zichzelf beweegt als er niets te meten valt. */
const EIGEN_BEWEGING: OrbToestand[] = ["verbinden", "denken", "spreken"];

export function Orb({
  toestand,
  niveau = 0,
  maat = 180,
  bijschrift = true,
  onClick,
}: {
  toestand: OrbToestand;
  niveau?: number;
  maat?: number;
  bijschrift?: boolean;
  onClick?: () => void;
}) {
  const [eigen, setEigen] = useState(0);
  const rustigerBeeld = useRustigerBeeld();

  // Eigen beweging als er niets gemeten wordt. Niet in elke toestand: in rust
  // hoort hij juist stil te zijn, anders trekt hij de aandacht zonder reden.
  useEffect(() => {
    if (rustigerBeeld || niveau > 0.02 || !EIGEN_BEWEGING.includes(toestand)) {
      setEigen(0);
      return;
    }
    let id = 0;
    const begin = performance.now();
    const tik = (nu: number) => {
      const t = (nu - begin) / 1000;
      // Twee golven over elkaar: één snelle en één trage. Daardoor herhaalt
      // het patroon zich niet hoorbaar, en dat is precies wat echte spraak
      // ook niet doet.
      setEigen(0.28 + 0.22 * Math.sin(t * 5.1) + 0.12 * Math.sin(t * 1.7));
      id = requestAnimationFrame(tik);
    };
    id = requestAnimationFrame(tik);
    return () => cancelAnimationFrame(id);
  }, [toestand, niveau, rustigerBeeld]);

  const beweging = rustigerBeeld ? 0 : Math.max(niveau, eigen);
  const kleur = KLEUREN[toestand];
  const Tag = onClick ? "button" : "div";

  return (
    <div className="flex flex-col items-center gap-3">
      <Tag
        onClick={onClick}
        aria-label={onClick ? `Orchestrator — ${BIJSCHRIFT[toestand]}` : undefined}
        className={`orb ${onClick ? "orb-klikbaar" : ""} orb-${toestand}`}
        style={{
          width: maat,
          height: maat,
          // Als CSS-variabelen, zodat de animaties in index.css eraan kunnen
          // rekenen zonder dat React elke frame klassen hoeft te wisselen.
          "--orb-kern": kleur.kern,
          "--orb-rand": kleur.rand,
          "--orb-niveau": beweging.toFixed(3),
        } as React.CSSProperties}
      >
        <span className="orb-halo" />
        <span className="orb-bol" />
        <span className="orb-glans" />
      </Tag>
      {bijschrift && (
        <span className="text-xs font-medium text-muted-foreground">
          {BIJSCHRIFT[toestand]}
        </span>
      )}
    </div>
  );
}

/** Respecteert de systeeminstelling "minder beweging". Niet alleen netjes:
 *  een pulserende bol is voor sommige mensen echt vervelend. */
function useRustigerBeeld() {
  const [rustiger, setRustiger] = useState(false);
  const mq = useRef<MediaQueryList | null>(null);

  useEffect(() => {
    try {
      mq.current = window.matchMedia("(prefers-reduced-motion: reduce)");
      setRustiger(mq.current.matches);
      const bij = (e: MediaQueryListEvent) => setRustiger(e.matches);
      mq.current.addEventListener("change", bij);
      return () => mq.current?.removeEventListener("change", bij);
    } catch {
      return;
    }
  }, []);

  return rustiger;
}
