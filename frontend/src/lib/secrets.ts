/**
 * lib/secrets.ts — de kluis met geheimen die overal gelden.
 *
 * Waarden gaan er alleen IN. Er is geen endpoint dat er een teruggeeft, ook
 * niet aan jou: wat hier ligt hoort de kluis alleen te verlaten op weg naar
 * een tool of een commando. Je verwijst er overal naar met
 * `{{secret:naam}}` — in een skill, een tool-argument, een workflow-stap, een
 * commando.
 */
import { api } from "@/lib/api";

export type SecretDto = {
  name: string;
  description: string | null;
  /** Wat je letterlijk typt waar de waarde zou staan. */
  placeholder: string;
  /** Leeg = geldt overal; anders alleen in deze labs. */
  lab_ids: string[];
  created_at: string;
  updated_at: string;
  last_used_at: string | null;
  use_count: number;
};

export const secretApi = {
  list: (labId?: string) =>
    api.get<SecretDto[]>("/secrets" + (labId ? `?lab_id=${encodeURIComponent(labId)}` : "")),
  put: (naam: string, payload: { value?: string; description?: string; lab_ids?: string[] }) =>
    api.put<SecretDto>(`/secrets/${encodeURIComponent(naam)}`, payload),
  remove: (naam: string) => api.delete<{ ok: boolean }>(`/secrets/${encodeURIComponent(naam)}`),
  /** Bestaat hij en is hij te ontsleutelen? Geeft lengte + twee tekens — genoeg
   *  om te zien of je de juiste hebt geplakt, te weinig om hem te gebruiken. */
  test: (naam: string) =>
    api.post<{ ok: boolean; lengte?: number; begint_met?: string; melding?: string }>(
      `/secrets/${encodeURIComponent(naam)}/test`),
};
