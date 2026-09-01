import { api } from "@/lib/api";
import type { AzureProfileDto } from "@/lib/types";

export const AZURE_BUNDLE_FILES = ["msal_token_cache.json", "azureProfile.json", "service_principal_entries.json"];

export const azureProfilesApi = {
  list: () => api.get<AzureProfileDto[]>("/azure-profiles"),
  create: (payload: Record<string, any>) => api.post<AzureProfileDto>("/azure-profiles", payload),
  captureHost: (name: string, description?: string) =>
    api.post<AzureProfileDto>("/azure-profiles/capture-host", { name, description }),
  update: (id: number, payload: Record<string, any>) => api.put<AzureProfileDto>(`/azure-profiles/${id}`, payload),
  remove: (id: number) => api.delete<{ ok: boolean }>(`/azure-profiles/${id}`),
  verify: (id: number) => api.post<{ ok: boolean; identity: Record<string, any> }>(`/azure-profiles/${id}/verify`),
  // Wisselt het refresh token in voor een vers paar; een profiel dat alleen in
  // de kluis ligt verloopt juist, want refresh tokens verlopen op stilte.
  refresh: (id: number) =>
    api.post<{ ok: boolean; kind: string; renewed?: number; detail: string; identity?: Record<string, any> }>(
      `/azure-profiles/${id}/refresh`,
    ),
  // Haalt de az-bestanden opnieuw van de host, na een verse `az login` daar.
  recaptureHost: (id: number) =>
    api.post<AzureProfileDto>(`/azure-profiles/${id}/recapture-host`, {}),
  sync: (id: number, payload: { target: "host" | "lab"; lab_id?: string; az_dir?: string }) =>
    api.post<{ ok: boolean; target: string; detail: any }>(`/azure-profiles/${id}/sync`, payload),
};
