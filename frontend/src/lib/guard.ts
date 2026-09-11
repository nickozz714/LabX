/**
 * lib/guard.ts — de data-guard: regels, testen en het audit-spoor.
 *
 * Classifier en guard zijn twee dingen. De REGELS zeggen wat er gezocht wordt
 * en wat ermee gebeurt; de audit laat zien wat er daadwerkelijk is gebeurd —
 * het origineel uit de container én wat het model ervan kreeg. Die teksten
 * zitten achter een aparte aanroep per regel, zodat klantgegevens niet
 * meekomen in een lijst die je toevallig openhebt.
 */
import { api } from "@/lib/api";

export type GuardDoel = "opdracht" | "uitvoer";
export type GuardActie = "blokkeren" | "maskeren" | "waarschuwen" | "toelaten";

export type GuardRegel = {
  id: number;
  key: string | null;
  target: GuardDoel;
  name: string;
  description: string | null;
  kind: "ingebouwd" | "regex";
  pattern: string | null;
  category: string;
  action: GuardActie;
  enabled: boolean;
  sort_order: number;
  /** Meegeleverd: uit te zetten, niet te verwijderen. */
  builtin: boolean;
};

export type GuardDetector = { key: string; label: string; uitleg: string };

export type GuardBevinding = {
  regel: string; categorie: string; actie: string; aantal: number; voorbeeld?: string;
};

export type GuardAuditRegel = {
  id: number; ts: string; lab_id: string | null; lab_name: string | null;
  worker_id: number | null; run_id: string | null; command: string;
  outcome: "doorgelaten" | "gemaskeerd" | "geblokkeerd" | "geweigerd";
  findings: GuardBevinding[];
  llm_verdict: Record<string, unknown> | null;
  bytes_original: number; bytes_delivered: number; heeft_tekst: boolean;
};

export type GuardAuditDetail = GuardAuditRegel & {
  origineel: string | null;
  geleverd: string | null;
};

export type GuardStatus = {
  regels: { opdracht: number; uitvoer: number; uit: number };
  presidio: { beschikbaar: boolean; reden: string | null };
  lokaal_model: {
    state: string; model: string; url: string; ready: boolean;
    reachable: boolean; hint?: string | null;
  };
};

export const guardApi = {
  status: () => api.get<GuardStatus>("/guard/status"),
  rules: () => api.get<GuardRegel[]>("/guard/rules"),
  detectors: () => api.get<GuardDetector[]>("/guard/detectors"),
  create: (payload: Record<string, unknown>) => api.post<GuardRegel>("/guard/rules", payload),
  update: (id: number, payload: Record<string, unknown>) =>
    api.patch<GuardRegel>(`/guard/rules/${id}`, payload),
  remove: (id: number) => api.delete<{ ok: boolean }>(`/guard/rules/${id}`),
  test: (payload: Record<string, unknown>) =>
    api.post<{ ok: boolean; fout?: string; aantal?: number; treffers?: string[];
               voorbeeld_gemaskeerd?: string; waarschuwing?: string }>("/guard/test", payload),
  probeer: (payload: { target: GuardDoel; sample: string }) =>
    api.post<{ actie: string; findings: GuardBevinding[]; resultaat?: string | null;
               reden?: string }>("/guard/probeer", payload),
  audit: (params: { lab_id?: string; outcome?: string; limit?: number } = {}) => {
    const qs = new URLSearchParams();
    if (params.lab_id) qs.set("lab_id", params.lab_id);
    if (params.outcome) qs.set("outcome", params.outcome);
    qs.set("limit", String(params.limit ?? 50));
    return api.get<GuardAuditRegel[]>(`/guard/audit?${qs}`);
  },
  auditDetail: (id: number) => api.get<GuardAuditDetail>(`/guard/audit/${id}`),
};
