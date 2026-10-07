/**
 * Orb.tsx — de visuele toestand van de orchestrator.
 *
 * De eerste versie was drie CSS-cirkels over elkaar en zag er precies zo uit:
 * een gekleurde bol die wat op en neer ging. Deze versie leent de beeldtaal
 * van de HUD-interfaces die je in dit soort projecten ziet (concentrische
 * bogen, ronddraaiende baanringen, radiale telemetriestaven, een gloeiende
 * kern) maar tekent alles op een canvas-2D in plaats van met Three.js. Dat
 * scheelt ~600 kB op een bundel die al 1,3 MB is, en haalt moeiteloos 60fps
 * op een telefoon — wat voor WebGL met een deeltjesveld niet vanzelf geldt.
 *
 * Het belangrijkste verschil met de meeste van zulke visualisaties: de staven
 * tonen het ECHTE frequentiebeeld van wat er gezegd wordt. Een ring die altijd
 * hetzelfde golft leest na twee keer kijken als een laadbalkje; een ring die
 * meebeweegt met je klinkers voelt alsof het ding luistert.
 *
 * Zeven toestanden:
 *   rust · verbinden · luisteren · denken · spreken · bevestigen · fout
 */
import { useEffect, useRef, useState } from "react";

export type OrbToestand =
  | "rust" | "verbinden" | "luisteren" | "denken"
  | "spreken" | "bevestigen" | "fout";

/** [tint, verzadiging] per toestand. De kleur doet het werk; de vorm blijft
 *  gelijk, zodat je aan één blik genoeg hebt zonder te hoeven lezen. */
