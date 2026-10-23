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
       | "omgeving" | "envbestand" | "extern" | "geheim";
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
  status: "gestopt" | "ophalen" | "draait" | "fout";
  fout: string | null;
  draait: boolean;
};

export type GitBestand = {
  pad: string;
  /** De twee tekens van `git status --porcelain`, voor wie ze kent. */
  code: string;
  soort: "nieuw" | "gewijzigd" | "verwijderd" | "toegevoegd" | "hernoemd";
  /** Of het vinkje standaard aan staat. Een .env staat uit. */
  standaard_aan: boolean;
  gevoelig: boolean;
};

export type BranchGitStatus = {
  lab_id: string;
  branch: string;
  titel: string;
  beschrijving: string;
  laatste_commit: string;
  vooruit: number;
  achter: number;
  op_remote: boolean;
  bestanden: GitBestand[];
  schoon: boolean;
  afgekapt: boolean;
};

export type PushUitslag = {
  ok: boolean;
  exit_code: number | null;
  branch: string;
  uitvoer: string;
};

export type ClusterBundel = {
  id: number;
  cluster_id: number;
  naam: string;
  bestandsnaam: string;
  bytes_uitgepakt: number;
  omvang: string;
  aantal_bestanden: number;
  boom: { pad: string; bytes: number }[];
  instructie: string;
  /** Waar de bundel hangt in de container die de opdracht uitvoert. */
  koppelpunt: string;
  laatst_toegepast: string | null;
  laatste_exitcode: number | null;
  laatste_uitvoer: string;
};

export type BundelUitslag = {
  toelichting: string;
  script: string;
  image?: string;
  uitvoer: string;
  exit_code: number | null;
};

export type DienstActie = {
  id: number;
  dienst_id: number;
  label: string;
  uitleg: string;
  /** `in` = docker exec in de dienst zelf. `naast` = een wegwerpcontainer op
   *  het clusternetwerk, die de dienst op naam bereikt. Nodig zodra je een
   *  client nodig hebt die niet in het image van de dienst zit. */
  plek: "in" | "naast";
  image: string;
  script: string;
  gevaarlijk: boolean;
  bron: "model" | "zelf" | "chat";
  laatst_gedraaid: string | null;
  laatste_exitcode: number | null;
  laatste_uitvoer: string;
};

export type ActieUitslag = {
  actie: DienstActie;
  exit_code: number;
  output: string;
  truncated: boolean;
};

export type DienstChatAntwoord = {
  antwoord: string;
  script: string;
  plek?: "in" | "naast";
  image?: string;
  label?: string;
  gevaarlijk?: boolean;
  uitvoer: string;
  exit_code: number | null;
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
  /** Het model waarop ELK lab van dit cluster draait. Leeg = de instelling
   *  van de installatie. Wijzigen zet het ook op de bestaande labs. */
  lab_model?: string | null;
  /** Hoeveel beurten een agent-run in een lab van dit cluster mag doen.
   *  Leeg = de instelling van de installatie. */
  lab_max_turns?: number | null;
  /** Krijgt een NIEUW branch-lab de guards aan? Bestaande labs houden wat ze
   *  hebben; dat staat per lab in de instellingen. */
  lab_data_guard?: boolean;
  lab_llm_guard?: boolean;
  lab_poorten: number[];
  /** Waar dit project env-bestanden verwacht. Uit de verkenning: afgeleid uit
   *  de `.env.example`-bestanden in de repo, aangevuld door het model met wat
   *  gitignored is maar wel in de documentatie staat. Alleen de PLEKKEN — de
   *  inhoud lever je zelf. */
  env_paden?: string[];
  /** Extra pakketten die elk branch-lab erbij krijgt (sleutels uit de
   *  lab-extra-catalogus). Git en het basisgereedschap zitten al in elk lab. */
  lab_extras?: string[];
  /** De proefbouw. "af" is niet "exitcode 0": af is wanneer JIJ zegt dat de
   *  branch goed is, en tot dat moment blijft het proeflab staan. */
  /** Haalt de applicatie haar geheimen zelf op uit een vault? Dan hoeven ze
   *  niet in de kluis — wel is er een aanmelding nodig. */
  geheimen_zelf: boolean;
  /** Het Azure-profiel dat elk branch-lab meekrijgt. In het lab kan niemand
   *  `az login` doen; de sessie komt hiervandaan. */
  azure_profiel_id: number | null;
  /** Nodig maar niet gekozen: de reden dat straks de helft van de applicatie
   *  niet start. */
  azure_profiel_nodig: boolean;
  proef_status: "geen" | "bezig" | "wacht_op_fiat" | "goedgekeurd" | "mislukt";
  proef_lab_id: string | null;
  proef_log: ProefRonde[];
  /** Welke daarvan nog niet in de kluis staan. */
  geheimen_ontbreken: string[];
  notitie: string | null;
  diensten: ClusterDienst[];
  /** poort_map = {containerpoort: machinepoort} — nodig voor het
   *  tunnelvenster, dat je laat kiezen welke poort een vast nummer krijgt. */
  labs: {
    id: string; name: string; branch: string | null;
    poort_map?: Record<string, number>;
    /** Waar deze branch over gaat. De titel is ook de branchnaam en reist
     *  mee naar git; de beschrijving blijft in LabX. */
    titel?: string; beschrijving?: string;
    status?: string; draait?: boolean;
    /** Start LabX de applicatie in dit lab, of alleen de container? Uit is
     *  voor een lab waarin je alleen code leest. */
    applicatie_starten?: boolean;
  }[];
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
  /** Het pad van het geïmporteerde env-bestand dat deze sleutel al levert,
   *  of null. Dan hoeft hij niet in de kluis: het bestand gaat bij elke start
   *  mee naar de werkkopie. */
  in_env_bestand?: string | null;
  gevonden: boolean;
};

