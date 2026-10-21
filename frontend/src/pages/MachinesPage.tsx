/**
 * pages/MachinesPage.tsx — computers in je netwerk die labs draaien.
 *
 * Eén server die alles doet krijgt het op zijn staart. Een pc die toch aan
 * staat kan er labs bij nemen, en dan verdubbel je je capaciteit zonder iets
 * te kopen.
 *
 * Dit scherm bestaat omdat Docker niets uitzendt: er is geen manier om te
 * "zien" welke machines beschikbaar zijn. De agent op die pc meldt zich dus
 * bij LabX, en dan staat hij hier — als `nieuw`, met niets. Toelaten doet een
 * MENS, met de code die op het scherm van díé computer staat. Dat is het hele
 * beveiligingsmodel: wat zich meldt mag zichzelf niet toelaten, want een
 * aanmelding is geen bewijs.
 *
 * De drie dingen die je hier daarna echt wilt weten staan op de kaart:
 * is hij online, komt LabX er werkelijk bij (de testknop — een gekoppelde
 * machine waar je niet bij kunt is erger dan een ongekoppelde), en blijft hij
 * wakker. Een pc die na tien minuten in slaap valt is geen lab-host, en dat
 * wil je wéten en niet merken aan een nachtrun die halverwege stopt.
 */
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  Activity, Check, Copy, Cpu, HardDrive, Loader2, Moon, Monitor, RefreshCw, Trash2,
  Wifi, WifiOff,
} from "lucide-react";
import { labHostsApi } from "@/lib/labs";
import type { LabHost, ModelRunner } from "@/lib/types";
import { Badge, Button, Card, EmptyState, Input, Label, Modal } from "@/components/ui";
import { Brain, KeyRound } from "lucide-react";
import { useMelding } from "@/components/Meldingen";
import { useBevestiging } from "@/components/Bevestiging";
import { ApiError } from "@/lib/api";

/** "4,5 GB" leest nu eenmaal makkelijker dan "4608". */
function gb(mb: number | null): string {
  if (!mb) return "—";
  return `${(mb / 1024).toFixed(mb >= 10240 ? 0 : 1)} GB`;
}

function geleden(iso: string | null): string {
  if (!iso) return "nooit";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 90) return "zojuist";
  if (s < 3600) return `${Math.round(s / 60)} min geleden`;
  if (s < 86400) return `${Math.round(s / 3600)} uur geleden`;
  return `${Math.round(s / 86400)} dagen geleden`;
}

/** Kopiëren naar het klembord, met een bevestiging die je ziet. Zonder die
 *  terugkoppeling weet je niet of je klik is aangekomen, en plak je een oude
 *  sleutel in een terminal. */
function KopieerKnop({ tekst, label = "Kopiëren" }: { tekst: string; label?: string }) {
  const [gekopieerd, setGekopieerd] = useState(false);
  return (
    <Button variant="ghost" className="px-2 py-0.5 text-xs" onClick={async () => {
      try {
        await navigator.clipboard.writeText(tekst);
        setGekopieerd(true);
        setTimeout(() => setGekopieerd(false), 2000);
      } catch {
        // Zonder klembordrechten (http op een IP-adres, bijvoorbeeld) is de
        // tekst nog steeds te selecteren; stil falen is dan het minst erg.
      }
    }}>
      {gekopieerd ? <Check className="mr-1 h-3.5 w-3.5" /> : <Copy className="mr-1 h-3.5 w-3.5" />}
      {gekopieerd ? "Gekopieerd" : label}
    </Button>
  );
}

type Kanalen = Awaited<ReturnType<typeof labHostsApi.diagnose>>["kanalen"];
type Afstemming = Awaited<ReturnType<typeof labHostsApi.afstemming>>;

/** Eén kanaal, met zijn eigen oordeel.
 *
 * Vier regels naast elkaar in plaats van één rondje: als ze het oneens zijn
 * wil je zién welk kanaal het begeeft. "De machine doet het niet" is geen
 * diagnose als de agent zich twee minuten geleden nog meldde. */
