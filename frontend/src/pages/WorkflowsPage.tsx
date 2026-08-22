/**
 * pages/WorkflowsPage.tsx
 *
 * "Een Workflow is een Markdown bestand dat eventueel stappen beschrijft" —
 * deliberately NOT a DAG canvas. Two views of the same data: a visual
 * step list (drag-free reorder via up/down, per-step title+instruction) and
 * a raw Markdown tab; editing either re-derives the other on save.
 */
import { useEffect, useState } from "react";
import { workflowApi } from "@/lib/workflows";
import { labsApi } from "@/lib/labs";
import type { Lab, WorkflowDto, WorkflowStep } from "@/lib/types";
import { Badge, Button, Card, EmptyState, Input, Label, Modal, TextArea } from "@/components/ui";

export function WorkflowsPage() {
  const [workflows, setWorkflows] = useState<WorkflowDto[]>([]);
  const [editing, setEditing] = useState<WorkflowDto | null>(null);
  const [creating, setCreating] = useState(false);

  function refresh() {
    workflowApi.list().then(setWorkflows);
  }
  useEffect(refresh, []);

  return (
    <div className="p-6">
      <div className="mb-4 flex justify-between">
        <h1 className="text-xl font-bold">Workflows</h1>
        <Button onClick={() => setCreating(true)}>+ Nieuwe workflow</Button>
      </div>
      {workflows.length === 0 ? (
        <EmptyState>Nog geen workflows. Een workflow is een reeks stappen (markdown) die de agent tegen een lab uitvoert.</EmptyState>
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {workflows.map((w) => (
            <Card key={w.id} className="p-4">
              <div className="flex items-center justify-between">
                <span className="cursor-pointer font-medium" onClick={() => setEditing(w)}>{w.name}</span>
                <button
                  onClick={() => workflowApi.updateMeta(w.id, { is_enabled: !w.is_enabled }).then(refresh)}
                  title="Aan/uit"
                >
                  <Badge tone={w.is_enabled ? "green" : "neutral"}>{w.is_enabled ? "aan" : "uit"}</Badge>
                </button>
              </div>
              <div className="cursor-pointer text-xs text-muted-foreground" onClick={() => setEditing(w)}>{w.description}</div>
              <div className="mt-1 flex items-center justify-between">
                <span className="text-xs text-muted-foreground">{w.steps.length} stap(pen)</span>
                <Button
                  variant="danger"
                  className="px-2 py-0.5 text-xs"
                  onClick={() => {
                    if (confirm(`Workflow "${w.name}" verwijderen?`)) workflowApi.remove(w.id).then(refresh);
                  }}
                >
                  Verwijderen
                </Button>
              </div>
            </Card>
          ))}
        </div>
      )}
      {creating && (
        <WorkflowEditor
          onClose={() => setCreating(false)}
          onSaved={() => {
            setCreating(false);
            refresh();
          }}
        />
      )}
      {editing && (
        <WorkflowEditor
          existing={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            refresh();
          }}
        />
      )}
    </div>
  );
}

