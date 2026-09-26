import { api, getToken } from "@/lib/api";
import type { DockerStatus, GuardModelStatus, ImagePreset, Lab, LabExtra } from "@/lib/types";

/** Eén regel uit de bestandsbrowser. `bytes` is null voor mappen, en ook voor
 *  een lab-image waarvan de `ls` geen groottes kan geven. */
export type LabFileEntry = { name: string; is_dir: boolean; bytes: number | null };

/** De toestand van de zichtbare browser van een lab. `browser_draait` is het
 *  veld waar het om gaat: het VNC-scherm kan prima werken terwijl er niets in
 *  staat, en dan kijk je naar een leeg bureaublad. */
export type BrowserStatus = {
  pakket: boolean;
  lab_draait: boolean;
  vnc: boolean;
  browser_draait: boolean;
  server: string | null;
};

/** Een geheim van een lab. De WAARDE staat er bewust niet in: die verlaat de
 *  kluis alleen richting de container, en zelfs daar niet via een
 *  commandoregel. `has_value` zegt of er een is. */
export type LabGeheim = {
  name: string;
  description: string | null;
  kind: "waarde" | "commando";
  has_value: boolean;
  produce_command: string | null;
  ttl_minutes: number;
  refreshed_at: string | null;
  last_used_at: string | null;
  last_error: string | null;
  /** Precies wat je in een commando schrijft. */
  placeholder: string;
};

/** Wat een upload teruggeeft. `skipped` is er met opzet: een bestand dat te
 *  groot of leeg was moet je zien, niet stil verdwijnen. */
export type Bijlage = { name: string; path: string; bytes: number };
export type UploadResultaat = {
  dir: string;
  files: Bijlage[];
  skipped: { name: string; reden: string }[];
};

/**
 * Hoe de machine ervoor staat. De cijfers komen uit /proc — in een container
 * niet geïsoleerd, dus dit is de HOST en niet de backend-container.
 */
export type HostMetrics = {
  geheugen: {
    totaal: number; beschikbaar: number; gebruikt: number;
    deel_gebruikt: number | null; swap_totaal: number; swap_gebruikt: number;
  };
  cpu: { kernen: number; bezet: number | null; load: number[]; load_per_kern: number | null };
  gpu: { naam: string; geheugen_totaal: number; geheugen_gebruikt: number;
         bezet: number; temperatuur: number }[];
  schijf: { totaal: number; gebruikt: number; vrij: number; pad: string } | null;
  labs: {
    labs_draaiend: number; geheugen: number; cpu: number;
    per_lab: { id: string; naam: string; werkers: number; geheugen: number; cpu: number }[];
  };
  waarschuwingen: { ernst: "laag" | "midden" | "hoog"; onderwerp: string; tekst: string }[];
};

export const labsApi = {
  list: () => api.get<Lab[]>("/labs"),
  hostMetrics: () => api.get<HostMetrics>("/labs/host-metrics"),
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
  scaleWorkers: (id: string, minWorkers: number, maxWorkers: number, chatDeelt?: boolean,
                 sessiesPerWerker?: number) =>
    api.post<{ ok: boolean; workers: number; min: number; max: number;
               toegevoegd: number[]; verwijderd: number[] }>(
      `/labs/${id}/workers`,
      { min_workers: minWorkers, max_workers: maxWorkers, chat_deelt_werker: chatDeelt,
        sessies_per_werker: sessiesPerWerker },
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
    api.get<{ path: string; entries: LabFileEntry[] }>(
      `/labs/${id}/files?path=${encodeURIComponent(path)}`,
    ),
  readFile: (id: string, path: string) =>
    api.get<{ path: string; content: string; truncated: boolean }>(`/labs/${id}/file?path=${encodeURIComponent(path)}`),
  writeFile: (id: string, path: string, content: string) =>
    api.put<{ ok: boolean }>(`/labs/${id}/file`, { path, content }),
  secrets: (id: string) => api.get<LabGeheim[]>(`/labs/${id}/secrets`),
  putSecret: (id: string, naam: string, payload: Record<string, unknown>) =>
    api.put<LabGeheim>(`/labs/${id}/secrets/${encodeURIComponent(naam)}`, payload),
  deleteSecret: (id: string, naam: string) =>
    api.delete<{ ok: boolean }>(`/labs/${id}/secrets/${encodeURIComponent(naam)}`),
  testSecret: (id: string, naam: string) =>
    api.post<LabGeheim & { ok: boolean; lengte: number }>(
      `/labs/${id}/secrets/${encodeURIComponent(naam)}/test`),
  browserStatus: (id: string) => api.get<BrowserStatus>(`/labs/${id}/browser-status`),
  browserStart: (id: string, url?: string) =>
    api.post<BrowserStatus & { ok: boolean; url: string }>(`/labs/${id}/browser-start`, { url }),
  browserStop: (id: string) =>
    api.post<{ ok: boolean; sessies_gesloten: number }>(`/labs/${id}/browser-stop`),
  upload: (id: string, files: File[], dir = "/workspace") => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f, f.name));
    form.append("dir", dir);
    return api.upload<UploadResultaat>(`/labs/${id}/upload`, form);
  },
  publish: (id: string, payload: Record<string, any>) => api.post(`/labs/${id}/publish`, payload),
  azLogin: (id: string, payload: Record<string, any>) => api.post(`/labs/${id}/az-login`, payload),
  guardAudit: (id: string, limit = 200) =>
    api.get<{ lab_id: string; total: number; items: any[] }>(`/labs/${id}/guard-audit?limit=${limit}`),
};

export function labTerminalUrl(id: string, token: string): string {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}/api/labs/${id}/terminal?token=${encodeURIComponent(token)}`;
}

/**
 * Een bestand of map uit het lab naar je eigen machine halen.
 *
 * Niet met een gewone `<a href>`: de API wil een Bearer-token en dat stuurt de
 * browser bij een link niet mee. Dus zelf ophalen, en het resultaat als blob
 * aanbieden. Een map komt er als tar.gz uit.
 *
 * De blob staat wel even volledig in het geheugen van de browser. Dat is de
 * prijs voor een download die niet om een token in de URL vraagt — en die zou
 * in serverlogs en je geschiedenis belanden.
 */
export async function downloadLabFile(labId: string, path: string): Promise<string> {
  const token = getToken();
  const res = await fetch(
    `/api/labs/${labId}/download?path=${encodeURIComponent(path)}`,
    { headers: token ? { Authorization: `Bearer ${token}` } : {} },
  );
  if (!res.ok) {
    const tekst = await res.text().catch(() => "");
    let detail = tekst;
    try {
      detail = JSON.parse(tekst).detail || tekst;
    } catch {
      /* geen JSON — de tekst zelf is dan het beste wat we hebben */
    }
    throw new Error(detail || `Downloaden mislukt (${res.status})`);
  }
  // De naam die de server meegeeft; die weet of het een tar.gz geworden is.
  const cd = res.headers.get("Content-Disposition") || "";
  const uitKop = /filename="([^"]+)"/.exec(cd)?.[1];
  const naam = uitKop || path.split("/").pop() || "download";

  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = naam;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Pas opruimen nadat de browser de download heeft opgepakt.
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
  return naam;
}

export function downloadGuardAuditCsvUrl(id: string): string {
  return `/api/labs/${id}/guard-audit?format=csv`;
}

export async function dockerStatus(): Promise<DockerStatus> {
  return api.get<DockerStatus>("/system/docker");
}
