/**
 * components/TicketVerwijzingen.tsx — sleutels in een omschrijving worden links.
 *
 * In een opdracht staat vaak "wacht op SWI-12" of "zie PROJ-7317". Dat zijn
 * verwijzingen naar echt werk, maar als platte tekst moet je ze overtypen in
 * het zoekveld — en dan kijk je dus meestal niet.
 *
 * Twee keuzes die het bruikbaar houden:
 *
 * - **Opzoeken gebeurt bij het klikken, niet bij het tonen.** Een omschrijving
 *   kan tien verwijzingen bevatten; die allemaal vooraf controleren betekent
 *   tien verzoeken voor iets waar je misschien nooit op klikt. Bijkomend
 *   voordeel: wat je ziet klopt op het moment dat het ertoe doet.
 * - **Bestaat hij niet, dan zeggen we dat.** Stil niets doen is het ergste van
 *   twee werelden: het lijkt een link, en je weet niet of je mis klikte of dat
 *   het ticket weg is.
 *
 * Een gearchiveerd ticket telt gewoon mee — dat bestaat nog, het staat alleen
 * niet op het bord.
 */
import type { ReactNode } from "react";
import { boardApi } from "@/lib/boards";
import { ApiError } from "@/lib/api";

/** SWI-12, PROJ-7317, LAB-3 — een prefix in hoofdletters en een nummer. */
const SLEUTEL = /\b([A-Z][A-Z0-9]{1,9}-\d+)\b/g;

export type OpenTicket = (ticketId: number) => void;

function Verwijzing({ sleutel, boardId, onOpen, onFout }: {
  sleutel: string;
  boardId: number;
  onOpen: OpenTicket;
  onFout: (melding: string) => void;
}) {
  return (
    <button
      type="button"
      className="rounded bg-primary/10 px-1 font-mono text-[0.95em] text-primary
                 underline-offset-2 hover:underline"
      title={`${sleutel} openen`}
      onClick={async (e) => {
        e.preventDefault();
        e.stopPropagation();
        try {
          const t = await boardApi.resolveTicket(boardId, sleutel);
          onOpen(t.id);
        } catch (err) {
          onFout(err instanceof ApiError ? err.message
                                         : `${sleutel} kon niet geopend worden.`);
        }
      }}
    >
      {sleutel}
    </button>
  );
}

/** Tekst opknippen op ticketsleutels. Laat al het andere ongemoeid. */
function knip(node: ReactNode, boardId: number, onOpen: OpenTicket,
              onFout: (m: string) => void, sleutelvan: number): ReactNode {
  if (typeof node !== "string") return node;
  const stukken: ReactNode[] = [];
  let vorig = 0;
  for (const tref of node.matchAll(SLEUTEL)) {
    const start = tref.index ?? 0;
    if (start > vorig) stukken.push(node.slice(vorig, start));
    stukken.push(
      <Verwijzing key={`${sleutelvan}-${start}`} sleutel={tref[1]} boardId={boardId}
                  onOpen={onOpen} onFout={onFout} />);
    vorig = start + tref[1].length;
  }
  if (!stukken.length) return node;
  if (vorig < node.length) stukken.push(node.slice(vorig));
  return stukken;
}

/**
 * De `components` voor ReactMarkdown die sleutels klikbaar maakt.
 *
 * Alleen in tekstdragende elementen, en met opzet NIET in `code`: daar staat
 * meestal een commando of een stuk uitvoer, en daar is "SWI-1" geen
 * verwijzing maar toevallig dezelfde vorm.
 */
export function ticketVerwijzingen(boardId: number, onOpen: OpenTicket,
                                   onFout: (melding: string) => void) {
  const maak = (Tag: "p" | "li" | "td" | "th" | "h1" | "h2" | "h3" | "strong" | "em") =>
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ({ children, ...rest }: any) => (
      <Tag {...rest}>
        {(Array.isArray(children) ? children : [children])
          .map((kind: ReactNode, i: number) => knip(kind, boardId, onOpen, onFout, i))}
      </Tag>
    );
  return {
    p: maak("p"), li: maak("li"), td: maak("td"), th: maak("th"),
    h1: maak("h1"), h2: maak("h2"), h3: maak("h3"),
    strong: maak("strong"), em: maak("em"),
  };
}