function KanaalRegel({ naam, ok, uitleg }: { naam: string; ok: boolean; uitleg: string }) {
  return (
    <div className="flex items-start gap-1.5">
      <span className={`mt-1 h-1.5 w-1.5 shrink-0 rounded-full ${ok ? "bg-green-500" : "bg-destructive"}`} />
      <span className="shrink-0 font-medium">{naam}</span>
      {uitleg && <span className="min-w-0 truncate text-muted-foreground">{uitleg}</span>}
    </div>
  );
}

export function MachinesPage() {
  const [hosts, setHosts] = useState<LabHost[]>([]);
  const [laden, setLaden] = useState(true);
  const [koppelen, setKoppelen] = useState<LabHost | null>(null);
  const melding = useMelding();
  const bevestig = useBevestiging();

  const ophalen = useCallback(async () => {
    try {
      setHosts(await labHostsApi.list());
    } finally {
      setLaden(false);
    }
  }, []);

  useEffect(() => {
    ophalen();
    // Een machine die zich net meldt hoort binnen een paar tellen in beeld te
    // staan — anders zit je naar een leeg scherm te kijken terwijl de agent
    // op die pc al draait.
    const t = setInterval(ophalen, 5000);
    return () => clearInterval(t);
  }, [ophalen]);

  const nieuwe = hosts.filter((h) => h.status === "nieuw");
  const rest = hosts.filter((h) => h.status !== "nieuw");

  return (
    <div className="pagina veilig-onder p-4 sm:p-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold">Machines</h1>
          <p className="text-sm text-muted-foreground">
            Computers in je netwerk die labs kunnen draaien.
          </p>
        </div>
        <Button variant="ghost" onClick={ophalen} title="Vernieuwen">
          <RefreshCw className="h-4 w-4" />
        </Button>
      </div>

      {laden ? (
        <p className="text-sm text-muted-foreground">Laden…</p>
      ) : hosts.length === 0 ? (
        <GeenMachines />
      ) : (
        <>
          {nieuwe.length > 0 && (
            <div className="mb-5">
              <h2 className="mb-2 text-sm font-semibold">
                Wachten op bevestiging ({nieuwe.length})
              </h2>
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                {nieuwe.map((h) => (
                  <HostKaart key={h.id} host={h} onKoppel={() => setKoppelen(h)}
                             onWijzig={ophalen} />
                ))}
              </div>
            </div>
          )}

          {rest.length === 0 ? (
            <EmptyState>
              Nog geen gekoppelde machine. Bevestig er hierboven een om labs te
              kunnen verdelen.
            </EmptyState>
          ) : (
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
              {rest.map((h) => (
                <HostKaart key={h.id} host={h} onKoppel={() => setKoppelen(h)}
                           onWijzig={ophalen} />
              ))}
            </div>
          )}
        </>
      )}

      <div className="mt-6 space-y-3">
        <Modelpool />
        <SleutelVanLabX />
        <ZoDoeJeDatUitleg />
      </div>

      {koppelen && (
        <KoppelVenster
          host={koppelen}
          onClose={() => setKoppelen(null)}
          onGekoppeld={() => {
            setKoppelen(null);
            melding.ok("Machine gekoppeld. Je kunt er nu labs op zetten.");
            ophalen();
          }}
        />
      )}

      {/* De verwijder-bevestiging hangt per kaart; hier alleen de haken. */}
      <span className="hidden">{bevestig ? "" : ""}</span>
    </div>
  );
}

function GeenMachines() {
  return (
    <EmptyState>
      <div className="mx-auto max-w-md space-y-2 text-left">
        <p className="font-medium text-foreground">Nog geen machine aangemeld.</p>
        <p>
          Docker zendt niets uit, dus LabX kan niet zelf zoeken wat er op je
          netwerk staat. Start de agent op de computer die mee moet doen; die
          meldt zich hier, en dan bevestig je hem met de code die op dat scherm
          verschijnt.
        </p>
      </div>
    </EmptyState>
  );
}

