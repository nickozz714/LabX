/**
 * pages/WorkflowsPage.tsx
 *
 * "Een Workflow is een Markdown bestand dat eventueel stappen beschrijft" —
 * deliberately NOT a DAG canvas. Two views of the same data: a visual
 * step list (drag-free reorder via up/down, per-step title+instruction) and
 * a raw Markdown tab; editing either re-derives the other on save.
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { workflowApi } from "@/lib/workflows";
import { labsApi } from "@/lib/labs";
import type { Lab, WorkflowDto, WorkflowStep } from "@/lib/types";
import { Badge, Button, Card, EmptyState, Input, Label, Modal, TextArea } from "@/components/ui";
import { useMelding } from "@/components/Meldingen";
import { useBevestiging } from "@/components/Bevestiging";
import { WorkflowRuns } from "@/components/WorkflowRuns";
import { ParameterInvuller } from "@/components/workflow/Parameters";
import { ApiError } from "@/lib/api";

export function WorkflowsPage() {
  const navigate = useNavigate();
  const bevestig = useBevestiging();
  const melding = useMelding();
  const [workflows, setWorkflows] = useState<WorkflowDto[]>([]);
  const [editing, setEditing] = useState<WorkflowDto | null>(null);
  const [creating, setCreating] = useState(false);
  // Welke workflow zijn runs laat zien. Eén tegelijk: het is een lange
  // lijst met uitklapbare activiteiten, en twee ervan naast elkaar leest niet.
  const [monitoringVoor, setMonitoringVoor] = useState<WorkflowDto | null>(null);

  function refresh() {
    workflowApi.list().then(setWorkflows);
  }
  useEffect(refresh, []);

  return (
    <div className="veilig-onder p-4 sm:p-6">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-bold">Workflows</h1>
        <Button onClick={() => setCreating(true)}>+ Nieuwe workflow</Button>
      </div>
      {workflows.length === 0 ? (
        <EmptyState>Nog geen workflows. Een workflow is een reeks stappen (markdown) die de agent tegen een lab uitvoert.</EmptyState>
      ) : (
        /* Een tabel en geen kaartjes. Vijf workflows in blokjes van twee
           kolommen leest als een puzzel: je ogen springen heen en weer om te
           vergelijken wat er aanstaat en hoeveel activiteiten erin zitten.
           Onder elkaar met vaste kolommen scan je dat in één beweging. */
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead className="border-b border-border bg-muted/40 text-left text-xs text-muted-foreground">
              <tr>
                <th className="px-3 py-2 font-medium">Naam</th>
                <th className="hidden px-3 py-2 font-medium sm:table-cell">Omschrijving</th>
                <th className="px-3 py-2 font-medium">Activiteiten</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {workflows.map((w) => (
                <tr key={w.id} className="border-b border-border last:border-0 hover:bg-muted/30">
                  <td className="px-3 py-2">
                    <button className="break-words text-left font-medium hover:underline"
                            onClick={() => navigate(`/workflows/${w.id}`)}>
                      {w.name}
                    </button>
                    {/* Op een smal scherm is er geen kolom voor; dan hoort hij
                        onder de naam in plaats van te verdwijnen. */}
                    <div className="break-words text-xs text-muted-foreground sm:hidden">
                      {w.description}
                    </div>
                  </td>
                  <td className="hidden max-w-xs px-3 py-2 text-xs text-muted-foreground sm:table-cell">
                    <span className="line-clamp-2 break-words">{w.description}</span>
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-xs text-muted-foreground">
                    {(w.nodes || []).length || w.steps.length}
                    {(w.waarschuwingen || []).length > 0 && (
                      <span className="ml-2 text-yellow-600"
                            title={w.waarschuwingen.join("\n")}>
                        ⚠ {w.waarschuwingen.length}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <button
                      onClick={() => workflowApi.updateMeta(w.id, { is_enabled: !w.is_enabled })
                        .then(refresh)
                        .then(() => melding.ok(`Workflow '${w.name}' `
                                               + (w.is_enabled ? "uitgezet" : "aangezet")))
                        .catch((err) => melding.fout("Aanpassen mislukt", String(err)))}
                      title="Aan/uit"
                    >
                      <Badge tone={w.is_enabled ? "green" : "neutral"}>
                        {w.is_enabled ? "aan" : "uit"}
                      </Badge>
                    </button>
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap items-center justify-end gap-1.5">
                      <Button variant="secondary" className="px-2 py-1 text-xs"
                              onClick={() => setMonitoringVoor(monitoringVoor?.id === w.id ? null : w)}>
                        {monitoringVoor?.id === w.id ? "Sluiten" : "Monitoring"}
                      </Button>
                      {/* Een workflow maak je zelden om hem één keer met de
                          hand te draaien; de volgende stap is bijna altijd
                          inplannen. Dat scheelt zoeken in een andere tab. */}
                      <Button variant="secondary" className="px-2 py-1 text-xs"
                              title="Een planning maken voor deze workflow"
                              onClick={() => navigate(
                                `/workbench/scheduling?workflow=${w.id}`)}>
                        Inplannen
                      </Button>
                      <Button variant="secondary" className="px-2 py-1 text-xs"
                              onClick={() => navigate(`/workflows/${w.id}`)}>
                        Bewerken
                      </Button>
                      <Button
                        variant="danger"
                        className="px-2 py-1 text-xs"
                        onClick={async () => {
                          const ja = await bevestig.vraag({
                            titel: `Workflow "${w.name}" verwijderen?`,
                            tekst: "De stappen erin gaan mee.",
                            bevestig: "Verwijderen",
                          });
                          if (ja) await workflowApi.remove(w.id).then(refresh);
                        }}
                      >
                        Verwijderen
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {monitoringVoor && (
        <div className="mt-4">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-sm font-semibold">Monitoring — '{monitoringVoor.name}'</h2>
            <Button variant="ghost" onClick={() => setMonitoringVoor(null)}>Sluiten</Button>
          </div>
          <p className="mb-2 text-xs text-muted-foreground">
            Handmatige én geplande runs staan hier door elkaar — het is hetzelfde ding. Klap een
            activiteit open voor de invoer die het model kreeg, wat het onderweg deed, en wat
            eruit kwam.
          </p>
          <WorkflowRuns workflowId={monitoringVoor.id} />
        </div>
      )}
      {creating && (
        <WorkflowEditor
          onClose={() => setCreating(false)}
          onSaved={(id) => {
            setCreating(false);
            refresh();
            // Meteen het doek in: een nieuwe workflow bestaat uit activiteiten
            // die je gaat tekenen, niet uit een naam.
            if (id) navigate(`/workflows/${id}`);
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

function WorkflowEditor({ existing, onClose, onSaved }: { existing?: WorkflowDto; onClose: () => void; onSaved: (id?: number) => void }) {
  const [name, setName] = useState(existing?.name || "");
  const [description, setDescription] = useState(existing?.description || "");
  const [tab, setTab] = useState<"steps" | "markdown">("steps");
  const [steps, setSteps] = useState<WorkflowStep[]>(existing?.steps || []);
  const [markdown, setMarkdown] = useState(existing?.markdown || "");
  const [labs, setLabs] = useState<Lab[]>([]);
  const [runLabId, setRunLabId] = useState("");
  const [runResult, setRunResult] = useState<string | null>(null);
  const [invoer, setInvoer] = useState<Record<string, string>>({});
  // Is er iets veranderd sinds het openen? Zo ja, dan gooit een klik naast het
  // venster dit niet meer weg — zie Modal.
  const gewijzigd =
    name !== (existing?.name || "") ||
    description !== (existing?.description || "") ||
    markdown !== (existing?.markdown || "") ||
    JSON.stringify(steps) !== JSON.stringify(existing?.steps || []);

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
    onSaved(wf?.id);
  }

  async function run() {
    if (!existing || !runLabId) return;
    setRunResult("Starten…");
    // Uitvoeren geeft alleen de run terug: het werk loopt op de achtergrond,
    // want zeven activiteiten duren minuten tot uren. De monitoring staat in het
    // verslag (knop 'Monitoring' bij de workflow).
    try {
      const r = await workflowApi.run(existing.id, runLabId, invoer);
      setRunResult(`Gestart (run ${r.id.slice(0, 8)}). Volg hem bij 'Monitoring' — daar `
                   + `staat per activiteit wat het model kreeg, deed en teruggaf.`);
    } catch (e) {
      setRunResult(e instanceof ApiError ? e.message : "Starten mislukt");
    }
  }

  return (
    <Modal open onClose={onClose} dirty={gewijzigd}
           title={existing ? "Workflow bewerken" : "Nieuwe workflow"} wide>
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
                {labs.map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.name}{l.status === "running" ? "" : ` (${l.status} — wordt gestart)`}
                  </option>
                ))}
              </select>
              <Button variant="secondary" disabled={!runLabId} onClick={run}>
                Uitvoeren
              </Button>
            </div>
            {existing.parameters?.length ? (
              <div className="mb-2">
                <ParameterInvuller parameters={existing.parameters}
                                   waarden={invoer} onChange={setInvoer} />
              </div>
            ) : null}
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
