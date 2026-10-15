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
import type { LabHost } from "@/lib/types";
import { Badge, Button, Card, EmptyState, Input, Label, Modal } from "@/components/ui";
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

      <div className="mt-6">
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
  const melding = useMelding();
  const bevestig = useBevestiging();

  async function test() {
    setTesten(true);
    setUitslag(null);
    try {
      const r = await labHostsApi.test(host.id);
      setUitslag({
        ok: r.ok,
        tekst: r.ok
          ? `Verbonden met ${r.docker_host}`
          : (r.detail || r.diagnose?.hint || "Geen verbinding met de Docker van die machine."),
      });
    } catch (e) {
      setUitslag({ ok: false, tekst: e instanceof ApiError ? e.message : String(e) });
    } finally {
      setTesten(false);
    }
  }

  async function bewaarMax() {
    const n = max.trim() === "" ? null : Math.max(1, Number(max));
    if (n !== null && !Number.isFinite(n)) return;
    await labHostsApi.wijzig(host.id, { max_labs: n });
    melding.ok("Opgeslagen.");
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
              <Label>Docker-adres</Label>
              <div className="truncate rounded-md bg-secondary/50 px-2 py-1 font-mono text-xs">
                {host.docker_host || "—"}
              </div>
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

          <div className="flex gap-2">
            <Button variant="ghost" onClick={test} busy={testen}>
              {testen ? <Loader2 className="h-4 w-4 animate-spin" /> : "Verbinding testen"}
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

function ZoDoeJeDatUitleg() {
  const [open, setOpen] = useState(false);
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
            Op de computer die mee moet doen draait Docker, en start je de
            LabX-agent:
          </p>
          <pre className="overflow-x-auto rounded-md bg-secondary/50 p-3 font-mono text-xs">
{`LABX_URL=https://labx.jouwserver.nl \\
python3 host-agent/labx_lab_host.py`}
          </pre>
          <p>
            Hij meldt zich elke 30 seconden, toont een koppelcode, en houdt de
            machine wakker zolang er labs op draaien. Commando&apos;s voert hij
            niet uit: dat gaat over SSH, zodat wie de agent overneemt niets kan
            starten.
          </p>
          <p>
            Zorg daarom dat de server met SSH bij die machine kan —{" "}
            <code className="rounded bg-secondary/50 px-1 font-mono text-xs">
              ssh-copy-id gebruiker@die-machine
            </code>{" "}
            — en gebruik daarna hierboven &quot;Verbinding testen&quot;.
          </p>
        </div>
      )}
    </Card>
  );
}