function HostKaart({ host, onKoppel, onWijzig }: {
  host: LabHost; onKoppel: () => void; onWijzig: () => void;
}) {
  const [testen, setTesten] = useState(false);
  const [uitslag, setUitslag] = useState<{ ok: boolean; tekst: string } | null>(null);
  const [max, setMax] = useState<string>(host.max_labs ? String(host.max_labs) : "");
  const [adres, setAdres] = useState<string>(host.docker_host || "");
  const [runners, setRunners] = useState<string>(String(host.ollama_runners ?? 0));
  const melding = useMelding();
  const bevestig = useBevestiging();

  const [kanalen, setKanalen] = useState<Kanalen | null>(null);
  const [afstemming, setAfstemming] = useState<Afstemming | null>(null);
  const [afstemmen, setAfstemmen] = useState(false);

  // Vier kanalen die het oneens kunnen zijn, en één uitslag.
  //
  // "Verbinding testen" keek alleen naar Docker. Dat gaf de melding "staat
  // die machine aan?" terwijl de machine áánstond en SSH het probleem was --
  // en LabX wist dat ook, want de agent had zich twee minuten eerder nog
  // gemeld. De zin komt nu uit dezelfde diagnose die een mislukte labstart
  // gebruikt, zodat scherm en foutmelding niet uiteenlopen.
  async function test() {
    setTesten(true);
    setUitslag(null);
    setKanalen(null);
    try {
      const r = await labHostsApi.diagnose(host.id);
      setUitslag({ ok: r.bruikbaar, tekst: r.melding });
      setKanalen(r.kanalen);
    } catch (e) {
      setUitslag({ ok: false, tekst: e instanceof ApiError ? e.message : String(e) });
    } finally {
      setTesten(false);
    }
  }

  async function stemAf() {
    setAfstemmen(true);
    setAfstemming(null);
    try {
      setAfstemming(await labHostsApi.afstemming(host.id));
    } catch (e) {
      melding.fout(e instanceof ApiError ? e.message : "Afstemmen lukte niet");
    } finally {
      setAfstemmen(false);
    }
  }

  async function bewaarMax() {
    const n = max.trim() === "" ? null : Math.max(1, Number(max));
    if (n !== null && !Number.isFinite(n)) return;
    await labHostsApi.wijzig(host.id, { max_labs: n });
    melding.ok("Opgeslagen.");
    onWijzig();
  }

  async function bewaarRunners() {
    const n = Math.max(0, Math.min(4, Number(runners) || 0));
    if (n === (host.ollama_runners ?? 0)) return;
    await labHostsApi.wijzig(host.id, { ollama_runners: n });
    melding.ok(n === 0 ? "Geen modelcontainers meer hier."
                       : `${n} modelcontainer(s) op ${host.naam}.`,
               n > 0 ? "Het model wordt opgehaald; dat duurt de eerste keer even."
                     : undefined);
    onWijzig();
  }

  async function bewaarAdres() {
    const a = adres.trim();
    if (!a || a === host.docker_host) return;
    await labHostsApi.wijzig(host.id, { docker_host: a });
    melding.ok("Adres gewijzigd.", "Test de verbinding om te zien of het klopt.");
    setUitslag(null);
    onWijzig();
  }

  async function verwijder() {
    const ja = await bevestig.vraag({
      titel: `${host.naam} verwijderen?`,
      tekst: host.labs.length > 0
        ? `Er staan nog ${host.labs.length} lab(s) op deze machine. Verplaats die eerst.`
        : "De machine verdwijnt uit de lijst. Draait de agent er nog, dan meldt hij zich opnieuw — als nieuw.",
      bevestig: "Verwijderen",
      variant: "danger",
    });
    if (!ja) return;
    await labHostsApi.verwijder(host.id);
    onWijzig();
  }

  const gekoppeld = host.status === "gekoppeld";

  return (
    <Card className="p-4">
      <div className="mb-2 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Monitor className="h-4 w-4 shrink-0 text-muted-foreground" />
            <span className="truncate font-semibold">{host.naam}</span>
          </div>
          <div className="mt-0.5 truncate text-xs text-muted-foreground">
            {host.besturingssysteem || "onbekend systeem"}
            {host.docker_versie ? ` · Docker ${host.docker_versie}` : ""}
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <Badge tone={gekoppeld ? "green" : host.status === "nieuw" ? "yellow" : "neutral"}>
            {host.status}
          </Badge>
          {/* Online zegt alleen iets over de AGENT. Dat is bewust gescheiden
              van de testknop: de agent kan prima melden terwijl de Docker
              daar onbereikbaar is. */}
          <span className={`flex items-center gap-1 text-[11px] ${
            host.online ? "text-success" : "text-muted-foreground"}`}>
            {host.online ? <Wifi className="h-3 w-3" /> : <WifiOff className="h-3 w-3" />}
            {host.online ? "online" : geleden(host.laatst_gezien)}
          </span>
        </div>
      </div>

      <div className="mb-3 grid grid-cols-3 gap-2 text-xs text-muted-foreground">
        <span className="flex items-center gap-1" title="Kernen">
          <Cpu className="h-3.5 w-3.5" />{host.cpus ?? "—"}
        </span>
        <span className="flex items-center gap-1" title="Vrij geheugen van totaal">
          <Activity className="h-3.5 w-3.5" />{gb(host.vrij_mb)}<span className="opacity-60">/{gb(host.geheugen_mb)}</span>
        </span>
        <span className="flex items-center gap-1" title="Vrije schijfruimte">
          <HardDrive className="h-3.5 w-3.5" />{host.schijf_vrij_gb ? `${host.schijf_vrij_gb} GB` : "—"}
        </span>
      </div>

      {gekoppeld && !host.blijft_wakker && (
        <div className="mb-3 flex items-start gap-2 rounded-md border border-warning/30 bg-warning/10 p-2 text-xs text-warning">
          <Moon className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            Deze machine blijft niet wakker. Valt hij in slaap, dan stopt een
            nachtrun halverwege.
          </span>
        </div>
      )}

      {host.status === "nieuw" ? (
        <div className="space-y-2">
          <p className="text-xs text-muted-foreground">
            Deze computer heeft zich gemeld. Hij krijgt nog niets: bevestig
            eerst de code die in het venster van de agent op die machine staat.
          </p>
          <div className="flex gap-2">
            <Button onClick={onKoppel}>Koppelen…</Button>
            <Button variant="ghost" onClick={async () => {
              await labHostsApi.weigeren(host.id);
              onWijzig();
            }}>Weigeren</Button>
          </div>
        </div>
      ) : (
        <>
          <div className="mb-3 space-y-2">
            <div>
              {/* Te wijzigen, want een adres kan gewoon verkeerd zijn -- en
                  dan wil je dat kunnen rechtzetten zonder de machine te
                  ontkoppelen en opnieuw te beginnen. */}
              <Label>Docker-adres</Label>
              <div className="flex gap-2">
                <Input value={adres} onChange={(e) => setAdres(e.target.value)}
                       onBlur={bewaarAdres}
                       placeholder="ssh://nick@werkpc.local"
                       className="font-mono text-xs" />
                <Button variant="ghost" onClick={bewaarAdres}
                        disabled={!adres.trim() || adres.trim() === host.docker_host}>
                  Opslaan
                </Button>
              </div>
              {/* Loopt het werk nu ergens anders langs? Dat moet je weten,
                  anders lijkt alles gewoon te werken tot de dag dat die omweg
                  er niet meer is en niemand begrijpt waarom het ooit lukte. */}
              {host.docker_host_omweg && (
                <p className="mt-1 text-xs text-warning">
                  Omweg actief: het ingestelde adres antwoordt niet, dus het werk
                  loopt nu via <code className="font-mono">{host.docker_host_omweg}</code> —
                  het adres waar deze machine zich vandaan meldt. Hij gaat er vanzelf
                  weer af zodra <code className="font-mono">{host.docker_host}</code> het
                  weer doet.
                </p>
              )}
              {/* Een docker-bridge-adres wijst naar een container op de
                  server zelf; dat is nooit een machine in je netwerk. */}
              {/^ssh:\/\/[^@]*@?(172\.(1[6-9]|2\d|3[01])\.|10\.88\.)/.test(adres) && (
                <p className="mt-1 text-xs text-warning">
                  Dit lijkt het adres van een container, niet van een computer
                  in je netwerk. Draait de agent daar, dan staat het goede
                  adres in zijn venster.
                </p>
              )}
            </div>
            <div>
              {/* Het guardmodel hoeft niet op de server te draaien. Hier
                  zeg jij wat deze machine erbij kan hebben -- LabX raadt dat
                  niet, want raden op iemands werkcomputer is precies wat je
                  niet wilt. */}
              <Label>Modelcontainers voor de data-guard</Label>
              <div className="flex gap-2">
                <Input value={runners} onChange={(e) => setRunners(e.target.value)}
                       onBlur={bewaarRunners} inputMode="numeric"
                       className="max-w-20" />
                <Button variant="ghost" onClick={bewaarRunners}
                        disabled={Number(runners) === (host.ollama_runners ?? 0)}>
                  Opslaan
                </Button>
              </div>
              <p className="mt-1 text-xs text-muted-foreground">
                0 = deze machine doet alleen labs. Elke container haalt het
                model één keer op (een paar honderd MB tot enkele GB) en
                bewaart het.
              </p>
            </div>

            <div>
              <Label>Maximaal aantal labs (leeg = geen grens)</Label>
              <div className="flex gap-2">
                <Input value={max} onChange={(e) => setMax(e.target.value)}
                       inputMode="numeric" placeholder="geen grens" className="max-w-28" />
                <Button variant="ghost" onClick={bewaarMax}>Opslaan</Button>
              </div>
            </div>
          </div>

          <div className="mb-3">
            <Label>Labs hier ({host.labs.length}{host.max_labs ? ` van ${host.max_labs}` : ""})</Label>
            {host.labs.length === 0 ? (
              <p className="text-xs text-muted-foreground">Nog geen. Verplaats er een vanuit het lab zelf.</p>
            ) : (
              <div className="flex flex-wrap gap-1">
                {host.labs.map((l) => (
                  <Link key={l.id} to={`/labs/${l.id}`}
                        className="rounded bg-secondary px-2 py-0.5 text-xs hover:bg-secondary/70">
                    {l.name}
                  </Link>
                ))}
              </div>
            )}
          </div>

          {uitslag && (
            <div className={`mb-2 rounded-md p-2 text-xs ${uitslag.ok
              ? "bg-success/10 text-success" : "bg-destructive/10 text-destructive"}`}>
              {uitslag.tekst}
            </div>
          )}

          {kanalen && (
            <div className="mb-2 grid gap-1 text-xs sm:grid-cols-2">
              <KanaalRegel naam="Laatst gemeld" ok={kanalen.opgeschreven.ok}
                           uitleg={kanalen.opgeschreven.laatst_gezien
                             ? new Date(kanalen.opgeschreven.laatst_gezien).toLocaleString("nl-NL")
                             : "nooit"} />
              <KanaalRegel naam="Hostagent" ok={kanalen.agent.ok}
                           uitleg={kanalen.agent.melding || kanalen.agent.url || ""} />
              <KanaalRegel naam="SSH" ok={kanalen.ssh.ok} uitleg={kanalen.ssh.melding} />
              <KanaalRegel naam="Docker" ok={kanalen.docker.ok} uitleg={kanalen.docker.melding} />
            </div>
          )}

          {afstemming && (
            <div className="mb-2 rounded-lg border border-border p-2 text-xs">
              <p className="font-medium">{afstemming.melding}</p>
              {afstemming.weg.map((x) => (
                <p key={x.lab_id} className="mt-1 text-amber-600 dark:text-amber-400">
                  {x.naam}: container {x.container_id} bestaat niet meer — starten bouwt hem
                  opnieuw op uit het volume.
                </p>
              ))}
              {afstemming.ander_label.map((x) => (
                <p key={x.lab_id} className="mt-1 text-amber-600 dark:text-amber-400">
                  {x.naam}: op de machine {x.op_de_machine}, in LabX {x.in_labx}.
                </p>
              ))}
              {afstemming.onbekend.map((x) => (
                <p key={x.container_id} className="mt-1 text-muted-foreground">
                  {x.naam} ({x.container_id}) draait daar, maar hoort bij geen lab hier —
                  waarschijnlijk van een andere LabX. Alleen gemeld, niet aangeraakt.
                </p>
              ))}
            </div>
          )}

          <div className="flex flex-wrap gap-2">
            <Button variant="ghost" onClick={test} busy={testen}>
              {testen ? <Loader2 className="h-4 w-4 animate-spin" /> : "Verbinding testen"}
            </Button>
            <Button variant="ghost" onClick={stemAf} busy={afstemmen}
                    title="Wat LabX denkt, naast wat er werkelijk op die machine staat">
              {afstemmen ? <Loader2 className="h-4 w-4 animate-spin" /> : "Afstemmen"}
            </Button>
            <Button variant="ghost" onClick={verwijder} title="Verwijderen">
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        </>
      )}
    </Card>
  );
}

