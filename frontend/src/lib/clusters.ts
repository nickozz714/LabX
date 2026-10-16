/**
 * lib/clusters.ts — development clusters en de verkenner.
 *
 * Een cluster is een groep labs die bij elkaar horen omdat ze dezelfde
 * applicatie draaien: één vaste laag die blijft staan (de emulator, de
 * database) en branch-labs die komen en gaan.
 *
 * Het stuk dat hier het meeste uitlegt nodig heeft is de VERKENNING.
 * `verkennen` maakt niets aan: het kloont ondiep in een wegwerpcontainer,
 * leest, en levert een PLAN — een voorstel met bewijs per onderdeel. Pas
 * `goedkeuren` zet dat om in een cluster. Die scheiding is geen beleefdheid
 * maar de kern: er draait geen container waar niemand op geklikt heeft.
 *
 * Zie docs/CLUSTERS.md voor het ontwerp.
 */
import { api } from "@/lib/api";

/** Waar een vondst vandaan komt. Zonder vindplaats is een plan een mening,
 *  en een mening kun je niet nakijken. */
export type Bewijs = {
  bestand: string;
  regel: number | null;
  fragment: string;
};

/**
 * Eén onderdeel van een voorstel.
 *
 * `bevestigd` is het veld waar het om draait. Een vondst die het model deed
 * en die de detectoren niet konden terugvinden in de repo blijft gewoon
 * staan — met vindplaats, zichtbaar — maar staat in het goedkeurscherm UIT.
 * Je kunt hem aanzetten; hij gaat niet vanzelf mee.
 */
export type Vondst = {
  /** dienst = een container die blijft staan · applicatie = wat uit de repo
   *  gebouwd wordt en dus in een branch-lab draait · lab_image = waarin dat
   *  gebeurt · startscript = bouwen en starten · omgeving = een instelling ·
   *  extern = een echte dienst zonder emulator · geheim = een sleutel
   *  waarvan we de dienst niet herkennen.
   *
   *  `applicatie` is een VASTSTELLING en geen voorstel: er valt niets aan te
   *  kiezen, en er hoort dus ook geen vinkje bij. `lab_image` is het
   *  omgekeerde: een KEUZE waarvan er precies een waar kan zijn. */
  soort: "dienst" | "applicatie" | "lab_image" | "startscript" | "omgeving" | "extern" | "geheim";
  sleutel: string;
  waarde: {
    image?: string;
    poorten?: number[];
    omgeving?: Record<string, string>;
    lab_omgeving?: Record<string, string>;
    sleutels?: string[];
    keuzes?: string[];
    standaard?: string;
    bouwen?: string[];
    starten?: string[];
    installeer?: string;
    [k: string]: unknown;
  };
  bewijs: Bewijs[];
  zekerheid: "hoog" | "midden" | "laag";
  /** `detector:compose`, `detector:omgeving`, ... of `model`. */
  bron: string;
  bevestigd: boolean;
  toelichting: string;
};

export type VondstKeuze = { soort: string; sleutel: string };

export type ClusterPlan = {
  id: number;
  cluster_id: number | null;
  repo_url: string;
  branch: string;
  commit: string | null;
  status: "voorstel" | "goedgekeurd" | "verworpen";
  model_gebruikt: boolean;
  /** Waarom het model niet meekeek. Geen fout van de verkenning: de
   *  detectoren hebben dan al een plan neergelegd, het is alleen dunner. */
  fout: string | null;
  samenvatting: string | null;
  startscript: string | null;
  /** Wat het model niet uit de repo kon opmaken. Beter een vraag dan een gok. */
  vragen: string[];
  vondsten: Vondst[];
  /** Was de repo-URL een SSH-adres? Dan is hij omgezet, en dat hoor je te zien. */
  url_omgezet: boolean | null;
  url_melding: string | null;
  boom_afgekapt: boolean | null;
  bestanden_gelezen: number | null;
  /** Wat er vooraf aanstaat: alles wat gestaafd is. */
  standaardkeuze: VondstKeuze[];
  keuzes: VondstKeuze[];
  created_at: string;
};

export type ClusterDienst = {
  id: number;
  naam: string;
  image: string;
  poorten: number[];
  omgeving: Record<string, string>;
  /** {containerpoort: machinepoort} — waar je hem van buiten bereikt. */
  poort_map: Record<string, number>;
  status: "gestopt" | "draait" | "fout";
  fout: string | null;
  draait: boolean;
};

export type Cluster = {
  id: number;
  naam: string;
  status: "nieuw" | "draait" | "fout";
  fout: string | null;
  netwerk: string;
  host_id: number | null;
  machine: string;
  repo_url: string | null;
  repo_token_geheim: string | null;
  lab_image: string | null;
  start_script: string | null;
  env_profiel: string | null;
  omgeving: Record<string, string>;
  benodigde_geheimen: Record<string, string>;
  /** Welke daarvan nog niet in de kluis staan. */
  geheimen_ontbreken: string[];
  notitie: string | null;
  diensten: ClusterDienst[];
  labs: { id: string; name: string; branch: string | null }[];
  created_at: string;
  updated_at: string;
};

