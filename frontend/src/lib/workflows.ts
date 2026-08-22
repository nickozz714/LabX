import { api } from "@/lib/api";
import type { ScheduleDto, ScheduleRunDto, WorkflowDto, WorkflowStep } from "@/lib/types";

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
  run: (id: number, labId: string) => api.post<{ id: string; status: string; output: string; error?: string }>(
    `/workflows/${id}/run`,
    { lab_id: labId },
  ),
};

export const scheduleApi = {
  list: () => api.get<ScheduleDto[]>("/schedules"),
  create: (payload: Record<string, any>) => api.post<ScheduleDto>("/schedules", payload),
  update: (id: number, payload: Record<string, any>) => api.patch<ScheduleDto>(`/schedules/${id}`, payload),
  remove: (id: number) => api.delete<{ ok: boolean }>(`/schedules/${id}`),
  runs: (id: number) => api.get<ScheduleRunDto[]>(`/schedules/${id}/runs`),
};