function KoppelVenster({ host, onClose, onGekoppeld }: {
  host: LabHost; onClose: () => void; onGekoppeld: () => void;
}) {
  const [code, setCode] = useState("");
  const [sleutel, setSleutel] = useState<{ publieke_sleutel: string; commando: string } | null>(null);
  // Een redelijke gok die je meestal alleen hoeft te bevestigen; de agent
  // kent zijn eigen gebruikersnaam niet met zekerheid, dus intypen mag.
  const [adres, setAdres] = useState(
    host.docker_host || `ssh://gebruiker@${(host.agent_url || "").replace(/^https?:\/\//, "").split(":")[0]}`);
  const [bezig, setBezig] = useState(false);
  const [fout, setFout] = useState<string | null>(null);

  useEffect(() => {
    labHostsApi.sleutel().then(setSleutel).catch(() => setSleutel(null));
  }, []);

  async function koppel() {
    setBezig(true);
    setFout(null);
    try {
      await labHostsApi.koppelen(host.id, code.trim(), adres.trim());
      onGekoppeld();
    } catch (e) {
      setFout(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBezig(false);
    }
  }

  return (
    <Modal open onClose={onClose} title={`${host.naam} koppelen`} dirty={code.length > 0}>
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Een aanmelding is geen bewijs — iets op je netwerk zegt dat het een
          lab-host is. De code hieronder staat in het venster van de agent op
          die computer; dat je daar fysiek bij kunt, is wat hem toelaat.
        </p>

        <div>
          <Label>Koppelcode (6 cijfers, van het scherm van die machine)</Label>
          <Input value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                 inputMode="numeric" autoFocus placeholder="123456"
                 className="font-mono text-lg tracking-widest" />
        </div>

        <div>
          <Label>Docker-adres</Label>
          <Input value={adres} onChange={(e) => setAdres(e.target.value)}
                 placeholder="ssh://nick@werkpc.local" className="font-mono text-sm" />
          <p className="mt-1 text-xs text-muted-foreground">
            Hierlangs stuurt LabX de containers aan, over SSH. Er hoeft dus geen
            poort open te staan.
          </p>
        </div>

        {sleutel && (
          <div className="rounded-md border border-border bg-secondary/40 p-3">
            <div className="mb-1 flex items-center justify-between gap-2">
              <Label>Eenmalig op die computer</Label>
              <KopieerKnop tekst={sleutel.commando} label="Regel kopiëren" />
            </div>
            <p className="mb-2 text-xs text-muted-foreground">
              Zet Remote Login aan (macOS: Systeeminstellingen → Algemeen → Delen),
              zorg dat Docker er draait, en plak deze regel één keer in een
              terminal op die machine:
            </p>
            <pre className="max-h-24 overflow-auto rounded bg-background p-2 font-mono text-[10px] leading-relaxed">
              {sleutel.commando}
            </pre>
          </div>
        )}

        {fout && (
          <div className="rounded-md bg-destructive/10 p-2 text-sm text-destructive">{fout}</div>
        )}

        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>Annuleren</Button>
          <Button onClick={koppel} busy={bezig} disabled={code.length < 6 || !adres.trim()}>
            Koppelen
          </Button>
        </div>
      </div>
    </Modal>
  );
}

/**
 * De publieke sleutel van deze LabX, altijd vindbaar.
 *
 * Hij stond alleen in het koppelvenster, en dat venster is weg zodra een
 * machine gekoppeld is. Dan kun je hem nergens meer terugvinden -- terwijl je
 * hem juist dán nodig hebt: een machine die opnieuw is geïnstalleerd, een
 * authorized_keys die is opgeschoond, of gewoon een tweede computer.
 *
 * Een publieke sleutel is niet geheim; dat is het hele idee ervan. Open tonen
 * kost niets en scheelt een zoektocht.
 */
/**
 * Waar het guardmodel draait, en hoe druk het daar is.
 *
 * De data-guard vraagt een klein lokaal model om een tweede mening. Draaide
 * dat alleen op de server, dan ging een drukke guard-ronde ten koste van het
 * werk zelf. Hier zie je waar het nu gebeurt.
 */
function Modelpool() {
  const [runners, setRunners] = useState<ModelRunner[] | null>(null);

  useEffect(() => {
    const haal = () => labHostsApi.modelpool()
      .then((r) => setRunners(r.runners))
      .catch(() => setRunners([]));
    haal();
    const t = setInterval(haal, 10000);
    return () => clearInterval(t);
  }, []);

  if (!runners || runners.length === 0) return null;
  const klaar = runners.filter((r) => r.status === "klaar").length;

  return (
    <Card className="p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Brain className="h-4 w-4 text-muted-foreground" />
        <span className="text-sm font-semibold">Guardmodel</span>
        <span className="text-xs text-muted-foreground">
          {klaar} van {runners.length} klaar
        </span>
      </div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {runners.map((r) => (
          <div key={r.id} className="rounded-md border border-border p-2 text-xs">
            <div className="mb-1 flex items-center gap-2">
              <span className="truncate font-medium">{r.machine}</span>
              <Badge tone={r.status === "klaar" ? "green"
                           : r.status === "fout" ? "red" : "yellow"}>
                {r.status}
              </Badge>
              {r.onderweg > 0 && (
                <span className="ml-auto text-muted-foreground">
                  {r.onderweg} bezig
                </span>
              )}
            </div>
            {r.fout && <div className="text-muted-foreground">{r.fout}</div>}
          </div>
        ))}
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        Een vraag gaat naar de runner met het minste werk onderweg. Is er geen
        vrij, dan gebruikt de guard het model op de server — de controle valt
        dus nooit weg omdat de pool bezet is.
      </p>
    </Card>
  );
}

function SleutelVanLabX() {
  const [sleutel, setSleutel] = useState<{ publieke_sleutel: string; commando: string } | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    labHostsApi.sleutel().then(setSleutel).catch(() => setSleutel(null));
  }, []);

  if (!sleutel) return null;

  return (
    <Card className="p-4">
      <button className="flex w-full items-center justify-between text-left text-sm font-semibold"
              onClick={() => setOpen((v) => !v)}>
        <span className="flex items-center gap-2">
          <KeyRound className="h-4 w-4 text-muted-foreground" />
          De sleutel van deze LabX
        </span>
        <span className="text-xs font-normal text-muted-foreground">
          {open ? "verbergen" : "tonen"}
        </span>
      </button>
      {open && (
        <div className="mt-3 space-y-2 text-sm text-muted-foreground">
          <p>
            Hiermee logt LabX in op een lab-host. Zet deze regel één keer in
            een terminal op die computer; dan mag LabX er containers starten.
            Verder heeft die machine niets van LabX nodig.
          </p>
          <div className="flex items-start justify-between gap-2 rounded-md bg-secondary/50 p-3">
            <pre className="overflow-x-auto font-mono text-[10px] leading-relaxed">
              {sleutel.commando}
            </pre>
            <KopieerKnop tekst={sleutel.commando} label="Regel kopiëren" />
          </div>
          <p className="text-xs">
            Alleen de publieke helft — die is niet geheim. De private helft
            blijft in LabX en komt er nooit uit.
          </p>
        </div>
      )}
    </Card>
  );
}