const TINT: Record<OrbToestand, [number, number]> = {
  rust:       [205, 0.45],
  verbinden:  [195, 0.70],
  luisteren:  [185, 1.00],
  denken:     [270, 0.85],
  spreken:    [155, 0.95],
  bevestigen: [38,  1.00],
  fout:       [2,   1.00],
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

const STAVEN = 72;

export function Orb({
  toestand,
  niveau = 0,
  spectrum,
  maat = 180,
  bijschrift = true,
  onClick,
}: {
  toestand: OrbToestand;
  niveau?: number;
  /** Vult de meegegeven buffer met frequentiedata. Ontbreekt dit, dan maakt
   *  de orb een eigen patroon — zie de uitleg bovenaan. */
  spectrum?: (uit: Uint8Array) => void;
  maat?: number;
  bijschrift?: boolean;
  onClick?: () => void;
}) {
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const rustiger = useRustigerBeeld();

  // In refs en niet in state: de tekenlus draait op elke frame, en bij elke
  // frame een re-render uitlokken is precies wat dit traag maakt.
  const toestandRef = useRef(toestand);
  const niveauRef = useRef(niveau);
  const spectrumRef = useRef(spectrum);
  toestandRef.current = toestand;
  niveauRef.current = niveau;
  spectrumRef.current = spectrum;

  useEffect(() => {
    const el = canvas.current;
    if (!el) return;
    const tekenaar = el.getContext("2d");
    if (!tekenaar) return;
    // Vastleggen in een niet-nullbaar type: TypeScript verliest de controle
    // hierboven zodra `teken` later wordt aangeroepen in plaats van nu.
    const ctx: CanvasRenderingContext2D = tekenaar;

    // Scherp op een retina-scherm; zonder dit ziet alles er wazig uit.
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    el.width = maat * dpr;
    el.height = maat * dpr;
    ctx.scale(dpr, dpr);

    const banden = new Uint8Array(256);
    const vorige = new Float32Array(STAVEN);
    let id = 0;
    let zichtbaar = true;

    // Niet doortekenen als niemand kijkt: een animatielus in een achtergrond-
    // tabblad kost batterij zonder iets op te leveren.
    const bijZichtbaarheid = () => {
      zichtbaar = document.visibilityState === "visible";
      if (zichtbaar && !id) id = requestAnimationFrame(teken);
    };
    document.addEventListener("visibilitychange", bijZichtbaarheid);

    const begin = performance.now();

    function teken(nu: number) {
      id = 0;
      if (!zichtbaar) return;
      const t = (nu - begin) / 1000;
      const st = toestandRef.current;
      const [tint, verzadiging] = TINT[st];
      const mid = maat / 2;
      const straal = maat * 0.3;

      // ── Het signaal ─────────────────────────────────────────────────────
      spectrumRef.current?.(banden);
      const eigen = !spectrumRef.current || niveauRef.current < 0.02;
      const stil = rustiger || (eigen && !EIGEN_BEWEGING.includes(st));

      ctx.clearRect(0, 0, maat, maat);

      // ── Baanringen ──────────────────────────────────────────────────────
      // Twee ellipsen onder een hoek: dat leest als een bol in de ruimte in
      // plaats van als een platte cirkel, zonder dat er 3D aan te pas komt.
      if (!rustiger) {
        for (let r = 0; r < 2; r++) {
          const draai = t * (r ? -0.22 : 0.3) + r * 1.1;
          ctx.save();
          ctx.translate(mid, mid);
          ctx.rotate(draai);
          ctx.beginPath();
          ctx.ellipse(0, 0, straal * 1.52, straal * (0.3 + 0.1 * r), 0, 0, Math.PI * 2);
          ctx.strokeStyle = `hsla(${tint} ${verzadiging * 70}% 62% / ${0.16 + 0.1 * r})`;
          ctx.lineWidth = 1;
          ctx.stroke();
          ctx.restore();
        }
      }

      // ── Telemetriestaven ────────────────────────────────────────────────
      for (let i = 0; i < STAVEN; i++) {
        let waarde: number;
        if (stil) {
          waarde = 0.06;
        } else if (eigen) {
          // Twee golven over elkaar: het patroon herhaalt zich niet hoorbaar,
          // en dat doet echte spraak ook niet.
          waarde = 0.12 + 0.2 * Math.abs(Math.sin(i * 0.35 + t * 3.4))
                        + 0.12 * Math.abs(Math.sin(i * 0.11 - t * 1.3));
        } else {
          // De lage banden dragen de spraak; het hoge eind is vrijwel altijd
          // leeg en zou de helft van de ring doodstil maken.
          const bron = Math.floor((i / STAVEN) * 96);
          waarde = (banden[bron] / 255) * 0.95;
        }
        // Soepel volgen: zonder demping flikkert de ring per frame.
        vorige[i] += (waarde - vorige[i]) * 0.3;
        const lengte = straal * (0.16 + vorige[i] * 0.62);
        const hoek = (i / STAVEN) * Math.PI * 2 - Math.PI / 2;
        const binnen = straal * 1.1;
        ctx.beginPath();
        ctx.moveTo(mid + Math.cos(hoek) * binnen, mid + Math.sin(hoek) * binnen);
        ctx.lineTo(mid + Math.cos(hoek) * (binnen + lengte),
                   mid + Math.sin(hoek) * (binnen + lengte));
        ctx.strokeStyle =
          `hsla(${tint} ${verzadiging * 90}% ${58 + vorige[i] * 24}% / ${0.3 + vorige[i] * 0.6})`;
        ctx.lineWidth = maat * 0.012;
        ctx.lineCap = "round";
        ctx.stroke();
      }

      // ── Boog die rondloopt ──────────────────────────────────────────────
      // Bij verbinden en denken is er niets te meten maar gebeurt er wél iets;
      // deze boog is het enige dat dat vertelt.
      if (!rustiger && (st === "verbinden" || st === "denken" || st === "bevestigen")) {
        const snelheid = st === "bevestigen" ? 2.6 : 1.4;
        ctx.beginPath();
        ctx.arc(mid, mid, straal * 1.38, t * snelheid, t * snelheid + 1.1);
        ctx.strokeStyle = `hsla(${tint} ${verzadiging * 95}% 70% / 0.85)`;
        ctx.lineWidth = maat * 0.016;
        ctx.lineCap = "round";
        ctx.stroke();
      }

      // ── De kern ─────────────────────────────────────────────────────────
      const puls = stil ? 0 : (eigen ? 0.12 + 0.06 * Math.sin(t * 2.2)
                                     : niveauRef.current * 0.3);
      const kern = straal * (0.82 + puls);

      const gloed = ctx.createRadialGradient(mid, mid, 0, mid, mid, kern * 2.1);
      gloed.addColorStop(0, `hsla(${tint} ${verzadiging * 95}% 70% / 0.5)`);
      gloed.addColorStop(1, `hsla(${tint} ${verzadiging * 90}% 55% / 0)`);
      ctx.fillStyle = gloed;
      ctx.fillRect(0, 0, maat, maat);

      // Lichtbron linksboven: dat leest als een fysiek object in plaats van
      // als een gekleurde schijf.
      const bol = ctx.createRadialGradient(
        mid - kern * 0.34, mid - kern * 0.38, kern * 0.06, mid, mid, kern);
      bol.addColorStop(0, `hsla(${tint} ${verzadiging * 60}% 96% / 0.97)`);
      bol.addColorStop(0.42, `hsla(${tint} ${verzadiging * 92}% 64% / 0.95)`);
      bol.addColorStop(1, `hsla(${tint + 18} ${verzadiging * 90}% 32% / 0.95)`);
      ctx.beginPath();
      ctx.arc(mid, mid, kern, 0, Math.PI * 2);
      ctx.fillStyle = bol;
      ctx.fill();

      // Een dunne rand maakt het randje scherp tegen een donkere achtergrond.
      ctx.beginPath();
      ctx.arc(mid, mid, kern, 0, Math.PI * 2);
      ctx.strokeStyle = `hsla(${tint} ${verzadiging * 80}% 80% / 0.35)`;
      ctx.lineWidth = 1;
      ctx.stroke();

      id = requestAnimationFrame(teken);
    }

    id = requestAnimationFrame(teken);
    return () => {
      if (id) cancelAnimationFrame(id);
      document.removeEventListener("visibilitychange", bijZichtbaarheid);
    };
  }, [maat, rustiger]);

  const Tag = onClick ? "button" : "div";

  return (
    <div className="flex flex-col items-center gap-2">
      <Tag
        onClick={onClick}
        aria-label={onClick ? `Orchestrator — ${BIJSCHRIFT[toestand]}` : undefined}
        className={onClick
          ? "rounded-full border-0 bg-transparent p-0 transition-transform hover:scale-[1.03] focus-visible:outline-2 focus-visible:outline-offset-4"
          : ""}
        style={{ lineHeight: 0 }}
      >
        <canvas ref={canvas} width={maat} height={maat}
                style={{ width: maat, height: maat }} />
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
 *  een pulserende ring is voor sommige mensen echt vervelend. */
function useRustigerBeeld() {
  const [rustiger, setRustiger] = useState(false);

  useEffect(() => {
    try {
      const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
      setRustiger(mq.matches);
      const bij = (e: MediaQueryListEvent) => setRustiger(e.matches);
      mq.addEventListener("change", bij);
      return () => mq.removeEventListener("change", bij);
    } catch {
      return;
    }
  }, []);

  return rustiger;
}
