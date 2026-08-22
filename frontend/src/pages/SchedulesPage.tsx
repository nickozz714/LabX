/**
 * pages/SchedulesPage.tsx — cron-driven runs against a lab (prompt or
 * workflow). "Scheduling mogelijkheden" from the request.
 */
import { useEffect, useState } from "react";
import { scheduleApi, workflowApi } from "@/lib/workflows";
import { labsApi } from "@/lib/labs";
import type { Lab, ScheduleDto, ScheduleRunDto, WorkflowDto } from "@/lib/types";
import { Badge, Button, Card, EmptyState, Input, Label, Modal, TextArea, Toggle } from "@/components/ui";
import { ApiError } from "@/lib/api";

export function SchedulesPage() {
  const [schedules, setSchedules] = useState<ScheduleDto[]>([]);
  const [creating, setCreating] = useState(false);
  const [runsFor, setRunsFor] = useState<ScheduleDto | null>(null);

  function refresh() {
    scheduleApi.list().then(setSchedules);
  }
  useEffect(refresh, []);

  return (
    <div className="p-6">
      <div className="mb-4 flex justify-between">
        <h1 className="text-xl font-bold">Scheduling</h1>
        <Button onClick={() => setCreating(true)}>+ Nieuwe schedule</Button>
      </div>
      {schedules.length === 0 ? (
        <EmptyState>Nog geen schedules. Een schedule draait een prompt of workflow op een cron-tijdstip tegen een lab.</EmptyState>
      ) : (
        <div className="space-y-2">
          {schedules.map((s) => (
            <Card key={s.id} className="flex items-center justify-between p-3">
              <div>
                <div className="font-medium text-sm">
                  {s.name} <Badge tone={s.is_enabled ? "green" : "neutral"}>{s.is_enabled ? "aan" : "uit"}</Badge>
                </div>
                <div className="text-xs text-muted-foreground font-mono">{s.cron_expression}</div>
                <div className="text-xs text-muted-foreground">laatst gedraaid: {s.last_run_at ? new Date(s.last_run_at).toLocaleString() : "nooit"}</div>
              </div>
              <div className="flex gap-2">
                <Button variant="secondary" onClick={() => setRunsFor(s)}>Runs</Button>
                <Button variant="secondary" onClick={() => scheduleApi.update(s.id, { is_enabled: !s.is_enabled }).then(refresh)}>
                  {s.is_enabled ? "Pauzeer" : "Hervat"}
                </Button>
                <Button variant="danger" onClick={() => scheduleApi.remove(s.id).then(refresh)}>Verwijderen</Button>
              </div>
            </Card>
          ))}
        </div>
      )}
      {creating && <CreateScheduleModal onClose={() => setCreating(false)} onCreated={() => { setCreating(false); refresh(); }} />}
      {runsFor && <RunsModal schedule={runsFor} onClose={() => setRunsFor(null)} />}
    </div>
  );
}

function CreateScheduleModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState("");
  const [cron, setCron] = useState("0 6 * * *");
  const [labId, setLabId] = useState("");
  const [mode, setMode] = useState<"prompt" | "workflow">("prompt");
  const [prompt, setPrompt] = useState("");
  const [workflowId, setWorkflowId] = useState<number | "">("");
  const [jsonSchema, setJsonSchema] = useState("");
  const [labs, setLabs] = useState<Lab[]>([]);
  const [workflows, setWorkflows] = useState<WorkflowDto[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    labsApi.list().then(setLabs);
    workflowApi.list().then(setWorkflows);
  }, []);

  async function submit() {
    try {
      await scheduleApi.create({
        name, cron_expression: cron, lab_id: labId,
        prompt: mode === "prompt" ? prompt : undefined,
        workflow_id: mode === "workflow" ? workflowId : undefined,
        json_schema: jsonSchema.trim() || undefined,
      });
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Aanmaken mislukt");
    }
  }

  return (
    <Modal open onClose={onClose} title="Nieuwe schedule">
      <div className="space-y-3">
        <div>
          <Label>Naam</Label>
          <Input value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <Label>Cron-expressie</Label>
          <Input value={cron} onChange={(e) => setCron(e.target.value)} className="font-mono" />
        </div>
        <div>
          <Label>Lab</Label>
          <select value={labId} onChange={(e) => setLabId(e.target.value)} className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm">
            <option value="">Kies een lab…</option>
            {labs.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name}
              </option>
            ))}
          </select>
        </div>
        <div className="flex gap-4 text-sm">
          <label className="flex items-center gap-1">
            <input type="radio" checked={mode === "prompt"} onChange={() => setMode("prompt")} /> Prompt
          </label>
          <label className="flex items-center gap-1">
            <input type="radio" checked={mode === "workflow"} onChange={() => setMode("workflow")} /> Workflow
          </label>
        </div>
        {mode === "prompt" ? (
          <Input value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="Wat moet de agent doen?" />
        ) : (
          <select value={workflowId} onChange={(e) => setWorkflowId(Number(e.target.value))} className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm">
            <option value="">Kies een workflow…</option>
            {workflows.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
              </option>
            ))}
          </select>
        )}
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
        {error && <p className="text-sm text-destructive">{error}</p>}
        <Button className="w-full" onClick={submit} disabled={!name || !cron || !labId}>
          Aanmaken
        </Button>
      </div>
    </Modal>
  );
}

function RunsModal({ schedule, onClose }: { schedule: ScheduleDto; onClose: () => void }) {
  const [runs, setRuns] = useState<ScheduleRunDto[]>([]);
  useEffect(() => {
    scheduleApi.runs(schedule.id).then(setRuns);
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
                <span>{new Date(r.scheduled_for).toLocaleString()}</span>
                <Badge tone={r.status === "completed" ? "green" : r.status === "failed" ? "red" : "yellow"}>{r.status}</Badge>
              </div>
              {(r.output || r.error) && (
                <pre className="mt-1 max-h-32 overflow-auto rounded bg-secondary p-2 text-xs whitespace-pre-wrap">{r.error || r.output}</pre>
              )}
            </Card>
          ))}
        </div>
      )}
    </Modal>
  );
}
