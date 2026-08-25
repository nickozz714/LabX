/**
 * components/BoardSettings.tsx — instellingen van één board.
 *
 * Vier blokken: algemeen (naam, lab), kolommen, de agent (welke kolom hij
 * leegwerkt en met welke vaste instructie) en de koppeling met Azure DevOps /
 * Jira. De providervelden komen uit de backend (`/boards/providers`), zodat een
 * nieuwe bron hier niets hoeft te veranderen.
 *
 * De statusmapping (kolom -> externe status) heeft een "verbinding testen"-knop
 * die de echte statusnamen uit de bron ophaalt: de mapping goed raden is niet
 * te doen, en een verkeerde naam faalt pas bij de eerste push.
 */
import { useEffect, useState } from "react";
import { boardApi } from "@/lib/boards";
import { labsApi } from "@/lib/labs";
import type { BoardColumnDto, BoardDto, Lab, ProviderSpec } from "@/lib/types";
import { Badge, Button, Card, Input, Label, Modal, Select, TextArea } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { Info } from "lucide-react";

export function BoardSettings({
  board, onClose, onSaved, onDeleted,
}: {
  board: BoardDto;
  onClose: () => void;
  onSaved: () => void;
  onDeleted: () => void;
}) {
  const [name, setName] = useState(board.name);
  const [description, setDescription] = useState(board.description || "");
  const [keyPrefix, setKeyPrefix] = useState(board.key_prefix);
  const [labId, setLabId] = useState(board.lab_id || "");
  const [columns, setColumns] = useState<BoardColumnDto[]>(board.columns);
  const [agentColumn, setAgentColumn] = useState(board.agent_column || "");
  const [agentDoneColumn, setAgentDoneColumn] = useState(board.agent_done_column || "");
  const [agentInstruction, setAgentInstruction] = useState(board.agent_instruction || "");
  const [provider, setProvider] = useState(board.provider);
  const [config, setConfig] = useState<Record<string, any>>({ ...board.provider_config });
  const [secret, setSecret] = useState("");
  const [direction, setDirection] = useState(board.sync_direction);
  const [autoSync, setAutoSync] = useState(board.auto_sync_minutes);
  const [labs, setLabs] = useState<Lab[]>([]);
  const [providers, setProviders] = useState<ProviderSpec[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<string | null>(null);
  const [discoveredStates, setDiscoveredStates] = useState<string[]>([]);
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    labsApi.list().then(setLabs);
    boardApi.providers().then(setProviders);
  }, []);

  const spec = providers.find((p) => p.key === provider);
  const stateMap: Record<string, string> = config.state_map || {};

  function setStateFor(columnKey: string, value: string) {
    const next = { ...stateMap };
    if (value) next[columnKey] = value;
    else delete next[columnKey];
    setConfig({ ...config, state_map: next });
  }

  async function save() {
    setError(null);
    try {
      await boardApi.update(board.id, {
        name,
        description: description.trim() || null,
        key_prefix: keyPrefix,
        lab_id: labId || null,
        columns,
        agent_column: agentColumn || null,
        agent_done_column: agentDoneColumn || null,
        agent_instruction: agentInstruction.trim() || null,
        provider,
        provider_config: config,
        sync_direction: direction,
        auto_sync_minutes: Number(autoSync) || 0,
        // Alleen meesturen als er echt iets is ingetypt: een leeg veld betekent
        // "ongewijzigd laten", niet "wissen".
        ...(secret.trim() ? { provider_secret: secret.trim() } : {}),
      });
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Opslaan mislukt");
    }
  }

  async function test() {
    setTesting(true);
    setTestResult(null);
    try {
      // Eerst opslaan: testen op niet-opgeslagen gegevens toont een uitkomst
      // die niets zegt over wat het board straks doet.
      await save();
      const result = await boardApi.testConnection(board.id);
      if (result.ok) {
        setDiscoveredStates(result.states || []);
        setTestResult(
          `Verbonden — ${result.found} item(s) gevonden.` +
            (result.states?.length ? ` Statussen in de bron: ${result.states.join(", ")}` : ""),
        );
      } else {
        setTestResult(result.error || "Verbinding mislukt");
      }
    } catch (err) {
      setTestResult(err instanceof ApiError ? err.message : "Verbinding mislukt");
    } finally {
      setTesting(false);
    }
  }

  return (
    <Modal open onClose={onClose} title={`Instellingen — ${board.name}`} wide>
      <div className="space-y-4">
        {/* algemeen */}
        <div className="grid grid-cols-3 gap-3">
          <div className="col-span-2">
            <Label>Naam</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div>
            <Label>Ticket-prefix</Label>
            <Input value={keyPrefix} onChange={(e) => setKeyPrefix(e.target.value.toUpperCase())} maxLength={16} />
          </div>
        </div>
        <div>
          <Label>Omschrijving</Label>
          <Input value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>
        <div>
          <Label>Gekoppeld lab</Label>
          <Select value={labId} onChange={(e) => setLabId(e.target.value)}>
            <option value="">Geen lab</option>
            {labs.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name} — {l.status}
              </option>
            ))}
          </Select>
        </div>

        {/* kolommen */}
        <Card className="p-3">
          <Label>Kolommen</Label>
          <div className="space-y-2">
            {columns.map((c, i) => (
              <div key={i} className="flex items-center gap-2">
                <Input
                  className="w-32 font-mono text-xs"
                  value={c.key}
                  onChange={(e) =>
                    setColumns(columns.map((x, idx) => (idx === i ? { ...x, key: e.target.value } : x)))
                  }
                />
                <Input
                  value={c.name}
                  onChange={(e) =>
                    setColumns(columns.map((x, idx) => (idx === i ? { ...x, name: e.target.value } : x)))
                  }
                />
                <label className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={!!c.is_done}
                    onChange={(e) =>
                      setColumns(columns.map((x, idx) => (idx === i ? { ...x, is_done: e.target.checked } : x)))
                    }
                  />
                  klaar
                </label>
                <Button
                  variant="ghost"
                  className="px-2"
                  onClick={() => setColumns(columns.filter((_, idx) => idx !== i))}
                  disabled={columns.length <= 1}
                >
                  ✕
                </Button>
              </div>
            ))}
          </div>
          <Button
            variant="secondary"
            className="mt-2 text-xs"
            onClick={() => setColumns([...columns, { key: `kolom_${columns.length + 1}`, name: "Nieuwe kolom" }])}
          >
            + Kolom
          </Button>
          <p className="mt-1 text-xs text-muted-foreground">
            Een kolom verwijderen verplaatst zijn tickets naar de eerste kolom — ze verdwijnen niet.
          </p>
        </Card>

        {/* agent */}
        <Card className="p-3">
          <Label>Agent</Label>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>Pakt werk op uit</Label>
              <Select value={agentColumn} onChange={(e) => setAgentColumn(e.target.value)}>
                <option value="">—</option>
                {columns.map((c) => (
                  <option key={c.key} value={c.key}>
                    {c.name}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <Label>Zet klaar werk in</Label>
              <Select value={agentDoneColumn} onChange={(e) => setAgentDoneColumn(e.target.value)}>
                <option value="">—</option>
                {columns.map((c) => (
                  <option key={c.key} value={c.key}>
                    {c.name}
                  </option>
                ))}
              </Select>
            </div>
          </div>
          <div className="mt-2">
            <Label>Vaste werkafspraken (elke agent-run op dit board krijgt deze mee)</Label>
            <TextArea
              rows={3}
              className="text-xs"
              value={agentInstruction}
              onChange={(e) => setAgentInstruction(e.target.value)}
              placeholder="Bijv.: werk in /workspace/repo, schrijf tests bij elke wijziging, push nooit naar main."
            />
          </div>
        </Card>

        {/* koppeling */}
        <Card className="p-3">
          <Label>Bron</Label>
          <Select value={provider} onChange={(e) => setProvider(e.target.value as BoardDto["provider"])}>
            {providers.map((p) => (
              <option key={p.key} value={p.key}>
                {p.name}
              </option>
            ))}
          </Select>

          {spec && spec.fields.length > 0 && (
            <div className="mt-3 space-y-2">
              {spec.fields.map((f) => (
                <div key={f.key}>
                  <Label>
                    {f.label}
                    {f.required ? " *" : ""}
                  </Label>
                  {f.multiline ? (
                    <TextArea
                      rows={2}
                      className="font-mono text-xs"
                      value={config[f.key] || ""}
                      placeholder={f.placeholder}
                      onChange={(e) => setConfig({ ...config, [f.key]: e.target.value })}
                    />
                  ) : (
                    <Input
                      value={config[f.key] || ""}
                      placeholder={f.placeholder}
                      onChange={(e) => setConfig({ ...config, [f.key]: e.target.value })}
                    />
                  )}
                </div>
              ))}

              {spec.secret_label && (
                <div>
                  <Label>
                    {spec.secret_label} {board.has_secret && <Badge tone="green">ingesteld</Badge>}
                  </Label>
                  <Input
                    type="password"
                    value={secret}
                    placeholder={board.has_secret ? "Laat leeg om het huidige token te behouden" : ""}
                    onChange={(e) => setSecret(e.target.value)}
                  />
                </div>
              )}

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <Label>Richting</Label>
                  <Select value={direction} onChange={(e) => setDirection(e.target.value as BoardDto["sync_direction"])}>
                    <option value="two_way">Twee richtingen (ook terugschrijven)</option>
                    <option value="pull">Alleen lezen (pull)</option>
                  </Select>
                </div>
                <div>
                  <Label>Automatisch synchroniseren (minuten, 0 = uit)</Label>
                  <Input
                    type="number"
                    min={0}
                    value={autoSync}
                    onChange={(e) => setAutoSync(Number(e.target.value))}
                  />
                </div>
              </div>

              {direction === "two_way" && spec.write_note && (
                <p className="flex items-start gap-1 rounded-md border border-border bg-secondary/40 p-2 text-xs text-muted-foreground">
                  <Info size={13} className="mt-0.5 shrink-0" />
                  {spec.write_note}
                </p>
              )}

              <div>
                <Label>Statusmapping (kolom → status in de bron)</Label>
                <p className="mb-1 text-xs text-muted-foreground">
                  {spec.state_hint} Leeg laten = die kolom wordt niet aan een externe status gekoppeld.
                </p>
                <div className="space-y-1">
                  {columns.map((c) => (
                    <div key={c.key} className="flex items-center gap-2">
                      <span className="w-32 shrink-0 text-xs text-muted-foreground">{c.name}</span>
                      <Input
                        list={`states-${board.id}`}
                        value={stateMap[c.key] || ""}
                        onChange={(e) => setStateFor(c.key, e.target.value)}
                      />
                    </div>
                  ))}
                  <datalist id={`states-${board.id}`}>
                    {discoveredStates.map((s) => (
                      <option key={s} value={s} />
                    ))}
                  </datalist>
                </div>
              </div>

              <div className="flex items-center gap-2">
                <Button variant="secondary" className="text-xs" onClick={test} disabled={testing}>
                  {testing ? "Testen…" : "Opslaan & verbinding testen"}
                </Button>
                {testResult && <span className="text-xs text-muted-foreground">{testResult}</span>}
              </div>
            </div>
          )}
        </Card>

        {error && <p className="text-sm text-destructive">{error}</p>}

        <div className="flex items-center justify-between">
          <Button
            variant="danger"
            className="text-xs"
            onClick={() => {
              if (confirm(`Board "${board.name}" en al zijn tickets verwijderen?`)) {
                boardApi.remove(board.id).then(onDeleted);
              }
            }}
          >
            Board verwijderen
          </Button>
          <Button onClick={save}>Opslaan</Button>
        </div>
      </div>
    </Modal>
  );
}
