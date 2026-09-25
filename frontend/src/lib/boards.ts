/**
 * lib/boards.ts — API-client voor het agent board.
 *
 * Tickets hangen onder hun board (/boards/{id}/tickets/...), net als in de
 * backend: er bestaat geen ticket zonder board, dus het board is overal de
 * context in plaats van een los id dat je erbij moet onthouden.
 */
import { api } from "@/lib/api";
import type { Bijlage } from "@/lib/labs";

/** De map in het lab waar bijlagen bij een ticket terechtkomen. De
 *  ticketsleutel als naam, want dat is het label dat ook op het bord en in de
 *  bron staat — zo weet je in de bestandsbrowser waar iets bij hoort. */
export const ticketBijlageMap = (ticketKey: string) =>
  `/workspace/uploads/${(ticketKey || "ticket").replace(/[^A-Za-z0-9._-]+/g, "_")}`;
import type {
  AgentRunStart, BoardDto, BoardSyncStats, ExternalBoardColumn, OverviewDto, PlanDto,
  ProviderSpec, TicketCommentDto, TicketDto,
} from "@/lib/types";

export const boardApi = {
  providers: () => api.get<ProviderSpec[]>("/boards/providers"),

  list: () => api.get<BoardDto[]>("/boards"),
  get: (id: number) => api.get<BoardDto>(`/boards/${id}`),
  create: (payload: Record<string, any>) => api.post<BoardDto>("/boards", payload),
  update: (id: number, payload: Record<string, any>) => api.patch<BoardDto>(`/boards/${id}`, payload),
  remove: (id: number) => api.delete<{ ok: boolean }>(`/boards/${id}`),

  tickets: (boardId: number, params?: { status?: string; assignee?: string }) => {
    const qs = new URLSearchParams();
    if (params?.status) qs.set("status", params.status);
    if (params?.assignee) qs.set("assignee", params.assignee);
    const s = qs.toString();
    return api.get<TicketDto[]>(`/boards/${boardId}/tickets${s ? `?${s}` : ""}`);
  },
  ticket: (boardId: number, ticketId: number) =>
    api.get<TicketDto>(`/boards/${boardId}/tickets/${ticketId}`),
  createTicket: (boardId: number, payload: Record<string, any>) =>
    api.post<TicketDto>(`/boards/${boardId}/tickets`, payload),
  updateTicket: (boardId: number, ticketId: number, payload: Record<string, any>) =>
    api.patch<TicketDto>(`/boards/${boardId}/tickets/${ticketId}`, payload),
  removeTicket: (boardId: number, ticketId: number) =>
    api.delete<{ ok: boolean }>(`/boards/${boardId}/tickets/${ticketId}`),
  moveTicket: (boardId: number, ticketId: number, status: string, position?: number) =>
    api.post<TicketDto>(`/boards/${boardId}/tickets/${ticketId}/move`, { status, position }),

  // ── planningen: geordende werkrijen ──
  overview: () => api.get<OverviewDto>("/boards/overview"),
  plans: (boardId: number) => api.get<PlanDto[]>(`/boards/${boardId}/plans`),
  plan: (boardId: number, planId: number) => api.get<PlanDto>(`/boards/${boardId}/plans/${planId}`),
  createPlan: (boardId: number, payload: Record<string, unknown>) =>
    api.post<PlanDto>(`/boards/${boardId}/plans`, payload),
  planFromColumn: (boardId: number, payload: Record<string, unknown> = {}) =>
    api.post<PlanDto>(`/boards/${boardId}/plans/from-column`, payload),
  pausePlan: (boardId: number, planId: number) =>
    api.post<PlanDto>(`/boards/${boardId}/plans/${planId}/pause`),
  resumePlan: (boardId: number, planId: number) =>
    api.post<PlanDto>(`/boards/${boardId}/plans/${planId}/resume`),
  cancelPlan: (boardId: number, planId: number) =>
    api.post<PlanDto>(`/boards/${boardId}/plans/${planId}/cancel`),
  reorderPlan: (boardId: number, planId: number, itemIds: number[]) =>
    api.post<PlanDto>(`/boards/${boardId}/plans/${planId}/reorder`, { item_ids: itemIds }),
  /** Welke tickets samen in één werker draaien: {item_id: bundelnummer}.
   *  Een leeg nummer betekent "alleen". */
  setPlanBundels: (boardId: number, planId: number, bundels: Record<number, number | null>) =>
    api.post<PlanDto>(`/boards/${boardId}/plans/${planId}/bundels`, { bundels }),
  removePlanItem: (boardId: number, planId: number, itemId: number) =>
    api.delete<PlanDto>(`/boards/${boardId}/plans/${planId}/items/${itemId}`),

  comments: (boardId: number, ticketId: number) =>
    api.get<TicketCommentDto[]>(`/boards/${boardId}/tickets/${ticketId}/comments`),
  addComment: (boardId: number, ticketId: number, body: string, internal = false) =>
    api.post<TicketCommentDto>(`/boards/${boardId}/tickets/${ticketId}/comments`, { body, internal }),
  // Een interne opmerking alsnog naar de bron sturen (alleen deze kant op).
  promoteComment: (boardId: number, ticketId: number, commentId: number) =>
    api.post<{ comment: TicketCommentDto; pushed: { ok: boolean; detail?: string; error?: string } | null }>(
      `/boards/${boardId}/tickets/${ticketId}/comments/${commentId}/promote`,
      {},
    ),

  runAgent: (boardId: number, ticketId: number, instruction?: string, attachments?: Bijlage[]) =>
    api.post<AgentRunStart>(`/boards/${boardId}/tickets/${ticketId}/agent-run`,
                            { instruction, attachments }),
  /** De agent van dit ticket stoppen. Werkt ook als de run alleen nog in de
   *  database "running" heet — dan geeft hij het ticket alsnog vrij. */
  cancelAgent: (boardId: number, ticketId: number) =>
    api.post<{ ok: boolean; afgebroken: boolean; run_id: string | null; planning_items: number }>(
      `/boards/${boardId}/tickets/${ticketId}/agent/cancel`, {}),
  pickUp: (boardId: number, payload?: { column?: string; max_tickets?: number }) =>
    api.post<{ started: AgentRunStart[]; count: number }>(`/boards/${boardId}/pick-up`, payload || {}),

  sync: (boardId: number) => api.post<BoardSyncStats>(`/boards/${boardId}/sync`),
  testConnection: (boardId: number) =>
    api.post<{
      ok: boolean;
      error?: string;
      found?: number;
      states?: string[];
      // De kolommen zoals ze in de bron op het bord staan, met de statussen
      // eronder — waarmee het instellingenscherm ze aan LabX-kolommen koppelt.
      columns?: ExternalBoardColumn[];
      unmapped_states?: string[];
      sample?: any[];
    }>(`/boards/${boardId}/sync/test`),
};