/** Eén ronde van de proefbouw: wat er draaide, wat er misging, wat er
 *  veranderd is. Meelezen in plaats van afwachten. */
export type ProefRonde = {
  ronde: number;
  fase: string;
  fout?: string | null;
  toelichting?: string | null;
  gewijzigd?: boolean;
  adres?: string | null;
  op: string;
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

  // ── de proefbouw ───────────────────────────────────────────────────────
  //
  // Duurt zo lang als de bouw duurt — bij een monorepo minuten. De aanroep
  // keert pas terug als de lus klaar is of op zijn plafond stuit.
  proefbouw: (id: number, branch?: string) =>
    api.post<{ status: string; ronde?: number; lab_id: string; adres?: string | null;
               log: ProefRonde[] }>(`/clusters/${id}/proefbouw`, branch ? { branch } : {}),
  proefBevestigen: (id: number) =>
    api.post<{ status: string; lab_id: string | null }>(`/clusters/${id}/proefbouw/bevestigen`),
  proefAfkeuren: (id: number, opmerking: string) =>
    api.post<{ status: string; log: ProefRonde[] }>(
      `/clusters/${id}/proefbouw/afkeuren`, { opmerking }),

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
    /** Env-bestanden die je NU al meegeeft. Het model krijgt alleen de
     *  sleutelNAMEN te zien en kan ze dan in het startscript gebruiken in
     *  plaats van ernaast iets te verzinnen. Bij goedkeuren verhuizen ze
     *  naar het cluster. */
    env_bestanden?: { pad: string; inhoud: string }[];
    /** Pakketten die je vooraf kiest. Worden bij goedkeuren samengevoegd met
     *  wat het model voorstelde; de twee sluiten elkaar niet uit. */
    lab_extras?: string[];
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
  /** Draait deze dienst, en antwoordt hij ook? Twee verschillende vragen:
   *  "de container draait" is wat docker weet, "hij antwoordt" is wat je
   *  nodig hebt. Een emulator met data erin zit daar minuten tussen. */
  dienstGezondheid: (dienstId: number) => api.get<{
    stand: "gestopt" | "ophalen" | "start_op" | "klaar" | "fout" | "onbekend";
    klaar: boolean;
    melding: string;
  }>(`/clusters/diensten/${dienstId}/gezondheid`),
  dienstStart: (dienstId: number) => api.post<ClusterDienst>(`/clusters/diensten/${dienstId}/start`),
  dienstStop: (dienstId: number) => api.post<ClusterDienst>(`/clusters/diensten/${dienstId}/stop`),
  dienstWeg: (dienstId: number) => api.delete<{ ok: boolean }>(`/clusters/diensten/${dienstId}`),

  // ── knoppen bij een vaste dienst ───────────────────────────────────────
  //
  // Een vaste dienst is geen lab: er staat geen agent in. Het model schrijft
  // de knoppen en het script eronder; LabX voert ze uit tegen de dienst.
  dienstActies: (dienstId: number) =>
    api.get<DienstActie[]>(`/clusters/diensten/${dienstId}/acties`),
  dienstActiesVoorstellen: (dienstId: number, opdracht?: string) =>
    api.post<{ toegevoegd: number; toelichting: string; acties: DienstActie[] }>(
      `/clusters/diensten/${dienstId}/acties/voorstellen`, { opdracht: opdracht || "" }),
  dienstActieZetten: (dienstId: number, body: Partial<DienstActie> & { label: string; script: string }) =>
    api.post<DienstActie>(`/clusters/diensten/${dienstId}/acties`, body),
  dienstActieDraaien: (actieId: number) =>
    api.post<ActieUitslag>(`/clusters/diensten/acties/${actieId}/draaien`),
  dienstActieWeg: (actieId: number) =>
    api.delete<{ ok: boolean }>(`/clusters/diensten/acties/${actieId}`),
  dienstChat: (dienstId: number, vraag: string, geschiedenis: { rol: string; tekst: string }[]) =>
    api.post<DienstChatAntwoord>(`/clusters/diensten/${dienstId}/chat`, { vraag, geschiedenis }),

  // ── bundels: bestanden van buiten, met een opdracht erbij ──────────────
  bundels: (id: number) => api.get<ClusterBundel[]>(`/clusters/${id}/bundels`),
  bundelUploaden: (id: number, bestand: File, naam: string, instructie: string) => {
    const form = new FormData();
    form.append("bestand", bestand);
    form.append("naam", naam);
    form.append("instructie", instructie);
    return api.upload<ClusterBundel>(`/clusters/${id}/bundels`, form);
  },
  bundelWijzigen: (bundelId: number, body: { naam?: string; instructie?: string }) =>
    api.patch<ClusterBundel>(`/clusters/bundels/${bundelId}`, body),
  bundelWeg: (bundelId: number) => api.delete<{ ok: boolean }>(`/clusters/bundels/${bundelId}`),
  bundelToepassen: (bundelId: number, body: {
    dienst_id: number; instructie?: string; script?: string; image?: string;
  }) => api.post<BundelUitslag>(`/clusters/bundels/${bundelId}/toepassen`, body),

  branchLab: (id: number, body: {
    titel?: string; beschrijving?: string; branch?: string; basis?: string; naam?: string;
  }) => api.post<{ id: string; name: string; branch?: string }>(`/clusters/${id}/branches`, body),
  // ── git aan een branch-lab ─────────────────────────────────────────────
  //
  // Alles draait naast de lab-container, nooit erin: zo komt de sleutel of
  // het token niet in de omgeving van de agent terecht.
  branchGit: (labId: string) => api.get<BranchGitStatus>(`/clusters/branches/${labId}/git`),
  branchGitPush: (labId: string, body: {
    bericht: string; bestanden?: string[]; alles?: boolean;
  }) => api.post<PushUitslag>(`/clusters/branches/${labId}/git/push`, body),
  /** Welke branches er in deze werkkopie te kiezen zijn — lokaal en op de
   *  remote. Dat laatste is het punt: je wilt naar een branch kunnen die je
   *  hier nog niet hebt. */
  branchLijst: (labId: string) => api.get<{
    huidig: string;
    branches: { naam: string; huidig: boolean; lokaal: boolean; op_remote: boolean }[];
  }>(`/clusters/branches/${labId}/git/branches`),
  /** Omzetten naar een andere branch. Weigert bij ongecommit werk, tenzij je
   *  zegt dat het weg mag. */
  branchCheckout: (labId: string, body: { branch: string; weggooien?: boolean }) =>
    api.post<{ ok: boolean; branch: string; exit_code: number | null; uitvoer: string }>(
      `/clusters/branches/${labId}/git/checkout`, body),
  /** Alles wat niet gecommit is, weg — inclusief nieuwe bestanden. */
  branchWeggooien: (labId: string) =>
    api.post<{ ok: boolean; weggegooid: string[]; uitvoer: string }>(
      `/clusters/branches/${labId}/git/weggooien`),
  /** De remote ophalen en de basis in deze branch mergen. */
  branchBijwerken: (labId: string, basis?: string) =>
    api.post<{ ok: boolean; uitvoer: string }>(
      `/clusters/branches/${labId}/git/bijwerken`, { basis: basis || "" }),
  branchWijzigen: (labId: string, body: {
    titel?: string; beschrijving?: string; setup_script?: string;
    applicatie_starten?: boolean;
  }) =>
    api.patch<{
      ok: boolean; titel: string; beschrijving: string; setup_script: string;
      applicatie_starten: boolean;
    }>(
      `/clusters/branches/${labId}`, body),

  /** Het hele cluster naar een andere machine: diensten, hun gegevens en
   *  alle branch-labs. Een eigen handeling, want alleen het nummer wijzigen
   *  zou de containers en volumes op de oude machine laten staan. */
  verplaats: (id: number, hostId: number | null) =>
    api.post<Cluster>(`/clusters/${id}/verplaatsen`, { host_id: hostId }),
  branchStop: (labId: string) => api.post<unknown>(`/clusters/branches/${labId}/stop`),
  branchStart: (labId: string) => api.post<unknown>(`/clusters/branches/${labId}/start`),
  /** De applicatie opnieuw starten zonder het lab te herstarten. Nodig zodra
   *  het startscript BOUWT in plaats van een devserver te draaien: dan is een
   *  wijziging pas na een nieuwe bouw te zien. */
  /** De applicatie stilzetten, maar het lab laten draaien. */
  branchAppStoppen: (labId: string) =>
    api.post<{ ok: boolean; map: string; uitvoer: string }>(
      `/clusters/branches/${labId}/stop-app`),
  branchAppHerstarten: (labId: string) =>
    api.post<{ ok: boolean; map?: string }>(`/clusters/branches/${labId}/herstart-app`),
  /** De uitvoer van het startscript — waar je kijkt als er niets antwoordt. */
  branchStartLog: (labId: string) =>
    api.get<{ log: string }>(`/clusters/branches/${labId}/start-log`),

  /** De env-bestanden van dit cluster: pad en de NAMEN van de sleutels.
   *  Nooit de waarden — die gaan één kant op, naar de werkkopie in een lab. */
  envBestanden: (id: number) =>
    api.get<EnvBestand[]>(`/clusters/${id}/env-bestanden`),
  /** Een env-bestand importeren of vervangen. `pad` is waar het in de
   *  werkkopie hoort, bijvoorbeeld `components/api/.env`. */
  envBestandZetten: (id: number, pad: string, inhoud: string) =>
    api.post<EnvBestand>(`/clusters/${id}/env-bestanden`, { pad, inhoud }),
  envBestandVerwijderen: (id: number, bestandId: number) =>
    api.delete<{ ok: boolean }>(`/clusters/${id}/env-bestanden/${bestandId}`),

  // ── pull requests ────────────────────────────────────────────────────────

  /** Wat er in de pull request zou komen, zonder hem te maken. Eerst zien,
   *  dan pas versturen: titel en beschrijving zijn het enige wat een reviewer
   *  krijgt voordat hij de diff opent. */
  // ── agents uit de repo ───────────────────────────────────────────────────

  /** De agents die deze repo zelf meebrengt (.claude/agents/*.md). De CLI op
   *  de server vindt ze niet — die zoekt relatief aan zijn eigen werkmap — dus
   *  leest LabX ze uit het lab. */
  repoAgents: (labId: string) =>
    api.get<{ naam: string; omschrijving: string }[]>(`/repo-agents/${labId}`),

  repoAgentStarten: (labId: string, naam: string, opdracht: string) =>
    api.post<{ ok: boolean; run_id: string; thread_id: string; agent: string }>(
      `/repo-agents/${labId}/${naam}/start`, { opdracht }),

  // ── opsplitsen ───────────────────────────────────────────────────────────

  /** Hoe de wijzigingen over kleinere pull requests verdeeld zouden worden.
   *  Een voorstel: er wordt nog niets gemaakt. */
  splitsVoorstel: (labId: string) =>
    api.get<{
      branch: string; basis: string;
      bestanden: { pad: string; erbij: number; eraf: number; status: string }[];
      groepen: { branch: string; titel: string; reden: string; bestanden: string[] }[];
      niet_toegewezen: string[];
    }>(`/opsplitsen/${labId}/voorstel`),

  splitsMaken: (labId: string,
                groepen: { branch: string; titel: string; bestanden: string[] }[]) =>
    api.post<{
      ok: boolean; basis: string;
      resultaat: { branch: string; ok: boolean; bestanden?: number; melding: string }[];
    }>(`/opsplitsen/${labId}`, { groepen }),

  /** Wat het token bij deze repo mag. Een 403 kan drie dingen betekenen die
   *  elk ergens anders opgelost worden; dit zegt welke. */
  prRechten: (labId: string) =>
    api.get<{
      repo: string; ziet_repo: boolean; mag_pushen: boolean;
      pr_lezen: boolean; reden: string; advies: string;
    }>(`/pr/${labId}/rechten`),

  prVoorstel: (labId: string) =>
    api.get<{
      branch: string; naar: string; bestaat_al: boolean;
      pr_nummer: number | null; pr_url: string;
      titel: string; beschrijving: string; ticket: string;
      commits: { sha: string; titel: string }[];
    }>(`/pr/${labId}/voorstel`),

  prMaken: (labId: string, body: { titel?: string; beschrijving?: string;
                                   naar?: string; concept?: boolean }) =>
    api.post<{ ok: boolean; nieuw: boolean; nummer: number; url: string; melding: string }>(
      `/pr/${labId}`, body),

  prComments: (labId: string) =>
    api.get<{
      pr_nummer: number | null; ticket: string; melding: string;
      comments: { id: string; auteur: string; soort: string; plek: string;
                  tekst: string; op: string; url: string }[];
    }>(`/pr/${labId}/comments`),

  /** Gekozen reacties als opmerking op het gekoppelde ticket zetten. */
  prNaarTicket: (labId: string, ids: string[]) =>
    api.post<{ ok: boolean; ticket: string; aantal: number }>(
      `/pr/${labId}/naar-ticket`, { ids }),

  /** Waar JIJ dit lab opent. Niet altijd localhost: draait het cluster op een
   *  machine in je netwerk, dan staat de poort op de loopback van DIE computer
   *  en vraagt de server de agent daar om hem door te zetten. */
  branchAdres: (labId: string, poort?: number) =>
    api.get<{ url: string; via: string }>(
      `/clusters/branches/${labId}/adres${poort ? `?poort=${poort}` : ""}`),

  /** Een scriptje dat een VASTE poort op jouw machine naar dit lab stuurt.
   *
   *  LabX deelt oplopende machinepoorten uit zodat branches naast elkaar
   *  kunnen draaien; een OAuth-redirect eist juist een vast nummer, want
   *  `http://localhost:5173` staat bij de identity provider geregistreerd en
   *  nergens anders. Je draait het script zelf en het is weg na een herstart
   *  — zie backend services/lab/tunnel.py voor waarom dat opzet is.
   *
   *  Geeft het adres terug dat je daarna moet openen. */
  /** De tunnel-helper: één bestand voor al je branch-labs, één keer ophalen.
   *  Er staat geen poortnummer in — hij vraagt LabX bij elke keuze opnieuw
   *  welke labs er zijn en welke doorgang erbij hoort. */
  tunnelHelper: async () => {
    // Je eigen origin meesturen: tussen de browser en de backend zitten twee
    // proxies, en die lieten de poort uit de Host-header vallen. De helper
    // kreeg daardoor `http://192.168.2.15` en daar staat niets.
    const basis = encodeURIComponent(window.location.origin);
    await api.download(`/tunnel/helper?basis=${basis}`, "labx-tunnel-helper.command");
  },

  branchTunnel: async (labId: string, os: TunnelOs, containerPoort?: number) => {
    const q = new URLSearchParams({ os });
    if (containerPoort) q.set("container_poort", String(containerPoort));
    const kop = await api.download(`/clusters/branches/${labId}/tunnel?${q}`,
                                   `labx-tunnel.${os === "windows" ? "cmd" : "sh"}`);
    return kop.get("X-Labx-Tunnel-Url") || "";
  },
};

/** De besturingssystemen waarvoor LabX een tunnelscript schrijft. */
export type TunnelOs = "mac" | "windows" | "linux";

/** Wat er in het menu staat, en welke extensie je krijgt. */
export const TUNNEL_OS: { waarde: TunnelOs; naam: string }[] = [
  { waarde: "mac", naam: "macOS (.sh)" },
  { waarde: "windows", naam: "Windows (.cmd)" },
  { waarde: "linux", naam: "Linux (.sh)" },
];

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
  envbestand: {
    titel: "Env-bestanden die dit project verwacht",
    onder: "De plekken, niet de inhoud. Deze bestanden staan in .gitignore, dus een verse "
      + "kloon heeft ze niet \u2014 importeer ze bij het cluster en elk branch-lab krijgt ze.",
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

/** Een env-bestand dat bij een cluster hoort. `sleutels` zijn alleen NAMEN:
 *  de waarden komen nooit uit de API. */
export type EnvBestand = {
  id: number;
  pad: string;
  sleutels: string[];
  aantal: number;
  updated_at: string;
};
