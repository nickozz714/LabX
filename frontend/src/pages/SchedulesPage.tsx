/**
 * pages/SchedulesPage.tsx — cron-gestuurde runs tegen een lab.
 *
 * Een schedule voert één van drie dingen uit:
 * - een prompt,
 * - een workflow,
 * - board-werk: de bovenste N tickets uit de agent-kolom van een board, die de
 *   agent zelfstandig oppakt en aanvult.
 *
 * Het overzicht toont per schedule wat hij uitvoert — een cron-expressie zonder
 * doel ernaast dwingt je anders elke keer de schedule te openen om te zien wat
 * er straks gebeurt.
 */
import { useEffect, useState } from "react";
import { scheduleApi, workflowApi } from "@/lib/workflows";
import { boardApi } from "@/lib/boards";
import { labsApi } from "@/lib/labs";
import type { BoardDto, Lab, ScheduleDto, ScheduleKind, ScheduleRunDto, WorkflowDto } from "@/lib/types";
import { Badge, Button, Card, EmptyState, Input, Label, Modal, Select, TextArea } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { Bot, CalendarClock, PlayCircle, Workflow as WorkflowIcon } from "lucide-react";

const KIND_LABEL: Record<ScheduleKind, string> = {
  prompt: "Prompt",
  workflow: "Workflow",
  board: "Board-werk",
};

export function SchedulesPage() {
  const [schedules, setSchedules] = useState<ScheduleDto[]>([]);
  const [labs, setLabs] = useState<Lab[]>([]);
  const [workflows, setWorkflows] = useState<WorkflowDto[]>([]);
  const [boards, setBoards] = useState<BoardDto[]>([]);
  const [editing, setEditing] = useState<ScheduleDto | null>(null);
  const [creating, setCreating] = useState(false);
  const [runsFor, setRunsFor] = useState<ScheduleDto | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  function refresh() {
    scheduleApi.list().then(setSchedules);
  }
  useEffect(() => {
    refresh();
    labsApi.list().then(setLabs);
    workflowApi.list().then(setWorkflows);
    boardApi.list().then(setBoards);
  }, []);

  function describe(s: ScheduleDto): string {
    if (s.kind === "workflow") {
      return workflows.find((w) => w.id === s.workflow_id)?.name || `workflow ${s.workflow_id}`;
    }
    if (s.kind === "board") {
      const board = boards.find((b) => b.id === s.board_id);
      const column = s.board_column || board?.agent_column || "agent-kolom";
      return `${board?.name || `board ${s.board_id}`} — max ${s.board_max_tickets} ticket(s) uit '${column}'`;
    }
    return (s.prompt || "").slice(0, 120) || "(lege prompt)";
  }

  async function runNow(s: ScheduleDto) {
    setNotice(null);
    try {
      await scheduleApi.runNow(s.id);
      setNotice(`'${s.name}' is gestart — de uitkomst verschijnt bij Runs.`);
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Starten mislukt");
    }
  }

  return (
    <div className="p-6">
      <div className="mb-4 flex justify-between">
        <h1 className="text-xl font-bold">Scheduling</h1>
        <Button onClick={() => setCreating(true)}>+ Nieuwe schedule</Button>
      </div>

      {notice && (
        <Card className="mb-3 p-3 text-sm">
          {notice}
          <button className="ml-2 text-xs text-muted-foreground" onClick={() => setNotice(null)}>
            sluiten
          </button>
        </Card>
      )}

      {schedules.length === 0 ? (
        <EmptyState>
          Nog geen schedules. Een schedule draait op een cron-tijdstip een prompt, een workflow, of
          laat de agent tickets van een board oppakken.
        </EmptyState>
      ) : (
        <div className="space-y-2">
          {schedules.map((s) => (
            <Card key={s.id} className="flex flex-wrap items-center justify-between gap-3 p-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2 text-sm font-medium">
                  {s.kind === "board" ? <Bot size={14} /> : s.kind === "workflow" ? <WorkflowIcon size={14} /> : <CalendarClock size={14} />}
                  {s.name}
                  <Badge tone={s.is_enabled ? "green" : "neutral"}>{s.is_enabled ? "aan" : "uit"}</Badge>
                  <Badge tone="violet">{KIND_LABEL[s.kind]}</Badge>
                </div>
                <div className="truncate text-xs text-muted-foreground">{describe(s)}</div>
                <div className="text-xs text-muted-foreground">
                  <span className="font-mono">{s.cron_expression}</span>
                  {" · lab: "}
                  {labs.find((l) => l.id === s.lab_id)?.name || s.lab_id.slice(0, 8)}
                  {" · laatst: "}
                  {s.last_run_at ? new Date(s.last_run_at).toLocaleString() : "nooit"}
                </div>
              </div>
              <div className="flex shrink-0 gap-2">
                <Button variant="secondary" className="text-xs" onClick={() => runNow(s)}>
                  <PlayCircle size={13} /> Nu uitvoeren
                </Button>
                <Button variant="secondary" className="text-xs" onClick={() => setRunsFor(s)}>
                  Runs
                </Button>
                <Button variant="secondary" className="text-xs" onClick={() => setEditing(s)}>
                  Bewerken
                </Button>
                <Button
                  variant="secondary"
                  className="text-xs"
                  onClick={() => scheduleApi.update(s.id, { is_enabled: !s.is_enabled }).then(refresh)}
                >
                  {s.is_enabled ? "Pauzeer" : "Hervat"}
                </Button>
                <Button
                  variant="danger"
                  className="text-xs"
                  onClick={() => {
                    if (confirm(`Schedule "${s.name}" verwijderen?`)) scheduleApi.remove(s.id).then(refresh);
                  }}
                >
                  Verwijderen
                </Button>
              </div>
            </Card>
          ))}
        </div>
      )}

      {(creating || editing) && (
        <ScheduleModal
          existing={editing || undefined}
          labs={labs}
          workflows={workflows}
          boards={boards}
          onClose={() => {
            setCreating(false);
            setEditing(null);
          }}
          onSaved={() => {
            setCreating(false);
            setEditing(null);
            refresh();
          }}
        />
      )}
      {runsFor && <RunsModal schedule={runsFor} onClose={() => setRunsFor(null)} />}
    </div>
  );
}

