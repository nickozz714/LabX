/**
 * lib/boards.ts — API-client voor het agent board.
 *
 * Tickets hangen onder hun board (/boards/{id}/tickets/...), net als in de
 * backend: er bestaat geen ticket zonder board, dus het board is overal de
 * context in plaats van een los id dat je erbij moet onthouden.
 */
import { api } from "@/lib/api";
import type {
  AgentRunStart, BoardDto, BoardSyncStats, ExternalBoardColumn, ProviderSpec, TicketCommentDto,
  TicketDto,
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

  comments: (boardId: number, ticketId: number) =>
    api.get<TicketCommentDto[]>(`/boards/${boardId}/tickets/${ticketId}/comments`),
  addComment: (boardId: number, ticketId: number, body: string) =>
    api.post<TicketCommentDto>(`/boards/${boardId}/tickets/${ticketId}/comments`, { body }),

  runAgent: (boardId: number, ticketId: number, instruction?: string) =>
    api.post<AgentRunStart>(`/boards/${boardId}/tickets/${ticketId}/agent-run`, { instruction }),
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
