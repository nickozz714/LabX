/**
 * lib/uren.ts — API-client voor de urenregistratie.
 *
 * De vraag hier is niet "wat heeft LabX gedaan" (dat is het auditspoor) maar
 * "wat kan ik schrijven". Vandaar de scheiding die overal in deze types
 * terugkomt: jouw eigen tijd is het getal dat je schrijft, de gemeten
 * agent-tijd staat ernaast als context, en `overlap` zegt hoeveel daarvan
 * tegelijk met een ander project liep en dus niet twee keer mag.
 */
import { api } from "@/lib/api";

/** Waar een getal vandaan komt. Bewust niet samen te vatten in één som. */
export type TijdSoort = "gemeten" | "geschat" | "gemeld" | "handmatig";

export type TijdRegel = {
  id: number;
  board_id: number;
  ticket_id: number | null;
  project: string | null;
  category: string | null;
  kind: TijdSoort;
  minutes: number;
  day: string;
  started_at: string | null;
  ended_at: string | null;
  note: string | null;
  approved: boolean;
  source_run_id: string | null;
  created_at: string;
  updated_at: string;
};

export type ProjectRegel = {
  project: string;
  /** Wat je zou schrijven: geschat + gemeld + handmatig. */
  eigen_minuten: number;
  geschat: number;
  gemeld: number;
  handmatig: number;
  /** Machinetijd, als context. Telt NIET mee in eigen_minuten. */
  agent_minuten: number;
  /** De eigen looptijd, dubbelingen met andere projecten meegerekend. */
  bruto: number;
  /** Het eerlijke deel van de werkelijk verstreken klok. */
  verdeeld: number;
  /** bruto − verdeeld: wat je niet twee keer mag schrijven. */
  overlap: number;
  tickets: number;
  regels: number;
  categorieen: { naam: string; minuten: number }[];
};

export type UrenOverzicht = {
  van: string | null;
  tot: string | null;
  projecten: ProjectRegel[];
  totaal: {
    eigen_minuten: number;
    agent_minuten: number;
    bruto: number;
    verdeeld: number;
    overlap: number;
    /** De vereniging van alle intervallen: het plafond. */
    klok: number;
  };
};

/** Minuten als uren, zoals je ze schrijft. */
export const urenVan = (minuten: number) => minuten / 60;

export const toonUren = (minuten: number) =>
  `${(minuten / 60).toFixed(1)} u`;

export const urenApi = {
  overzicht: (params?: { van?: string; tot?: string; board_id?: number }) => {
    const qs = new URLSearchParams();
    if (params?.van) qs.set("van", params.van);
    if (params?.tot) qs.set("tot", params.tot);
    if (params?.board_id) qs.set("board_id", String(params.board_id));
    const s = qs.toString();
    return api.get<UrenOverzicht>(`/time/overzicht${s ? `?${s}` : ""}`);
  },
  regels: (params?: {
    van?: string; tot?: string; board_id?: number;
    ticket_id?: number; kind?: TijdSoort;
  }) => {
    const qs = new URLSearchParams();
    Object.entries(params || {}).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
    });
    const s = qs.toString();
    return api.get<TijdRegel[]>(`/time/entries${s ? `?${s}` : ""}`);
  },
  maak: (payload: Record<string, unknown>) =>
    api.post<TijdRegel>("/time/entries", payload),
  bewerk: (id: number, payload: Record<string, unknown>) =>
    api.patch<TijdRegel>(`/time/entries/${id}`, payload),
  verwijder: (id: number) => api.delete<{ ok: boolean }>(`/time/entries/${id}`),
  /** Runs opnieuw uitlezen en de schattingen bijwerken. */
  verzamel: (payload?: { sinds?: string; board_id?: number }) =>
    api.post<{ gemeten_nieuw: number; schattingen: number }>(
      "/time/verzamel", payload || {}),
  suggesties: () =>
    api.get<{ projecten: string[]; categorieen: string[] }>("/time/suggesties"),
};