function ScheduleModal({
  existing, labs, workflows, boards, onClose, onSaved,
}: {
  existing?: ScheduleDto;
  labs: Lab[];
  workflows: WorkflowDto[];
  boards: BoardDto[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(existing?.name || "");
  const [cron, setCron] = useState(existing?.cron_expression || "0 6 * * *");
  const [labId, setLabId] = useState(existing?.lab_id || "");
  const [kind, setKind] = useState<ScheduleKind>(existing?.kind || "prompt");
  const [prompt, setPrompt] = useState(existing?.prompt || "");
  const [workflowId, setWorkflowId] = useState<number | "">(existing?.workflow_id ?? "");
  const [boardId, setBoardId] = useState<number | "">(existing?.board_id ?? "");
  const [boardColumn, setBoardColumn] = useState(existing?.board_column || "");
  const [maxTickets, setMaxTickets] = useState(existing?.board_max_tickets ?? 1);
  const [jsonSchema, setJsonSchema] = useState(existing?.json_schema || "");
  const [error, setError] = useState<string | null>(null);

  const board = boards.find((b) => b.id === boardId);

  // Het lab volgt het board: board-werk draait per definitie in het lab van
  // dat board, dus die twee uit elkaar laten lopen levert alleen verwarring op.
  useEffect(() => {
    if (kind === "board" && board?.lab_id) setLabId(board.lab_id);
  }, [kind, board?.lab_id]); // eslint-disable-line react-hooks/exhaustive-deps

  async function submit() {
    setError(null);
    const payload: Record<string, any> = {
      name,
      cron_expression: cron,
      lab_id: labId,
      kind,
      prompt: kind === "prompt" ? prompt : null,
      workflow_id: kind === "workflow" ? workflowId || null : null,
      board_id: kind === "board" ? boardId || null : null,
      board_column: kind === "board" ? boardColumn || null : null,
      board_max_tickets: kind === "board" ? Number(maxTickets) || 1 : 1,
      json_schema: kind === "prompt" ? jsonSchema.trim() || null : null,
    };
    try {
      if (existing) await scheduleApi.update(existing.id, payload);
      else await scheduleApi.create(payload);
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Opslaan mislukt");
    }
  }

  return (
    <Modal open onClose={onClose} title={existing ? "Schedule bewerken" : "Nieuwe schedule"}>
      <div className="space-y-3">
        <div>
          <Label>Naam</Label>
          <Input value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <Label>Cron-expressie</Label>
          <Input value={cron} onChange={(e) => setCron(e.target.value)} className="font-mono" />
          <p className="mt-1 text-xs text-muted-foreground">
            Bijv. <span className="font-mono">0 6 * * *</span> = elke dag om 06:00 (UTC).
          </p>
        </div>

        <div>
          <Label>Wat moet er gebeuren?</Label>
          <div className="flex flex-wrap gap-4 text-sm">
            {(["prompt", "workflow", "board"] as ScheduleKind[]).map((k) => (
              <label key={k} className="flex items-center gap-1">
                <input type="radio" checked={kind === k} onChange={() => setKind(k)} /> {KIND_LABEL[k]}
              </label>
            ))}
          </div>
        </div>

        {kind === "prompt" && (
          <div>
            <Label>Prompt</Label>
            <TextArea
              rows={3}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="Wat moet de agent doen?"
            />
          </div>
        )}

        {kind === "workflow" && (
          <div>
            <Label>Workflow</Label>
            <Select value={workflowId} onChange={(e) => setWorkflowId(Number(e.target.value))}>
              <option value="">Kies een workflow…</option>
              {workflows.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </Select>
          </div>
        )}

        {kind === "board" && (
          <div className="space-y-3">
            <div>
              <Label>Board</Label>
              <Select value={boardId} onChange={(e) => setBoardId(Number(e.target.value))}>
                <option value="">Kies een board…</option>
                {boards.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                    {b.lab_name ? ` — lab: ${b.lab_name}` : " — geen lab"}
                  </option>
                ))}
              </Select>
              {board && !board.lab_id && (
                <p className="mt-1 text-xs text-destructive">
                  Dit board heeft geen lab; de agent kan er niets oppakken.
                </p>
              )}
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>Uit kolom</Label>
                <Select value={boardColumn} onChange={(e) => setBoardColumn(e.target.value)}>
                  <option value="">
                    Agent-kolom van het board{board?.agent_column ? ` (${board.agent_column})` : ""}
                  </option>
                  {(board?.columns || []).map((c) => (
                    <option key={c.key} value={c.key}>
                      {c.name}
                    </option>
                  ))}
                </Select>
              </div>
              <div>
                <Label>Max. tickets per keer</Label>
                <Input
                  type="number"
                  min={1}
                  value={maxTickets}
                  onChange={(e) => setMaxTickets(Number(e.target.value))}
                />
              </div>
            </div>
          </div>
        )}

        <div>
          <Label>Lab</Label>
          <Select value={labId} onChange={(e) => setLabId(e.target.value)} disabled={kind === "board" && !!board?.lab_id}>
            <option value="">Kies een lab…</option>
            {labs.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name} — {l.status}
              </option>
            ))}
          </Select>
          {kind === "board" && board?.lab_id && (
            <p className="mt-1 text-xs text-muted-foreground">Volgt automatisch het lab van het board.</p>
          )}
        </div>

        {kind === "prompt" && (
          <details className="rounded-md border border-border p-3 text-sm">
            <summary className="cursor-pointer font-medium text-muted-foreground">Geavanceerd</summary>
            <div className="mt-2">
              <Label>Structured output schema (JSON, optioneel — "--json-schema")</Label>
              <TextArea
                rows={3}
                value={jsonSchema}
                onChange={(e) => setJsonSchema(e.target.value)}
                placeholder='{"type":"object","properties":{"status":{"type":"string"}},"required":["status"]}'
              />
              <p className="mt-1 text-xs text-muted-foreground">
                Zet de run-uitkomst om in machine-controleerbare JSON i.p.v. vrije tekst.
              </p>
            </div>
          </details>
        )}

        {error && <p className="text-sm text-destructive">{error}</p>}
        <Button className="w-full" onClick={submit} disabled={!name || !cron || !labId}>
          {existing ? "Opslaan" : "Aanmaken"}
        </Button>
      </div>
    </Modal>
  );
}

