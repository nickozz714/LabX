import { api, streamSSE } from "@/lib/api";
import type { BackgroundRunDto, ChatEvent, Message, Thread } from "@/lib/types";

export const chatApi = {
  listThreads: () => api.get<Thread[]>("/chat/threads"),
  createThread: (lab_id: string, title?: string) => api.post<Thread>("/chat/threads", { lab_id, title }),
  getThread: (id: string) => api.get<Thread>(`/chat/threads/${id}`),
  renameThread: (id: string, title: string) => api.patch<Thread>(`/chat/threads/${id}`, { title }),
  setThreadModel: (id: string, model: string | null) => api.patch<Thread>(`/chat/threads/${id}`, { model }),
  setThreadEffort: (id: string, effort: string | null) => api.patch<Thread>(`/chat/threads/${id}`, { effort }),
  deleteThread: (id: string) => api.delete<{ ok: boolean }>(`/chat/threads/${id}`),
  listMessages: (threadId: string) => api.get<Message[]>(`/chat/threads/${threadId}/messages`),
  ask: (threadId: string, message: string, onEvent: (ev: ChatEvent) => void, signal?: AbortSignal) =>
    streamSSE(`/chat/threads/${threadId}/ask`, { message }, onEvent as any, signal),
  startBackground: (threadId: string, message: string) =>
    api.post<BackgroundRunDto>(`/chat/threads/${threadId}/background`, { message }),
  listBackgroundRuns: (params?: { thread_id?: string; status?: string; mode?: string }) => {
    const qs = new URLSearchParams();
    if (params?.thread_id) qs.set("thread_id", params.thread_id);
    if (params?.status) qs.set("status", params.status);
    if (params?.mode) qs.set("mode", params.mode);
    const s = qs.toString();
    return api.get<BackgroundRunDto[]>(`/chat/background-runs${s ? `?${s}` : ""}`);
  },
  getBackgroundRun: (runId: string) => api.get<BackgroundRunDto>(`/chat/background-runs/${runId}`),
  streamBackgroundRun: (runId: string, onEvent: (ev: ChatEvent) => void, signal?: AbortSignal) =>
    streamSSE(`/chat/background-runs/${runId}/stream`, {}, onEvent as any, signal),
  cancelBackgroundRun: (runId: string) =>
    api.post<{ ok: boolean; cancelled: boolean }>(`/chat/background-runs/${runId}/cancel`),
};
