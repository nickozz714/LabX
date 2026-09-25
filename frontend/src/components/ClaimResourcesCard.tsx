/**
 * components/ClaimResourcesCard.tsx — de catalogus van claimbare resources.
 *
 * Een lab is een sandbox-pc, en sinds een planning meerdere tickets in dezelfde
 * werker kan zetten kunnen er twee agents tegelijk achter die pc zitten. Het
 * meeste kan naast elkaar; de browser, een playground of een vaste poort niet.
 * Hier staat wát er te reserveren valt en wat er moet gebeuren als het bezet
 * is; per lab vink je aan welke ervan gelden.
 *
 * Het blijft een AFSPRAAK, geen slot: de agent claimt, LabX houdt bij wie wat
 * heeft, en niemand wordt technisch tegengehouden. Een echte vergrendeling zou
 * betekenen dat LabX weet wat een commando gaat aanraken, en dat weet het niet.
 */
import { useEffect, useState } from "react";
import { resourcesApi } from "@/lib/resources";
import type { ClaimResourceDto } from "@/lib/resources";
import { Button, Card, Input, Label, Select, TextArea, Toggle } from "@/components/ui";
import { ApiError } from "@/lib/api";

const LEEG = {
  key: "", label: "", description: "", scope: "werker", gedrag: "wachten",
  timeout_minutes: 30, default_on: false,
};

export function ClaimResourcesCard() {
  const [rows, setRows] = useState<ClaimResourceDto[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [nieuw, setNieuw] = useState<typeof LEEG | null>(null);
  const [fout, setFout] = useState<string | null>(null);

  function laad() {
    resourcesApi.list().then(setRows).catch(() => setRows([]));
  }
  useEffect(laad, []);

  async function patch(key: string, payload: Record<string, unknown>) {
    setFout(null);
    try {
      const bij = await resourcesApi.update(key, payload);
      setRows((cur) => cur.map((r) => (r.key === bij.key ? bij : r)));
    } catch (e) {
      setFout(e instanceof ApiError ? e.message : "Opslaan mislukt");
    }
  }

  async function maak() {
    setFout(null);
    try {
      await resourcesApi.create({ ...nieuw, key: (nieuw?.key || "").trim().toLowerCase() });
      setNieuw(null);
      laad();
    } catch (e) {
      setFout(e instanceof ApiError ? e.message : "Aanmaken mislukt");
    }
  }

  return (
    <Card className="p-4 space-y-3">
      <h2 className="text-sm font-semibold">Claimbare resources</h2>
      <p className="text-xs text-muted-foreground">
        Waar er in een lab maar één van is: de browser, een playground, een vaste poort. Draaien
        er twee sessies in dezelfde werker, dan reserveert een agent dit met{" "}
        <code>lab__resource__claim</code> en wacht de ander tot het vrij is. Per lab stel je in
        welke hiervan gelden.
      </p>
      {fout && <p className="text-sm text-destructive">{fout}</p>}

      <div className="divide-y divide-border rounded-md border border-border">
        {rows.map((row) => (
          <div key={row.key} className="p-2">
            <div className="flex items-center gap-2">
              <button className="flex-1 text-left text-sm"
                      onClick={() => setOpen(open === row.key ? null : row.key)}>
                <span className="font-mono">{row.key}</span>
                <span className="ml-2 text-muted-foreground">{row.label}</span>
                <span className="ml-2 text-[11px] text-muted-foreground">
                  · {row.scope} · {row.gedrag} · vervalt na {row.timeout_minutes} min
                  {row.default_on ? " · standaard aan" : ""}
                </span>
              </button>
              <Toggle checked={row.is_enabled}
                      onChange={(v) => patch(row.key, { is_enabled: v })} />
            </div>
            {open === row.key && (
              <div className="mt-2 space-y-2">
                {row.description && (
                  <p className="text-xs text-muted-foreground">{row.description}</p>
                )}
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <Label>Waar geldt hij</Label>
                    <Select value={row.scope} onChange={(e) => patch(row.key, { scope: e.target.value })}>
                      <option value="werker">Per werker (één per container)</option>
                      <option value="lab">Per lab (over de werkers heen)</option>
                    </Select>
                  </div>
                  <div>
                    <Label>Als hij bezet is</Label>
                    <Select value={row.gedrag} onChange={(e) => patch(row.key, { gedrag: e.target.value })}>
                      <option value="wachten">Wachten tot hij vrijkomt</option>
                      <option value="weigeren">Meteen melden dat hij bezet is</option>
                    </Select>
                  </div>
                  <div>
                    <Label>Vervalt na (minuten)</Label>
                    <Input type="number" defaultValue={row.timeout_minutes}
                           onBlur={(e) => patch(row.key, { timeout_minutes: Number(e.target.value) })} />
                  </div>
                  <div className="flex items-end">
                    <Toggle checked={row.default_on}
                            onChange={(v) => patch(row.key, { default_on: v })}
                            label="Standaard aan in een nieuw lab" />
                  </div>
                </div>
                <p className="text-[11px] text-muted-foreground">
                  Vervallen is het vangnet voor een agent die crasht of vergeet vrij te geven —
                  zonder dat staat de browser voorgoed op slot. Te kort is ook niet goed: dan
                  raakt iemand hem kwijt terwijl hij nog bezig is.
                </p>
                {!row.builtin && (
                  <Button variant="ghost"
                          onClick={() => resourcesApi.remove(row.key).then(laad)}>
                    Verwijderen
                  </Button>
                )}
              </div>
            )}
          </div>
        ))}
      </div>

      {nieuw ? (
        <div className="space-y-2 rounded-md border border-border p-2">
          <div className="grid grid-cols-2 gap-2">
            <div>
              <Label>Sleutel (wat de agent claimt)</Label>
              <Input value={nieuw.key} placeholder="poort-5173"
                     onChange={(e) => setNieuw({ ...nieuw, key: e.target.value })} />
            </div>
            <div>
              <Label>Naam</Label>
              <Input value={nieuw.label} onChange={(e) => setNieuw({ ...nieuw, label: e.target.value })} />
            </div>
          </div>
          <div>
            <Label>Waarom er maar één van is</Label>
            <TextArea rows={2} value={nieuw.description}
                      onChange={(e) => setNieuw({ ...nieuw, description: e.target.value })} />
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setNieuw(null)}>Annuleren</Button>
            <Button onClick={maak} disabled={!nieuw.key.trim()}>Toevoegen</Button>
          </div>
        </div>
      ) : (
        <Button variant="secondary" onClick={() => setNieuw({ ...LEEG })}>Resource toevoegen</Button>
      )}
    </Card>
  );
}
