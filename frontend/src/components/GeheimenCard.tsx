/**
 * components/GeheimenCard.tsx — de kluis beheren.
 *
 * Eén plek waar je een webhook-URL, een API-sleutel of een wachtwoord neerzet,
 * en overal in LabX naar verwijst met `{{secret:naam}}`. De waarde komt er
 * alleen in; er is geen knop die hem laat zien, ook niet voor jou — wat je niet
 * kunt tonen, kun je ook niet per ongeluk in een prompt plakken.
 *
 * De agent kan geheimen KIEZEN maar niet KENNEN: hij ziet de namen (via
 * lab__secret_list) en schrijft de verwijzing in zijn tool-argument; LabX vult
 * hem vlak voor de aanroep in.
 */
import { useEffect, useState } from "react";
import { secretApi } from "@/lib/secrets";
import type { SecretDto } from "@/lib/secrets";
import { labsApi } from "@/lib/labs";
import type { Lab } from "@/lib/types";
import { Badge, Button, Card, Input, Label, TextArea } from "@/components/ui";
import { ApiError } from "@/lib/api";

const LEEG = { name: "", value: "", description: "", lab_ids: [] as string[] };

export function GeheimenCard() {
  const [rijen, setRijen] = useState<SecretDto[]>([]);
  const [labs, setLabs] = useState<Lab[]>([]);
  const [nieuw, setNieuw] = useState<typeof LEEG | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [fout, setFout] = useState<string | null>(null);
  const [melding, setMelding] = useState<string | null>(null);

  function laad() {
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
    } catch (e) {
      setFout(e instanceof ApiError ? e.message : "Opslaan mislukt");
    }
  }

  return (
    <Card className="p-4 space-y-3">
      <h2 className="text-sm font-semibold">Geheimen</h2>
      <p className="text-xs text-muted-foreground">
        Eén plek voor webhook-URL&apos;s, API-sleutels en wachtwoorden. Verwijs er overal naar
        met <code>{"{{secret:naam}}"}</code> — in een skill, een tool-argument, een workflow-stap
        of een commando. LabX vult de waarde pas in op weg naar buiten; in je prompt, je
        gesprek en het audit-spoor staat alleen de naam. De agent kan ze dus kiezen, niet kennen.
      </p>
      {fout && <p className="text-sm text-destructive">{fout}</p>}
      {melding && <p className="text-xs text-muted-foreground">{melding}</p>}

      <div className="divide-y divide-border rounded-md border border-border">
        {rijen.map((r) => (
          <div key={r.name} className="p-2">
            <div className="flex flex-wrap items-center gap-2">
              <button className="flex-1 text-left text-sm"
                      onClick={() => setOpen(open === r.name ? null : r.name)}>
                <code className="font-mono">{r.placeholder}</code>
                {r.description && (
                  <span className="ml-2 text-muted-foreground">{r.description}</span>
                )}
              </button>
              {r.lab_ids.length > 0 && (
                <Badge tone="violet">{r.lab_ids.length} lab(s)</Badge>
              )}
              <span className="text-[11px] text-muted-foreground">
                {r.use_count}× gebruikt
                {r.last_used_at && ` · ${new Date(r.last_used_at).toLocaleDateString()}`}
              </span>
            </div>
            {open === r.name && (
              <div className="mt-2 space-y-2">
                <div>
                  <Label>Nieuwe waarde (leeg = laat de bestaande staan)</Label>
                  <Input type="password" placeholder="••••••••"
                         onBlur={(e) => e.target.value
                           && bewaar(r.name, { value: e.target.value })} />
                </div>
                <div>
                  <Label>Omschrijving</Label>
                  <Input defaultValue={r.description || ""}
                         onBlur={(e) => bewaar(r.name, { description: e.target.value })} />
                </div>
                <div>
                  <Label>Alleen in deze labs (niets aangevinkt = overal)</Label>
                  <div className="flex flex-wrap gap-2">
                    {labs.map((l) => {
                      const aan = r.lab_ids.includes(l.id);
                      return (
                        <button key={l.id}
                                onClick={() => bewaar(r.name, {
                                  lab_ids: aan ? r.lab_ids.filter((x) => x !== l.id)
                                               : [...r.lab_ids, l.id] })}
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
                <div className="flex gap-2">
                  <Button variant="ghost"
                          onClick={() => secretApi.test(r.name).then((t) => setMelding(
                            t.ok ? `'${r.name}': ${t.lengte} tekens, begint met ${t.begint_met}`
                                 : `'${r.name}': ${t.melding}`))}>
                    Controleren
                  </Button>
                  <Button variant="danger"
                          onClick={() => secretApi.remove(r.name).then(laad)}>
                    Verwijderen
                  </Button>
                </div>
              </div>
            )}
          </div>
        ))}
        {rijen.length === 0 && (
          <p className="p-3 text-xs text-muted-foreground">Nog geen geheimen.</p>
        )}
      </div>

      {nieuw ? (
        <div className="space-y-2 rounded-md border border-border p-2">
          <div className="grid grid-cols-2 gap-2">
            <div>
              <Label>Naam (wordt {"{{secret:naam}}"})</Label>
              <Input value={nieuw.name} placeholder="teams-webhook"
                     onChange={(e) => setNieuw({ ...nieuw, name: e.target.value })} />
            </div>
            <div>
              <Label>Waarde</Label>
              <Input type="password" value={nieuw.value}
                     onChange={(e) => setNieuw({ ...nieuw, value: e.target.value })} />
            </div>
          </div>
          <div>
            <Label>Waar is dit voor</Label>
            <TextArea rows={2} value={nieuw.description}
                      onChange={(e) => setNieuw({ ...nieuw, description: e.target.value })} />
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setNieuw(null)}>Annuleren</Button>
            <Button disabled={!nieuw.name.trim() || !nieuw.value}
                    onClick={() => bewaar(nieuw.name.trim(), {
                      value: nieuw.value, description: nieuw.description })}>
              Opslaan
            </Button>
          </div>
        </div>
      ) : (
        <Button variant="secondary" onClick={() => setNieuw({ ...LEEG })}>Geheim toevoegen</Button>
      )}
    </Card>
  );
}
