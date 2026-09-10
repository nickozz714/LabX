/**
 * lib/notify.ts — meldkanalen.
 *
 * Het geheim (SMTP-wachtwoord of bot-token) gaat er alleen IN. De server geeft
 * hem nooit terug; je ziet alleen `has_secret`. Een leeg `secret` meesturen
 * betekent daarom "niet wijzigen" en niet "wissen" — anders zou elk formulier
 * dat het veld niet toont het geheim per ongeluk wegpoetsen.
 */
import { api } from "@/lib/api";

export type MeldKanaalSoort = "email" | "telegram";

export type MeldKanaal = {
  id: number;
  name: string;
  kind: MeldKanaalSoort;
  enabled: boolean;
  config: Record<string, unknown>;
  has_secret: boolean;
  events: string[];
  allow_reply: boolean;
  last_error: string | null;
  last_sent_at: string | null;
  last_poll_at: string | null;
};

export type MeldGebeurtenis = { key: string; label: string };

export type MeldLogRegel = {
  id: number;
  channel_id: number;
  event: string;
  title: string;
  status: string;
  error: string | null;
  created_at: string;
  context: Record<string, unknown>;
};

export const notifyApi = {
  events: () => api.get<MeldGebeurtenis[]>("/notify/events"),
  channels: () => api.get<MeldKanaal[]>("/notify/channels"),
  create: (payload: Record<string, unknown>) => api.post<MeldKanaal>("/notify/channels", payload),
  update: (id: number, payload: Record<string, unknown>) =>
    api.patch<MeldKanaal>(`/notify/channels/${id}`, payload),
  remove: (id: number) => api.delete<{ ok: boolean }>(`/notify/channels/${id}`),
  test: (id: number, send = false) =>
    api.post<Record<string, unknown>>(`/notify/channels/${id}/test`, { send }),
  telegramChats: (payload: { channel_id?: number; secret?: string }) =>
    api.post<{ chats: { chat_id: string; naam: string; type: string }[]; hint: string }>(
      "/notify/telegram/chats", payload),
  log: (limit = 30) => api.get<MeldLogRegel[]>(`/notify/log?limit=${limit}`),
};
