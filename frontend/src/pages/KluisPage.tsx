/**
 * pages/KluisPage.tsx — de kluis: geheimen die in élk lab gelden.
 *
 * Eén plek voor een webhook-URL, een API-sleutel of een wachtwoord, en overal
 * in LabX verwijs je ernaar met `{{secret:naam}}`. Dat is bewust een eigen
 * pagina en geen kaartje onderaan de instellingen: het is iets dat je erbij
 * pakt terwijl je een skill schrijft of een ticket opstelt, niet iets dat je
 * één keer instelt en nooit meer opent.
 *
 * De afspraak die dit veilig maakt staat in services/secrets/vault.py: een
 * verwijzing wordt alleen ingevuld op weg naar BUITEN — een commando, een
 * tool-aanroep — en nooit op weg naar het model. De agent mag geheimen KIEZEN,
 * niet KENNEN. Daarom is er ook geen knop die een waarde laat zien, ook niet
 * voor jou: wat je niet kunt tonen, kun je ook niet per ongeluk in een prompt
 * plakken.
 */
import { useEffect, useState } from "react";
import { Copy, KeyRound, RefreshCw, Trash2 } from "lucide-react";
import { secretApi, type SecretDto } from "@/lib/secrets";
import { labsApi } from "@/lib/labs";
import type { Lab } from "@/lib/types";
import { Badge, Button, Card, EmptyState, Input, Label, TextArea } from "@/components/ui";
import { useMelding } from "@/components/Meldingen";
import { useBevestiging } from "@/components/Bevestiging";
import { ApiError } from "@/lib/api";
import { vergeetGeheimen } from "@/components/GeheimInvoegen";

const LEEG = { name: "", value: "", description: "", lab_ids: [] as string[] };