function ZoDoeJeDatUitleg() {
  const [open, setOpen] = useState(true);
  // Het adres waarop die andere computer DEZE LabX bereikt. Niet "localhost":
  // dat is op die machine zijn eigen computer, en dan meldt hij zich bij
  // niets. Draai je hier wel op localhost, dan moet je je eigen LAN-adres
  // invullen -- dat zegt de tekst er dan ook bij.
  const basis = window.location.origin;
  const opLocalhost = /^https?:\/\/(localhost|127\.0\.0\.1)/.test(basis);
  // De gewone weg: één regel die het script van GitHub haalt en meteen
  // start. De terugval eronder is voor een machine zonder internet — die
  // haalt hetzelfde script bij deze LabX op, en heeft dan gegarandeerd de
  // versie die bij deze installatie hoort.
  const viaGithub =
    `curl -fsSL https://raw.githubusercontent.com/nickozz714/LabX/main/lab-host/install.sh \\\n`
    + `  | sh -s -- --labx ${basis}`;
  const viaLabX = `curl -fsSL ${basis}/api/lab-hosts/agent -o labx_lab_host.py\n`
    + `python3 labx_lab_host.py --labx ${basis}`;
  return (
    <Card className="p-4">
      <button className="flex w-full items-center justify-between text-left text-sm font-semibold"
              onClick={() => setOpen((v) => !v)}>
        Een computer toevoegen
        <span className="text-xs font-normal text-muted-foreground">{open ? "verbergen" : "tonen"}</span>
      </button>
      {open && (
        <div className="mt-3 space-y-3 text-sm text-muted-foreground">
          <p>
            Zet op die computer eerst <strong>Docker</strong> aan en{" "}
            <strong>externe login</strong> (macOS: Systeeminstellingen →
            Algemeen → Delen → Externe login). Plak daarna deze regel in een
            terminal op die machine; hij installeert de agent als
            achtergronddienst en controleert of die LabX bereikt:
          </p>
          <div className="rounded-md bg-secondary/50 p-3">
            <div className="flex items-start justify-between gap-2">
              <pre className="overflow-x-auto font-mono text-xs">{viaGithub}</pre>
              <KopieerKnop tekst={viaGithub} label="Kopiëren" />
            </div>
          </div>
          <details className="text-xs">
            <summary className="cursor-pointer">
              Die computer kan niet bij GitHub?
            </summary>
            <div className="mt-2 rounded-md bg-secondary/50 p-3">
              <div className="flex items-start justify-between gap-2">
                <pre className="overflow-x-auto font-mono text-xs">{viaLabX}</pre>
                <KopieerKnop tekst={viaLabX} label="Kopiëren" />
              </div>
            </div>
            <p className="mt-2">
              Deze LabX serveert hetzelfde script uit, dus je krijgt
              gegarandeerd de versie die bij deze installatie hoort.
            </p>
          </details>
          {opLocalhost && (
            <p className="rounded-md border border-warning/30 bg-warning/10 p-2 text-xs text-warning">
              Let op: je bekijkt LabX nu op <code>{basis}</code>. Op die andere
              computer wijst dat naar zichzélf. Vervang het door het adres
              waarop deze machine in je netwerk te bereiken is, bijvoorbeeld{" "}
              <code>http://192.168.2.10:8081</code>.
            </p>
          )}
          <p>
            Hij meldt zich elke 30 seconden, toont een koppelcode, en houdt de
            machine wakker zolang er labs op draaien. Commando&apos;s voert hij
            niet uit: dat gaat over SSH, zodat wie de agent overneemt niets kan
            starten.{" "}
            <a href="https://github.com/nickozz714/LabX/blob/main/lab-host/README.md"
               target="_blank" rel="noreferrer"
               className="underline underline-offset-2">
              Volledige instructies
            </a>.
          </p>
          <p>
            <strong className="text-foreground">Op een Mac</strong> kan die
            dienst de eerste keer door macOS van het lokale netwerk geweerd
            worden: de vraag om toestemming komt alleen in beeld bij een
            programma met een venster. Het script merkt dat en zegt welk vinkje
            je waar moet zetten — zonder die controle ziet zo&apos;n geweigerde
            dienst er bedrieglijk uit als &quot;hij draait&quot;.
          </p>
          <p>
            Hij toont dan een <strong>koppelcode</strong>. Druk hierboven bij die
            machine op &quot;Koppelen&quot;, vul die code in met het SSH-adres,
            en plak de sleutelregel uit dat venster één keer op die computer.
            Daarna zegt &quot;Verbinding testen&quot; of het klopt.
          </p>
          <p>
            Om het PATH hoef je niet te denken: de agent zorgt zelf dat docker
            ook vindbaar is in een SSH-sessie, want daar staat Docker Desktop
            standaard niet in.
          </p>
        </div>
      )}
    </Card>
  );
}
