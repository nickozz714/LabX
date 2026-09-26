/**
 * lib/audit.ts — wat er in een lab gebeurd is.
 *
 * Los van `lib/guard.ts`: dat spoor gaat over maskeren en blokkeren, dit over
 * beurten. Wat ging erin, wat kwam eruit, welk model deed het en welke acties
 * zijn er ondernomen.
 */
import { api } from "@/lib/api";

export type AuditActie = { naam: string; aantal: number };

/** Eén stap uit de beurt: wat de agent dacht, of wat hij opvroeg. De telling
 *  per tool zegt DAT er een aanroep was; dit zegt welke data erin ging. */
export type AuditVerloop = {
  soort: "denken" | "actie" | "afgekapt";
  tekst?: string;
  naam?: string;
  invoer?: string;
};

export type AuditGebeurtenis = {
  id: string;
  /** chat | taak | workflow — hoe deze beurt ontstond. */
  bron: string;
  ts: string;
  lab_id: string | null;
  lab_naam: string;
  werker: number | null;
  model: string;
  titel: string;
  status: string;
  invoer: string | null;
  uitvoer: string | null;
  acties: AuditActie[];
  acties_totaal: number;
  verloop: AuditVerloop[];
  duur_ms: number | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  thread_id?: string;
  run_id?: string;
  iteratie?: number | null;
};

export type AuditEmmer = {
  label: string;
  van: string;
  beurten: number;
  acties: number;
  fouten: number;
  cost_usd: number;
  input_tokens: number;
  output_tokens: number;
};

export type AuditAggregatie = {
  periode: "dag" | "week" | "maand";
  emmers: AuditEmmer[];
  top_acties: AuditActie[];
  totaal_beurten: number;
  totaal_acties: number;
  totaal_kosten: number;
};

export const auditApi = {
  activiteit: (p: { labId?: string; bron?: string; limit?: number; offset?: number }) => {
    const q = new URLSearchParams();
    if (p.labId) q.set("lab_id", p.labId);
    if (p.bron) q.set("bron", p.bron);
    q.set("limit", String(p.limit ?? 50));
    q.set("offset", String(p.offset ?? 0));
    return api.get<{ totaal: number; items: AuditGebeurtenis[] }>(`/audit/activiteit?${q}`);
  },
  aggregatie: (p: { labId?: string; periode: string; aantal: number }) => {
    const q = new URLSearchParams({ periode: p.periode, aantal: String(p.aantal) });
    if (p.labId) q.set("lab_id", p.labId);
    return api.get<AuditAggregatie>(`/audit/aggregatie?${q}`);
  },
};
