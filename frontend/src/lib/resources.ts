/**
 * lib/resources.ts — claimbare resources: de catalogus, en wie wat vasthoudt.
 *
 * Een lab is een sandbox-pc. Sinds een planning meerdere tickets in dezelfde
 * werker kan zetten, kunnen er twee agents tegelijk achter die pc zitten — en
 * dan is er nog steeds maar één browser, één playground, één poort 8000. Deze
 * lijst zegt wát er te reserveren valt; de agent doet het zelf met
 * `lab__resource__claim`.
 */
import { api } from "@/lib/api";

export type ClaimResourceDto = {
  key: string;
  label: string;
  description: string | null;
  /** "werker" (één per container) of "lab" (één voor het hele lab). */
  scope: string;
  /** "wachten" (blijf hangen tot hij vrij is) of "weigeren" (meteen antwoord). */
  gedrag: string;
  /** Na zoveel minuten vervalt een claim vanzelf — het vangnet voor een agent
   *  die crasht of vergeet vrij te geven. */
  timeout_minutes: number;
  default_on: boolean;
  is_enabled: boolean;
  builtin: boolean;
  created_at: string;
  updated_at: string;
};

export type ActieveClaim = {
  resource: string;
  worker_id: number | null;
  houder: string;
  houder_soort: string;
  houder_label: string | null;
  reden: string | null;
  sinds: string;
  verloopt: string | null;
};

export const resourcesApi = {
  list: () => api.get<ClaimResourceDto[]>("/claim-resources"),
  create: (payload: Record<string, unknown>) =>
    api.post<ClaimResourceDto>("/claim-resources", payload),
  update: (key: string, payload: Record<string, unknown>) =>
    api.patch<ClaimResourceDto>(`/claim-resources/${encodeURIComponent(key)}`, payload),
  remove: (key: string) =>
    api.delete<{ ok: boolean }>(`/claim-resources/${encodeURIComponent(key)}`),
  /** Wat er in dit lab te reserveren valt en wie er nu op zit. */
  labClaims: (labId: string) =>
    api.get<{ beschikbaar: ClaimResourceDto[]; actief: ActieveClaim[] }>(`/labs/${labId}/claims`),
  /** Een vastgelopen reservering met de hand losbreken. */
  losbreken: (labId: string, key: string) =>
    api.delete<{ ok: boolean; vrijgegeven: number }>(
      `/labs/${labId}/claims/${encodeURIComponent(key)}`),
};
