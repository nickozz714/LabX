import { api } from "@/lib/api";
import type { AzureProfileDto } from "@/lib/types";

export const AZURE_BUNDLE_FILES = ["msal_token_cache.json", "azureProfile.json", "service_principal_entries.json"];

/** Eén doel waar een profiel naartoe gezet is: de host, of een lab. */
export interface ApplyStep {
  target: string;
  ok: boolean;
  detail: string;
}

/** De device-code-inlog voor een eigen Entra-app-registratie. Twee stappen,
 *  want zo werkt de flow: eerst een code die de gebruiker ergens invoert, dan
 *  wachten tot hij klaar is. Het pollen doet de BROWSER — een verzoek dat een
 *  kwartier openblijft wordt onderweg door elke proxy afgekapt, en dan lijkt
 *  een geslaagde inlog mislukt. */
export type DeviceLoginStart = {
  device_code: string; user_code: string; verification_uri: string;
  expires_in: number; interval: number; message?: string;
};

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
  // apply=true (de standaard) zet het verse resultaat meteen door naar de host
  // en de labs; zonder dat houden die een oude sessie over.
  refresh: (id: number, apply = true) =>
    api.post<{
      ok: boolean; kind: string; renewed?: number; detail: string;
      identity?: Record<string, any>;
      apply?: { ok: boolean; steps: ApplyStep[] };
    }>(`/azure-profiles/${id}/refresh?apply=${apply}`),
  // Doorzetten naar alles wat dit profiel gebruikt: host + elk gekoppeld lab.
  apply: (id: number) =>
    api.post<{ ok: boolean; steps: ApplyStep[] }>(`/azure-profiles/${id}/apply`),
  // Haalt de az-bestanden opnieuw van de host, na een verse `az login` daar.
  // De andere kant van sync: een sessie die IN een lab is ontstaan (interactief
  // ingelogd via de labbrowser of een tunnel) vastleggen als profiel.
  captureLab: (labId: string, name: string, description?: string) =>
    api.post<AzureProfileDto>("/azure-profiles/capture-lab",
      { lab_id: labId, name, description }),
  recaptureLab: (id: number, labId: string) =>
    api.post<AzureProfileDto>(`/azure-profiles/${id}/recapture-lab`, { lab_id: labId }),
  recaptureHost: (id: number) =>
    api.post<AzureProfileDto>(`/azure-profiles/${id}/recapture-host`, {}),
  deviceLoginStart: (id: number, scopes?: string[]) =>
    api.post<DeviceLoginStart>(`/azure-profiles/${id}/device-login`, { scopes }),
  deviceLoginPoll: (id: number, deviceCode: string) =>
    api.post<{ status: "wacht" | "klaar"; identity?: Record<string, any> }>(
      `/azure-profiles/${id}/device-login/poll`, { device_code: deviceCode }),
  /** Kan dit profiel een token halen voor deze API? Geeft bij een fout de
   *  melding van Entra zelf terug (AADSTS…) — die vertelt precies wat er in
   *  de app-registratie nog mist. */
  tokenTest: (id: number, scope: string) =>
    api.post<{ ok: boolean; error?: string; audience?: string; scopes?: string[]; upn?: string }>(
      `/azure-profiles/${id}/token-test`, { scope }),
  sync: (id: number, payload: { target: "host" | "lab"; lab_id?: string; az_dir?: string }) =>
    api.post<{ ok: boolean; target: string; detail: any }>(`/azure-profiles/${id}/sync`, payload),
};