function RunsModal({ schedule, onClose }: { schedule: ScheduleDto; onClose: () => void }) {
  const [runs, setRuns] = useState<ScheduleRunDto[]>([]);
  useEffect(() => {
    const load = () => scheduleApi.runs(schedule.id).then(setRuns);
    load();
    // Een net gestarte run staat op "running" — even blijven kijken.
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [schedule.id]);
  return (
    <Modal open onClose={onClose} title={`Runs — ${schedule.name}`} wide>
      {runs.length === 0 ? (
        <EmptyState>Nog geen runs.</EmptyState>
      ) : (
        <div className="max-h-96 space-y-2 overflow-y-auto">
          {runs.map((r) => (
            <Card key={r.id} className="p-3">
              <div className="flex items-center justify-between text-sm">
                <span>
                  {r.scheduled_for.startsWith("manual:")
                    ? `handmatig — ${new Date(r.scheduled_for.slice(7)).toLocaleString()}`
                    : new Date(r.scheduled_for).toLocaleString()}
                </span>
                <Badge tone={r.status === "completed" ? "green" : r.status === "failed" ? "red" : "yellow"}>
                  {r.status}
                </Badge>
              </div>
              {(r.output || r.error) && (
                <pre className="mt-1 max-h-32 overflow-auto rounded bg-secondary p-2 text-xs whitespace-pre-wrap">
                  {r.error || r.output}
                </pre>
              )}
            </Card>
          ))}
        </div>
      )}
    </Modal>
  );
}
