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
  soort: "dienst" | "applicatie" | "lab_image" | "lab_poorten" | "startscript"
       | "omgeving" | "extern" | "geheim";
  sleutel: string;
  waarde: {
    image?: string;
    poorten?: number[];
    omgeving?: Record<string, string>;
    lab_omgeving?: Record<string, string>;
    /** Variabelen waarvan het model de waarde NIET wist. Die horen niet in de
     *  omgeving van een lab terecht te komen — anders draait het met een
     *  sleutel die iedereen kan raden. Ze gaan naar de kluislijst. */
    omgeving_uit_kluis?: Record<string, string>;
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
  /** Wat JIJ hierover zei bij een eerdere ronde. */
  opmerking: string;
  /** Door jou uitgevinkt bij een eerdere ronde. De vondst blijft bestaan —
   *  het bewijs is nog net zo waar — maar hij staat niet meer vooraf aan.
   *  Dat is het verschil tussen "ik heb het niet gevonden" en "jij wilde het
   *  niet", en die twee horen niet hetzelfde te zien. */
  door_gebruiker_uit: boolean;
  /** Op grond van jouw opdracht weggelaten. Een detector vindt wat er in de
   *  repo staat en weet niet wat je ermee wilt. De vondst blijft staan — het
   *  bewijs is waar — maar hij gaat niet vanzelf mee. */
  uitgesloten: boolean;
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
  /** Wat het model niet uit de repo kon opmaken. Beter een vraag dan een gok —
   *  en je kunt erop antwoorden, wat een nieuw plan oplevert. */
  vragen: string[];
  /** Wat jij op eerdere vragen geantwoord hebt: {vraag: antwoord}. */
  antwoorden: Record<string, string>;
  /** Wat je van afzonderlijke onderdelen vond: {"dienst::mailpit": "..."}. */
  opmerkingen: Record<string, string>;
  /** Wat je van het plan als geheel vond. */
  opmerking: string | null;
  /** Wat je VOORAF meegaf. Anders dan `opmerking`, dat gaat over een plan dat
   *  er al ligt — deze stond er vóór de eerste regel gelezen was. */
  instructie: string | null;
  /** Het plan waar dit uit voortkwam, als het een antwoordronde was. */
  vorig_plan_id: number | null;
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
  /** De publieke helft van de deploy key. Geen geheim — die hoort juist
   *  gekopieerd te worden, naar Settings → Deploy keys van de repo. */
  ssh_publiek: string | null;
  /** Hoe dit cluster bij zijn repo komt. Een sleutel wint van een token. */
  toegang_soort: "ssh" | "https" | "geen";
  /** Waarop de applicatie IN een branch-lab luistert; LabX publiceert die. */
  lab_poorten: number[];
  /** Welke daarvan nog niet in de kluis staan. */
  geheimen_ontbreken: string[];
  notitie: string | null;
  diensten: ClusterDienst[];
  labs: { id: string; name: string; branch: string | null }[];
  created_at: string;
  updated_at: string;
};

/** Eén geheim in de kluis van een cluster. Nooit de waarde — wel hoe lang
 *  hij is, want dat is genoeg om te zien of je de juiste hebt geplakt. */
export type ClusterGeheim = {
  naam: string;
  omschrijving: string | null;
  lengte: number;
  leesbaar: boolean;
  created_at: string;
  updated_at: string;
  last_used_at: string | null;
};

/** Wat de applicatie nodig heeft, en of het er is. `in_eigen_kluis` is het
 *  verschil tussen "staat in dit cluster" en "komt uit de gedeelde kluis". */
export type BenodigdGeheim = {
  naam: string;
  waarvoor: string;
  in_eigen_kluis: boolean;
  gevonden: boolean;
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

  // ── de kluis van dit cluster ───────────────────────────────────────────
  //
  // Apart van de gedeelde kluis, want een cluster verwijst naar geheimen met
  // de naam van een OMGEVINGSVARIABELE — JWT_SECRET, DB_PASS — en die namen
  // zijn juist niet uniek. In één gedeelde kluis vechten twee clusters dan om
  // hetzelfde geheim, en wie het laatst opslaat wint zonder dat iemand het
  // merkt: de waarde is nergens te zien.
  /** Een deploy key voor dit cluster maken. Deploy keys zijn op repo-niveau
   *  en vallen buiten het OAuth- en PAT-goedkeuringsbeleid van een
   *  organisatie — precies de muur waar een token op stuit. */
  sleutelMaken: (id: number) =>
    api.post<{ publieke_sleutel: string }>(`/clusters/${id}/sleutel`),
  sleutelWeg: (id: number) => api.delete<{ ok: boolean }>(`/clusters/${id}/sleutel`),

  geheimen: (id: number) =>
    api.get<{ eigen: ClusterGeheim[]; benodigd: BenodigdGeheim[] }>(`/clusters/${id}/geheimen`),
  geheimZetten: (id: number, naam: string, body: { waarde?: string; omschrijving?: string }) =>
    api.put<ClusterGeheim>(`/clusters/${id}/geheimen/${encodeURIComponent(naam)}`, body),
  geheimWeg: (id: number, naam: string) =>
    api.delete<{ ok: boolean }>(`/clusters/${id}/geheimen/${encodeURIComponent(naam)}`),

  // ── verkennen ──────────────────────────────────────────────────────────
  /** Maakt NIETS aan. Kloont ondiep, leest, en levert een voorstel. */
  verkennen: (body: {
    repo_url: string;
    branch?: string;
    token_geheim?: string | null;
    host_id?: number | null;
    cluster_id?: number | null;
    /** Een briefing vooraf: "de agent hoort er niet bij", "gebruik node 22".
     *  Scheelt een hele ronde ten opzichte van achteraf corrigeren. */
    instructie?: string;
  }) => api.post<ClusterPlan>("/clusters/verkennen", body),
  plan: (planId: number) => api.get<ClusterPlan>(`/clusters/plannen/${planId}`),
  /** Terugkoppeling geven en opnieuw verkennen. Levert een NIEUW plan op
   *  naast het oude, zodat je ziet wat je opmerking veranderde.
   *
   *  Alle vier optioneel, maar er moet er minstens één zijn: antwoorden op de
   *  vragen, een opmerking per onderdeel, iets over het plan als geheel, en
   *  wat je aangevinkt had — want een vinkje uitzetten is ook terugkoppeling. */
  opnieuw: (planId: number, body: {
    antwoorden?: Record<string, string>;
    opmerkingen?: Record<string, string>;
    opmerking?: string;
    instructie?: string;
    keuze?: VondstKeuze[];
  }) => api.post<ClusterPlan>(`/clusters/plannen/${planId}/opnieuw`, body),
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
  lab_poorten: {
    titel: "Waarop de applicatie luistert",
    onder: "LabX publiceert deze poorten naar buiten. Staat hier het verkeerde nummer, "
      + "dan is het lab onbereikbaar terwijl het wel draait.",
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
