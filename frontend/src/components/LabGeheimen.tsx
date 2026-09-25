/**
 * components/LabGeheimen.tsx — tokens en sleutels van een lab.
 *
 * Waarom dit scherm bestaat en niet gewoon "zet je token in een env-var": een
 * commando is TEKST. Het staat in het audit-spoor, het komt als tool-invoer
 * terug bij het model, en het lekt zodra een script iets echoot. Een token dat
 * één keer in een commando staat, is daarmee overal.
 *
 * Hier leg je hem één keer vast onder een naam, en overal daarna schrijf je
 * `{{secret:naam}}`. LabX vult dat pas in de container in, via een bestand dat
 * alleen root daar kan lezen — de waarde staat dus op geen enkele
 * commandoregel.
 *
 * Twee soorten, en de tweede is de interessante: een geheim van het soort
 * COMMANDO wordt door LabX zelf vers gemaakt zodra hij te oud is. Dat is de
 * goede vorm voor een Azure-token, dat een uur meegaat — niemand hoeft er dan
 * ooit nog aan te denken.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { KeyRound, RefreshCw, Trash2 } from "lucide-react";
import { ApiError } from "@/lib/api";
import { labsApi, type LabGeheim } from "@/lib/labs";
import { secretApi, type SecretDto } from "@/lib/secrets";
import type { Lab } from "@/lib/types";
import { Badge, Button, Input, Label, Select, TextArea } from "@/components/ui";
import { useBevestiging } from "@/components/Bevestiging";

const VOORBEELD =
  "az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv";

function tijd(waarde: string | null): string {
  if (!waarde) return "nooit";
  return new Date(waarde).toLocaleString(undefined, {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function LabGeheimen({ lab }: { lab: Lab }) {
  const bevestig = useBevestiging();
  const [rijen, setRijen] = useState<LabGeheim[]>([]);
  // De kluis die niet aan één lab hangt. Hij werkte hier al — je kon alleen
  // nergens zien dát hij bestond, dus hij werd niet gebruikt.
  const [kluis, setKluis] = useState<SecretDto[]>([]);
  const [nieuw, setNieuw] = useState(false);
  const [naam, setNaam] = useState("");
  const [soort, setSoort] = useState<"commando" | "waarde">("commando");
  const [waarde, setWaarde] = useState("");
  const [commando, setCommando] = useState(VOORBEELD);
  const [omschrijving, setOmschrijving] = useState("");
  const [ttl, setTtl] = useState(50);
  const [melding, setMelding] = useState<string | null>(null);
  const [bezig, setBezig] = useState(false);

  async function laad() {
    try {
      setRijen(await labsApi.secrets(lab.id));
    } catch {
      /* een lab zonder geheimen is geen fout */
    }
    try {
      setKluis(await secretApi.list(lab.id));
    } catch {
      setKluis([]);
    }
  }

  useEffect(() => {
    laad();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lab.id]);

  async function bewaar() {
    setBezig(true);
    setMelding(null);
    try {
      await labsApi.putSecret(lab.id, naam.trim(), {
        description: omschrijving || null,
        ...(soort === "commando"
          ? { command: commando, ttl_minutes: ttl }
          : { value: waarde }),
      });
      setNieuw(false);
      setNaam(""); setWaarde(""); setOmschrijving("");
      await laad();
    } catch (e) {
      setMelding(e instanceof ApiError ? e.message : "Opslaan mislukt");
    } finally {
      setBezig(false);
    }
  }

  async function test(g: LabGeheim) {
    setBezig(true);
    setMelding(null);
    try {
      const r = await labsApi.testSecret(lab.id, g.name);
      setMelding(r.ok
        ? `'${g.name}' werkt — waarde van ${r.lengte} tekens opgehaald.`
        : `'${g.name}' leverde niets op: ${r.last_error || "onbekende reden"}`);
      await laad();
    } catch (e) {
      setMelding(e instanceof ApiError ? e.message : "Testen mislukt");
    } finally {
      setBezig(false);
    }
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        Een commando is tekst: het staat in het audit-spoor, het komt terug bij het model, en
        het lekt zodra een script iets echoot. Leg een token daarom hier één keer vast en
        schrijf overal <code>{"{{secret:naam}}"}</code>. LabX vult dat pas in de container in,
        dus de waarde staat op geen enkele commandoregel — en komt hij tóch in de uitvoer
        terecht, dan wordt hij daar weggehaald.
      </p>

      {melding && <p className="text-xs text-muted-foreground">{melding}</p>}

      <div className="divide-y divide-border rounded-md border border-border">
        {rijen.map((g) => (
          <div key={g.name} className="flex flex-wrap items-center gap-2 p-2 text-sm">
            <KeyRound size={13} className="text-muted-foreground" />
            <code className="rounded bg-secondary px-1 text-xs">{g.placeholder}</code>
            <Badge tone={g.kind === "commando" ? "violet" : "neutral"}>{g.kind}</Badge>
            {!g.has_value && <Badge tone="yellow">nog geen waarde</Badge>}
            {g.last_error && <Badge tone="red">fout</Badge>}
            <span className="text-[11px] text-muted-foreground">
              {g.description ? `${g.description} · ` : ""}
              ververst {tijd(g.refreshed_at)}
              {g.kind === "commando" ? ` · elke ${g.ttl_minutes} min` : ""}
            </span>
            <div className="ml-auto flex items-center gap-1">
              <Button variant="ghost" className="text-xs" disabled={bezig}
                      title="Haal de waarde nu op en kijk of het werkt"
                      onClick={() => test(g)}>
                <RefreshCw size={12} />
              </Button>
              <Button variant="ghost" className="text-xs text-destructive" disabled={bezig}
                      onClick={async () => {
                        const ja = await bevestig.vraag({
                          titel: `Geheim '${g.name}' verwijderen?`,
                          tekst: "De waarde is daarna weg; wie hem nodig heeft moet hem opnieuw invoeren.",
                          bevestig: "Verwijderen",
                        });
                        if (ja) await labsApi.deleteSecret(lab.id, g.name).then(laad);
                      }}>
                <Trash2 size={12} />
              </Button>
            </div>
            {g.last_error && (
              <p className="w-full text-[11px] text-destructive">{g.last_error}</p>
            )}
          </div>
        ))}
        {rijen.length === 0 && (
          <p className="p-3 text-xs text-muted-foreground">
            Nog geen geheimen van dit lab. Voor een Azure- of Fabric-token kies je
            <em> commando</em>: LabX haalt hem dan zelf op en ververst hem vanzelf.
          </p>
        )}
      </div>

      {/* De kluis erbij. Een geheim dat overal geldt is hier net zo goed te
          gebruiken als een van dit lab; zonder deze lijst was dat alleen
          nergens te zien, en dan bestaat het in de praktijk niet. */}
      <div>
        <div className="mb-1 flex items-center gap-2">
          <Label>Uit de kluis — geldt in elk lab</Label>
          <Link to="/settings" className="text-[11px] text-primary underline">beheren</Link>
        </div>
        <div className="divide-y divide-border rounded-md border border-dashed border-border">
          {kluis.map((g) => (
            <div key={g.name} className="flex flex-wrap items-center gap-2 p-2 text-sm">
              <KeyRound size={13} className="text-muted-foreground" />
              <code className="rounded bg-secondary px-1 text-xs">{g.placeholder}</code>
              <Badge tone="neutral">kluis</Badge>
              {g.lab_ids.length > 0 && <Badge tone="violet">alleen bepaalde labs</Badge>}
              <span className="text-[11px] text-muted-foreground">
                {g.description ? `${g.description} · ` : ""}{g.use_count}× gebruikt
              </span>
            </div>
          ))}
          {kluis.length === 0 && (
            <p className="p-3 text-xs text-muted-foreground">
              De kluis is leeg. Zet er bij <Link to="/settings" className="text-primary underline">
              Instellingen &gt; Geheimen</Link> een webhook-URL, API-sleutel of wachtwoord in;
              die is daarna in élk lab te gebruiken met <code>{"{{secret:naam}}"}</code> — in
              een commando, een tool-argument, een skill of een workflow-stap. Een geheim van
              één klant beperk je daar tot diens lab.
            </p>
          )}
        </div>
      </div>

      {!nieuw ? (
        <Button variant="secondary" className="text-xs" onClick={() => setNieuw(true)}>
          Geheim toevoegen
        </Button>
      ) : (
        <div className="space-y-2 rounded-md border border-border p-3">
          <div className="grid grid-cols-2 gap-2">
            <div>
              <Label>Naam</Label>
              <Input value={naam} onChange={(e) => setNaam(e.target.value)}
                     placeholder="fabric" />
              <p className="mt-1 text-[11px] text-muted-foreground">
                Letters, cijfers, - en _. Je gebruikt hem als{" "}
                <code>{`{{secret:${naam || "naam"}}}`}</code>.
              </p>
            </div>
            <div>
              <Label>Soort</Label>
              <Select value={soort} onChange={(e) => setSoort(e.target.value as any)}>
                <option value="commando">Commando — LabX haalt hem op en ververst hem</option>
                <option value="waarde">Vaste waarde — plak hem er zelf in</option>
              </Select>
            </div>
          </div>

          {soort === "commando" ? (
            <>
              <div>
                <Label>Commando dat de waarde maakt (draait in dit lab)</Label>
                <TextArea rows={2} className="font-mono text-xs" value={commando}
                          onChange={(e) => setCommando(e.target.value)} />
              </div>
              <div>
                <Label>Hoe lang blijft hij bruikbaar (minuten)</Label>
                <Input type="number" className="w-28" value={ttl}
                       onChange={(e) => setTtl(Number(e.target.value))} />
                <p className="mt-1 text-[11px] text-muted-foreground">
                  Een Azure-token leeft een uur; 50 minuten laat ruimte voor een script dat
                  hem aan het begin ophaalt.
                </p>
              </div>
            </>
          ) : (
            <div>
              <Label>Waarde</Label>
              <Input type="password" value={waarde} onChange={(e) => setWaarde(e.target.value)} />
              <p className="mt-1 text-[11px] text-muted-foreground">
                Wordt versleuteld opgeslagen en nooit meer getoond — ook niet aan jou.
              </p>
            </div>
          )}

          <div>
            <Label>Omschrijving (optioneel)</Label>
            <Input value={omschrijving} onChange={(e) => setOmschrijving(e.target.value)}
                   placeholder="waar dit voor is" />
          </div>

          <div className="flex items-center gap-2">
            <Button className="text-xs" disabled={bezig || !naam.trim()} onClick={bewaar}>
              Opslaan
            </Button>
            <Button variant="ghost" className="text-xs" onClick={() => setNieuw(false)}>
              Annuleren
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
