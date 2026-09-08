import { api } from "@/lib/api";
import type { DockerStatus, GuardModelStatus, ImagePreset, Lab, LabExtra } from "@/lib/types";

export const labsApi = {
  list: () => api.get<Lab[]>("/labs"),
  get: (id: string) => api.get<Lab>(`/labs/${id}`),
  images: () => api.get<{ presets: ImagePreset[]; local_images: string[]; default_image: string }>("/labs/images"),
  searchImages: (q: string) =>
    api.get<{ ok: boolean; results: any[]; error?: string }>(`/labs/images/search?q=${encodeURIComponent(q)}`),
  // Lab-extra's: de catalogus van wat je in een lab kunt laten installeren.
  extras: () => api.get<LabExtra[]>("/labs/extras"),
  createExtra: (payload: Partial<LabExtra>) => api.post<LabExtra>("/labs/extras", payload),
  updateExtra: (id: number, payload: Partial<LabExtra>) => api.patch<LabExtra>(`/labs/extras/${id}`, payload),
  resetExtra: (id: number) => api.post<LabExtra>(`/labs/extras/${id}/reset`),
  deleteExtra: (id: number) => api.delete<{ ok: boolean }>(`/labs/extras/${id}`),
  // Opnieuw inrichten; antwoordt meteen, de voortgang staat op het lab zelf.
  provision: (id: string, force = false) =>
    api.post<{ ok: boolean; provision_status: string }>(`/labs/${id}/provision`, { force }),
  // Opnieuw opbouwen op (een nieuw) image; /workspace blijft staan.
  // Gegevens voor een tunnel naar dit lab (interactief inloggen met je eigen
  // browser): het container-IP en de poort, plus een kant-en-klaar script.
  tunnel: (id: string, port = 8400) =>
    api.get<{ lab_id: string; lab_name: string; port: number; container_ip: string;
              container: string; network: string }>(`/labs/${id}/tunnel?port=${port}`),
  tunnelScript: (id: string, sshTarget: string, port = 8400) =>
    api.get<{ filename: string; script: string }>(
      `/labs/${id}/tunnel-script?ssh_target=${encodeURIComponent(sshTarget)}&port=${port}`),
  // De grenzen van de autoscaler: min blijft altijd staan, tot max mag hij
  // bijschalen als er werk wacht.
  scaleWorkers: (id: string, minWorkers: number, maxWorkers: number) =>
    api.post<{ ok: boolean; workers: number; min: number; max: number;
               toegevoegd: number[]; verwijderd: number[] }>(
      `/labs/${id}/workers`,
      { min_workers: minWorkers, max_workers: maxWorkers },
    ),
  rebuild: (id: string, image?: string) =>
    api.post<{ ok: boolean; status: string; image: string }>(`/labs/${id}/rebuild`, { image }),
  guardModelStatus: () => api.get<GuardModelStatus>("/labs/guard-model/status"),
  guardModelEnsure: () => api.post<GuardModelStatus>("/labs/guard-model/ensure"),
  create: (payload: Record<string, any>) => api.post<Lab>("/labs", payload),
  update: (id: string, payload: Record<string, any>) => api.patch<Lab>(`/labs/${id}`, payload),
  start: (id: string) => api.post<Lab>(`/labs/${id}/start`),
  stop: (id: string) => api.post<Lab>(`/labs/${id}/stop`),
  remove: (id: string) => api.delete<{ ok: boolean }>(`/labs/${id}`),
  exec: (id: string, command: string, timeout?: number) =>
    api.post<{ exit_code: number; output: string; truncated: boolean; guarded?: boolean; guard_reason?: string }>(
      `/labs/${id}/exec`,
      { command, timeout },
    ),
  files: (id: string, path = "/workspace") =>
    api.get<{ path: string; entries: { name: string; is_dir: boolean }[] }>(
      `/labs/${id}/files?path=${encodeURIComponent(path)}`,
    ),
  readFile: (id: string, path: string) =>
    api.get<{ path: string; content: string; truncated: boolean }>(`/labs/${id}/file?path=${encodeURIComponent(path)}`),
  writeFile: (id: string, path: string, content: string) =>
    api.put<{ ok: boolean }>(`/labs/${id}/file`, { path, content }),
  publish: (id: string, payload: Record<string, any>) => api.post(`/labs/${id}/publish`, payload),
  azLogin: (id: string, payload: Record<string, any>) => api.post(`/labs/${id}/az-login`, payload),
  guardAudit: (id: string, limit = 200) =>
    api.get<{ lab_id: string; total: number; items: any[] }>(`/labs/${id}/guard-audit?limit=${limit}`),
};

export function labTerminalUrl(id: string, token: string): string {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}/api/labs/${id}/terminal?token=${encodeURIComponent(token)}`;
}

export function downloadGuardAuditCsvUrl(id: string): string {
  return `/api/labs/${id}/guard-audit?format=csv`;
}

export async function dockerStatus(): Promise<DockerStatus> {
  return api.get<DockerStatus>("/system/docker");
}