export type Toegang = {
  gecontroleerd: boolean;
  ok?: boolean;
  mag_pushen?: boolean;
  hint?: string | null;
};

export type Goedkeuring = {
  cluster: Cluster;
  diensten_toegevoegd: string[];
  geheimen_ontbreken: string[];
};

export const clusterApi = {
  lijst: () => api.get<Cluster[]>("/clusters"),
  lees: (id: number) => api.get<Cluster>(`/clusters/${id}`),
  maak: (body: { naam: string; host_id?: number | null }) => api.post<Cluster>("/clusters", body),
  wijzig: (id: number, body: Partial<Cluster>) => api.patch<Cluster>(`/clusters/${id}`, body),
  /** Haalt ook de containers van de vaste diensten en het clusternetwerk weg.
   *  `niet_opgeruimd` zegt wat er bij Docker toch bleef staan — opruimen dat
   *  halverwege struikelt is erger dan opruimen dat iets laat staan, maar je
   *  hoort wel te zien wát er bleef. */
  verwijder: (id: number) =>
    api.delete<{ ok: boolean; niet_opgeruimd: string[] }>(`/clusters/${id}`),

  /** Kan dit cluster bij zijn repo, en mag het pushen? Vóór het klonen, want
   *  een mislukte kloon geeft een kale git-fout waar drie heel verschillende
   *  oorzaken achter kunnen zitten. */
  toegang: (id: number) => api.post<Toegang>(`/clusters/${id}/toegang`),

  // ── verkennen ──────────────────────────────────────────────────────────
  /** Maakt NIETS aan. Kloont ondiep, leest, en levert een voorstel. */
  verkennen: (body: {
    repo_url: string;
    branch?: string;
    token_geheim?: string | null;
    host_id?: number | null;
    cluster_id?: number | null;
  }) => api.post<ClusterPlan>("/clusters/verkennen", body),
  plan: (planId: number) => api.get<ClusterPlan>(`/clusters/plannen/${planId}`),
  plannen: (clusterId: number) => api.get<ClusterPlan[]>(`/clusters/${clusterId}/plannen`),
  /** `gekozen` weglaten betekent: neem over wat gestaafd is. */
  goedkeuren: (planId: number, body: {
    naam?: string;
    host_id?: number | null;
    gekozen?: VondstKeuze[];
    token_geheim?: string | null;
    env_profiel?: string | null;
  }) => api.post<Goedkeuring>(`/clusters/plannen/${planId}/goedkeuren`, body),

  // ── diensten en branches ───────────────────────────────────────────────
  dienstToevoegen: (id: number, body: { naam: string; image: string; poorten?: number[] }) =>
    api.post<ClusterDienst>(`/clusters/${id}/diensten`, body),
  dienstStart: (dienstId: number) => api.post<ClusterDienst>(`/clusters/diensten/${dienstId}/start`),
  dienstStop: (dienstId: number) => api.post<ClusterDienst>(`/clusters/diensten/${dienstId}/stop`),
  dienstWeg: (dienstId: number) => api.delete<{ ok: boolean }>(`/clusters/diensten/${dienstId}`),

  branchLab: (id: number, body: { branch: string; basis?: string; naam?: string }) =>
    api.post<{ id: string; name: string }>(`/clusters/${id}/branches`, body),
  branchStart: (labId: string) => api.post<unknown>(`/clusters/branches/${labId}/start`),
};

/** Wat een soort in het scherm heet, en waarom hij er toe doet. Eén plek,
 *  zodat de uitleg niet per scherm verschilt. */
export const SOORT_UITLEG: Record<Vondst["soort"], { titel: string; onder: string }> = {
  dienst: {
    titel: "Vaste diensten",
    onder: "Containers die blijven staan en door alle branches gedeeld worden.",
  },
  applicatie: {
    titel: "Wat een branch-lab gaat draaien",
    onder: "Dit wordt uit de repo gebouwd. Hier valt niets te kiezen \u2014 het volgt uit de repo.",
  },
  lab_image: {
    titel: "Waarin een branch-lab draait",
    onder: "Kies er \u00e9\u00e9n: het image met de taal en pakketbeheerder die dit project nodig heeft.",
  },
  startscript: {
    titel: "Bouwen en starten",
    onder: "Eén keer opgeschreven voor het hele cluster; elk branch-lab gebruikt het.",
  },
  omgeving: {
    titel: "Instellingen",
    onder: "Keuzes die per cluster vastliggen en niet in een script horen.",
  },
  extern: {
    titel: "Moet van buiten komen",
    onder: "Echte diensten zonder emulator. Hier zijn geheimen voor nodig.",
  },
  geheim: {
    titel: "Losse sleutels",
    onder: "Ziet eruit als een geheim, maar van welke dienst is onduidelijk.",
  },
};
