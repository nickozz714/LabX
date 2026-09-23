import { api } from "@/lib/api";
import type {
  ScheduleDto, ScheduleRunDto, WorkflowDto, WorkflowEdge, WorkflowNode,
  WorkflowRunDto, WorkflowStep,
} from "@/lib/types";

export const workflowApi = {
  list: () => api.get<WorkflowDto[]>("/workflows"),
  get: (id: number) => api.get<WorkflowDto>(`/workflows/${id}`),
  create: (payload: { name: string; description?: string; steps?: WorkflowStep[]; markdown?: string }) =>
    api.post<WorkflowDto>("/workflows", payload),
  updateSteps: (id: number, steps: WorkflowStep[]) => api.patch<WorkflowDto>(`/workflows/${id}`, { steps }),
  updateMarkdown: (id: number, markdown: string) => api.patch<WorkflowDto>(`/workflows/${id}`, { markdown }),
  updateMeta: (id: number, payload: { name?: string; description?: string; is_enabled?: boolean }) =>
    api.patch<WorkflowDto>(`/workflows/${id}`, payload),
  remove: (id: number) => api.delete<{ ok: boolean }>(`/workflows/${id}`),
  updateGraaf: (id: number, nodes: WorkflowNode[], edges: WorkflowEdge[]) =>
    api.patch<WorkflowDto>(`/workflows/${id}`, { nodes, edges }),
  /** Alles in één keer: naam, omschrijving én de graaf. Twee losse verzoeken
   *  voor één druk op 'opslaan' zijn twee keer wachten, en de tussentoestand
   *  (naam opgeslagen, graaf nog niet) wil niemand. */
  opslaan: (id: number, payload: {
    name: string; description: string; nodes: WorkflowNode[]; edges: WorkflowEdge[];
  }) => api.patch<WorkflowDto>(`/workflows/${id}`, payload),
  /** Starten geeft meteen de run terug; het werk loopt op de achtergrond. */
  run: (id: number, labId: string) =>
    api.post<WorkflowRunDto>(`/workflows/${id}/run`, { lab_id: labId }),
  /** De laatste runs — handmatig én gepland, want dat is hetzelfde ding. */
  runs: (workflowId?: number, limit = 50) =>
    api.get<WorkflowRunDto[]>(
      `/workflows/runs?limit=${limit}` + (workflowId ? `&workflow_id=${workflowId}` : "")),
  /** Het volledige verslag: per activiteit invoer, redenatie, uitvoer, prijs. */
  run_detail: (runId: string) => api.get<WorkflowRunDto>(`/workflows/runs/${runId}`),
  cancelRun: (runId: string) =>
    api.post<{ ok: boolean; status: string }>(`/workflows/runs/${runId}/cancel`),
};

export const scheduleApi = {
  list: () => api.get<ScheduleDto[]>("/schedules"),
  create: (payload: Record<string, any>) => api.post<ScheduleDto>("/schedules", payload),
  update: (id: number, payload: Record<string, any>) => api.patch<ScheduleDto>(`/schedules/${id}`, payload),
  remove: (id: number) => api.delete<{ ok: boolean }>(`/schedules/${id}`),
  runs: (id: number) => api.get<ScheduleRunDto[]>(`/schedules/${id}/runs`),
  runNow: (id: number) => api.post<{ ok: boolean; scheduled_for: string }>(`/schedules/${id}/run`),
};