function WorkflowEditor({ existing, onClose, onSaved }: { existing?: WorkflowDto; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState(existing?.name || "");
  const [description, setDescription] = useState(existing?.description || "");
  const [tab, setTab] = useState<"steps" | "markdown">("steps");
  const [steps, setSteps] = useState<WorkflowStep[]>(existing?.steps || []);
  const [markdown, setMarkdown] = useState(existing?.markdown || "");
  const [labs, setLabs] = useState<Lab[]>([]);
  const [runLabId, setRunLabId] = useState("");
  const [runResult, setRunResult] = useState<string | null>(null);

  useEffect(() => {
    labsApi.list().then(setLabs);
  }, []);

  function addStep() {
    setSteps((prev) => [...prev, { index: prev.length + 1, title: "Nieuwe stap", instruction: "" }]);
  }
  function moveStep(i: number, dir: -1 | 1) {
    setSteps((prev) => {
      const next = [...prev];
      const j = i + dir;
      if (j < 0 || j >= next.length) return prev;
      [next[i], next[j]] = [next[j], next[i]];
      return next.map((s, idx) => ({ ...s, index: idx + 1 }));
    });
  }
  function removeStep(i: number) {
    setSteps((prev) => prev.filter((_, idx) => idx !== i).map((s, idx) => ({ ...s, index: idx + 1 })));
  }

  async function save() {
    let wf = existing;
    if (!wf) {
      wf = await workflowApi.create({ name, description, steps: tab === "steps" ? steps : undefined, markdown: tab === "markdown" ? markdown : undefined });
    } else {
      await workflowApi.updateMeta(wf.id, { name, description });
      wf = tab === "steps" ? await workflowApi.updateSteps(wf.id, steps) : await workflowApi.updateMarkdown(wf.id, markdown);
    }
    onSaved();
  }

  async function run() {
    if (!existing || !runLabId) return;
    setRunResult("Bezig…");
    const r = await workflowApi.run(existing.id, runLabId);
    setRunResult(r.error ? `Fout: ${r.error}` : r.output);
  }

  return (
    <Modal open onClose={onClose} title={existing ? "Workflow bewerken" : "Nieuwe workflow"} wide>
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label>Naam</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div>
            <Label>Beschrijving</Label>
            <Input value={description} onChange={(e) => setDescription(e.target.value)} />
          </div>
        </div>

        <div className="flex gap-1 border-b border-border text-sm">
          {(["steps", "markdown"] as const).map((t) => (
            <button key={t} onClick={() => setTab(t)} className={`px-3 py-1.5 ${tab === t ? "border-b-2 border-primary font-medium" : "text-muted-foreground"}`}>
              {t === "steps" ? "Visuele editor" : "Markdown"}
            </button>
          ))}
        </div>

        {tab === "steps" ? (
          <div className="space-y-2">
            {steps.map((s, i) => (
              <Card key={i} className="p-3">
                <div className="mb-1 flex items-center gap-2">
                  <span className="text-xs text-muted-foreground">#{s.index}</span>
                  <Input
                    value={s.title}
                    onChange={(e) => setSteps((prev) => prev.map((x, idx) => (idx === i ? { ...x, title: e.target.value } : x)))}
                    className="flex-1"
                  />
                  <Button variant="ghost" onClick={() => moveStep(i, -1)}>↑</Button>
                  <Button variant="ghost" onClick={() => moveStep(i, 1)}>↓</Button>
                  <Button variant="danger" onClick={() => removeStep(i)}>✕</Button>
                </div>
                <TextArea
                  rows={2}
                  value={s.instruction}
                  onChange={(e) => setSteps((prev) => prev.map((x, idx) => (idx === i ? { ...x, instruction: e.target.value } : x)))}
                />
              </Card>
            ))}
            <Button variant="secondary" onClick={addStep}>
              + Stap
            </Button>
          </div>
        ) : (
          <TextArea rows={14} value={markdown} onChange={(e) => setMarkdown(e.target.value)} className="font-mono text-xs" />
        )}

        {existing && (
          <Card className="p-3">
            <div className="mb-2 flex items-center gap-2 text-sm">
              <Label>Handmatig uitvoeren tegen lab</Label>
              <select value={runLabId} onChange={(e) => setRunLabId(e.target.value)} className="rounded border border-input bg-background px-2 py-1 text-sm">
                <option value="">Kies een lab…</option>
                {labs.filter((l) => l.status === "running").map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.name}
                  </option>
                ))}
              </select>
              <Button variant="secondary" disabled={!runLabId} onClick={run}>
                Uitvoeren
              </Button>
            </div>
            {runResult && <pre className="max-h-48 overflow-auto rounded bg-secondary p-2 text-xs whitespace-pre-wrap">{runResult}</pre>}
          </Card>
        )}

        <Button className="w-full" onClick={save} disabled={!name.trim()}>
          Opslaan
        </Button>
      </div>
    </Modal>
  );
}