function tijd(waarde: string | null): string {
  return waarde ? new Date(waarde).toLocaleString(undefined, {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "nooit";
}

export function KluisPage() {
  const melding = useMelding();
  const bevestig = useBevestiging();
  const [rijen, setRijen] = useState<SecretDto[]>([]);
  const [labs, setLabs] = useState<Lab[]>([]);
  const [nieuw, setNieuw] = useState<typeof LEEG | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [fout, setFout] = useState<string | null>(null);

  function laad() {
    // De kiezer in andere schermen deelt één opgehaalde lijst; na een
    // wijziging hoort die opnieuw opgehaald te worden, anders kun je een
    // zojuist toegevoegd geheim daar nog niet kiezen.
    vergeetGeheimen();
    secretApi.list().then(setRijen).catch(() => setRijen([]));
  }
  useEffect(() => {
    laad();
    labsApi.list().then(setLabs).catch(() => {});
  }, []);

  async function bewaar(naam: string, payload: Record<string, unknown>) {
    setFout(null);
    try {
      await secretApi.put(naam, payload);
      setNieuw(null);
      laad();
      melding.ok(`'${naam}' opgeslagen`);
    } catch (e) {
      setFout(e instanceof ApiError ? e.message : "Opslaan mislukt");
    }
  }

  return (
    <div className="space-y-4 p-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">Kluis</h1>
        <Badge tone="neutral">{rijen.length} geheim(en)</Badge>
        <span className="flex-1" />
        {!nieuw && (
          <Button onClick={() => setNieuw({ ...LEEG })}>Geheim toevoegen</Button>
        )}
      </div>

      <Card className="space-y-2 p-4 text-xs text-muted-foreground">
        <p>
          Zet hier een webhook-URL, API-sleutel of wachtwoord neer en schrijf overal
          <code className="mx-1">{"{{secret:naam}}"}</code>waar de waarde zou staan. Dat werkt
          in <strong>elk lab</strong> en op elke plek waar je tekst typt: een shell-commando,
          een argument van een tool, een <strong>skill</strong>, een <strong>bericht in de
          chat</strong>, een <strong>board-ticket</strong> en een workflow-stap.
        </p>
        <p>
          De waarde wordt pas ingevuld op het laatste moment, op weg naar buiten — nooit op weg
          naar het model. In je prompt, het gesprek en het audit-spoor staat dus alleen de naam,
          en komt een waarde tóch in de uitvoer terug, dan wordt hij daar weggehaald. De agent
          kan geheimen kiezen, niet kennen; hij ziet de namen met <code>lab__secret_list</code>.
        </p>
        <p>
          Hoort een geheim bij één klant, beperk hem dan tot diens lab. Een lab kan dezelfde
          naam overschrijven met een eigen geheim — dat is de specifiekere afspraak, en handig
          voor iets dat in die container ontstaat (een token dat elk uur ververst).
        </p>
      </Card>

      {fout && <p className="text-sm text-destructive">{fout}</p>}

      {nieuw && (
        <Card className="space-y-3 p-4">
          <div className="grid gap-3 md:grid-cols-2">
            <div>
              <Label>Naam</Label>
              <Input value={nieuw.name} placeholder="teams-incidenten-webhook"
                     onChange={(e) => setNieuw({ ...nieuw, name: e.target.value })} />
              <p className="mt-1 text-[11px] text-muted-foreground">
                Je schrijft hem als{" "}
                <code>{`{{secret:${nieuw.name.trim() || "naam"}}}`}</code>. Letters, cijfers,
                <code className="mx-1">-</code>en<code className="mx-1">_</code>.
              </p>
            </div>
            <div>
              <Label>Waarde</Label>
              <Input type="password" value={nieuw.value} placeholder="plak hem hier"
                     onChange={(e) => setNieuw({ ...nieuw, value: e.target.value })} />
              <p className="mt-1 text-[11px] text-muted-foreground">
                Gaat er versleuteld in en komt er nooit meer uit — ook niet voor jou.
              </p>
            </div>
          </div>
          <div>
            <Label>Waar is dit voor</Label>
            <TextArea rows={2} value={nieuw.description}
                      placeholder="De Power Automate-webhook van het incidentenkanaal"
                      onChange={(e) => setNieuw({ ...nieuw, description: e.target.value })} />
          </div>
          <LabKeuze labs={labs} gekozen={nieuw.lab_ids}
                    onChange={(ids) => setNieuw({ ...nieuw, lab_ids: ids })} />
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setNieuw(null)}>Annuleren</Button>
            <Button disabled={!nieuw.name.trim() || !nieuw.value}
                    onClick={() => bewaar(nieuw.name.trim(), {
                      value: nieuw.value, description: nieuw.description,
                      lab_ids: nieuw.lab_ids })}>
              Opslaan
            </Button>
          </div>
        </Card>
      )}

      {rijen.length === 0 && !nieuw ? (
        <EmptyState>
          <p className="font-medium text-foreground">De kluis is leeg</p>
          <p className="mx-auto mt-1 max-w-lg">
            Zet er iets in dat nu nog in een skill of een commando staat — een webhook-URL
            bijvoorbeeld — en vervang het daar door de verwijzing.
          </p>
          <Button className="mt-3" onClick={() => setNieuw({ ...LEEG })}>Geheim toevoegen</Button>
        </EmptyState>
      ) : (
        <div className="divide-y divide-border rounded-md border border-border">
          {rijen.map((r) => (
            <div key={r.name} className="p-3">
              <div className="flex flex-wrap items-center gap-2">
                <KeyRound size={14} className="text-muted-foreground" />
                <button className="text-left font-mono text-sm"
                        onClick={() => setOpen(open === r.name ? null : r.name)}>
                  {r.placeholder}
                </button>
                <Button variant="ghost" className="px-1 text-xs" title="Verwijzing kopiëren"
                        onClick={() => {
                          navigator.clipboard?.writeText(r.placeholder);
                          melding.ok("Verwijzing gekopieerd");
                        }}>
                  <Copy size={12} />
                </Button>
                {r.description && (
                  <span className="text-xs text-muted-foreground">{r.description}</span>
                )}
                <span className="flex-1" />
                <Badge tone={r.lab_ids.length ? "violet" : "green"}>
                  {r.lab_ids.length
                    ? `${r.lab_ids.length} lab(s)`
                    : "elk lab"}
                </Badge>
                <span className="text-[11px] text-muted-foreground">
                  {r.use_count}× gebruikt · laatst {tijd(r.last_used_at)}
                </span>
              </div>

              {open === r.name && (
                <div className="mt-3 space-y-3 border-t border-border pt-3">
                  <div className="grid gap-3 md:grid-cols-2">
                    <div>
                      <Label>Nieuwe waarde</Label>
                      <Input type="password" placeholder="leeg laten = ongewijzigd"
                             onBlur={(e) => e.target.value
                               && bewaar(r.name, { value: e.target.value })} />
                    </div>
                    <div>
                      <Label>Waar is dit voor</Label>
                      <Input defaultValue={r.description || ""}
                             onBlur={(e) => bewaar(r.name, { description: e.target.value })} />
                    </div>
                  </div>
                  <LabKeuze labs={labs} gekozen={r.lab_ids}
                            onChange={(ids) => bewaar(r.name, { lab_ids: ids })} />
                  <div className="flex gap-2">
                    <Button variant="ghost" className="text-xs"
                            title="Bestaat hij en is hij te ontsleutelen?"
                            onClick={() => secretApi.test(r.name).then((t) => (
                              t.ok
                                ? melding.ok(`'${r.name}': ${t.lengte} tekens, begint met ${t.begint_met}`)
                                : melding.fout(`'${r.name}'`, t.melding || "onbruikbaar")))}>
                      <RefreshCw size={12} className="mr-1" /> Controleren
                    </Button>
                    <Button variant="danger" className="text-xs"
                            onClick={async () => {
                              const ja = await bevestig.vraag({
                                titel: `'${r.name}' verwijderen?`,
                                tekst: "De waarde is daarna weg. Verwijzingen die er nog naar "
                                       + "wijzen stoppen de aanroep in plaats van hem leeg te "
                                       + "versturen.",
                                bevestig: "Verwijderen",
                              });
                              if (ja) await secretApi.remove(r.name).then(laad);
                            }}>
                      <Trash2 size={12} className="mr-1" /> Verwijderen
                    </Button>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** In welke labs dit geheim geldt. Niets aangevinkt = overal — dat is het
 *  normale geval, dus het hoeft geen keuze te zijn die je moet maken. */
function LabKeuze({ labs, gekozen, onChange }: {
  labs: Lab[];
  gekozen: string[];
  onChange: (ids: string[]) => void;
}) {
  return (
    <div>
      <Label>Waar geldt hij</Label>
      <div className="flex flex-wrap gap-2">
        <button onClick={() => onChange([])}
                className={`rounded-md border px-2 py-1 text-xs ${
                  gekozen.length === 0 ? "border-primary bg-primary/10"
                                       : "border-border text-muted-foreground"}`}>
          Elk lab
        </button>
        {labs.map((l) => {
          const aan = gekozen.includes(l.id);
          return (
            <button key={l.id}
                    onClick={() => onChange(aan ? gekozen.filter((x) => x !== l.id)
                                                : [...gekozen, l.id])}
                    className={`rounded-md border px-2 py-1 text-xs ${
                      aan ? "border-primary bg-primary/10"
                          : "border-border text-muted-foreground"}`}>
              {l.name}
            </button>
          );
        })}
      </div>
      <p className="mt-1 text-[11px] text-muted-foreground">
        Een sleutel van de ene klant hoort niet te werken in het lab van een andere.
      </p>
    </div>
  );
}
