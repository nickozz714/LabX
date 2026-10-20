import { api, getToken } from "@/lib/api";
import type { DockerStatus, GuardModelStatus, ImagePreset, Lab, LabExtra, LabHost, LabRepo, ModelRunner } from "@/lib/types";

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

/**
 * Machines in je netwerk die labs draaien.
 *
 * Aanmelden doet de agent zelf, zonder login — die kan er geen hebben. Al het
 * andere hier verandert iets en zit dus achter de gewone LabX-login.
 */
export const labHostsApi = {
  list: () => api.get<LabHost[]>("/lab-hosts"),
  /** De publieke SSH-sleutel van deze LabX, plus een kant-en-klare regel om
   *  op de andere machine te plakken. Dat is het enige dat je daar fysiek
   *  moet doen. */
  sleutel: () => api.get<{ publieke_sleutel: string; commando: string }>("/lab-hosts/sleutel"),
  get: (id: number) => api.get<LabHost>(`/lab-hosts/${id}`),
  /** Toelaten. De code staat in het venster van de agent op die computer. */
  koppelen: (id: number, code: string, dockerHost: string) =>
    api.post<LabHost>(`/lab-hosts/${id}/koppelen`, { code, docker_host: dockerHost }),
  weigeren: (id: number) => api.post<LabHost>(`/lab-hosts/${id}/weigeren`),
  /** Welke modelcontainers draaien er, waar, en hoe druk zijn ze? */
  modelpool: () => api.get<{ runners: ModelRunner[] }>("/lab-hosts/modelpool"),
  wijzig: (id: number, payload: Partial<Pick<LabHost, "naam" | "max_labs" | "notitie" | "docker_host" | "ollama_runners">>) =>
    api.patch<LabHost>(`/lab-hosts/${id}`, payload),
  verwijder: (id: number) => api.delete<{ ok: boolean }>(`/lab-hosts/${id}`),
  /** Meten in plaats van aannemen: komt LabX er werkelijk bij? Een gekoppelde
   *  machine waar je niet bij kunt is erger dan een ongekoppelde — je zet er
   *  labs op en die starten niet. */
  /** Alle kanalen van een machine in één uitslag: wat er opgeschreven staat,
   *  de hostagent, SSH en Docker. Ze kunnen het oneens zijn, en juist dán is
   *  één melding nodig in plaats van vier. */
  diagnose: (id: number) => api.get<{
    bruikbaar: boolean;
    oorzaak: string;
    melding: string;
    kanalen: {
      opgeschreven: { ok: boolean; laatst_gezien: string | null };
      agent: { ok: boolean; melding: string; url: string | null };
      ssh: { ok: boolean; melding: string };
      docker: { ok: boolean; melding: string };
    };
  }>(`/lab-hosts/${id}/diagnose`),
  /** Wat LabX denkt, naast wat er werkelijk op die machine staat. Verandert
   *  niets — een afstemming die stilletjes dingen rechtzet is hoe je werk
   *  kwijtraakt waar niemand om vroeg. */
  afstemming: (id: number) => api.get<{
    machine: string;
    labs: number;
    gemeten: boolean;
    melding: string;
    gelijk: { lab_id: string; naam: string; status: string; toelichting: string }[];
    weg: { lab_id: string; naam: string; status: string; container_id: string; toelichting: string }[];
    ander_label: { lab_id: string; naam: string; status: string; op_de_machine: string; in_labx: string }[];
    onbekend: { container_id: string; naam: string; state: string }[];
  }>(`/lab-hosts/${id}/afstemming`),
  test: (id: number) => api.post<{
    ok: boolean; detail?: string | null; docker_host?: string;
    /** Apart van `ok`: inloggen lukt wél maar Docker draait niet is een ander
     *  probleem dan er niet in komen, en heeft een andere oplossing. */
    ssh_ok?: boolean; ssh_melding?: string; diagnose?: any;
  }>(`/lab-hosts/${id}/test`),
};

export const labsApi = {
  /** Draait de hostagent, en waar? Het aanmaakscherm vraagt dit vóórdat je een
   *  hostlab maakt dat toch niet kan werken. */
  hostAgentStatus: () => api.get<{ bereikbaar: boolean; adres: string; hint: string | null }>(
    "/labs/host-agent/status"),
  list: () => api.get<Lab[]>("/labs"),
  hostMetrics: () => api.get<HostMetrics>("/labs/host-metrics"),
  get: (id: string) => api.get<Lab>(`/labs/${id}`),
  /** Dit lab naar een andere machine verhuizen; hostId null = terug naar deze
   *  server. Het volume gaat mee, de containerlaag niet — daarom richt het lab
   *  zich op de nieuwe machine opnieuw in. */
  verplaats: (id: string, hostId: number | null) =>
    api.post<Lab & {
      /** Andere processorsoort op de nieuwe machine: je werk gaat mee, maar
       *  niet elk pakket bestaat voor allebei. */
      waarschuwing?: string | null;
      /** Bijvoorbeeld: hij was verlopen en heeft een verse vervaltijd. */
      melding?: string | null;
    }>(`/labs/${id}/verplaatsen`, { host_id: hostId }),
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
  // Hernoemen en verplaatsen zijn dezelfde bewerking; het verschil is alleen
  // of de map in het doelpad anders is.
  renameFile: (id: string, van: string, naar: string) =>
    api.post<{ van: string; naar: string }>(`/labs/${id}/file/rename`, { van, naar }),
  deleteFile: (id: string, path: string) =>
    api.post<{ verwijderd: string }>(`/labs/${id}/file/delete`, { path }),
  mkdir: (id: string, path: string) =>
    api.post<{ map: string }>(`/labs/${id}/file/mkdir`, { path }),
  // Repo-registratie. Een repo wordt eerst geregistreerd (en gekloond); pas
  // daarna kun je hem kiezen om mee te werken.
  repos: (id: string) => api.get<LabRepo[]>(`/labs/${id}/repos`),
  registreerRepo: (id: string, payload: { url: string; naam?: string; branch?: string; token?: string }) =>
    api.post<{ repo: string; url: string; branch: string | null }>(`/labs/${id}/repos`, payload),
  verwijderRepo: (id: string, naam: string) =>
    api.delete<{ verwijderd: string }>(`/labs/${id}/repos/${encodeURIComponent(naam)}`),
  pullRepo: (id: string, naam: string) =>
    api.post<{ repo: string; output: string }>(`/labs/${id}/repos/${encodeURIComponent(naam)}/pull`),

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
  publish: (id: string, payload: { repo: string; branch?: string; message?: string; token?: string }) =>
    api.post<{ ok: boolean; repo: string; branch: string | null; output: string }>(
      `/labs/${id}/publish`, payload),
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
