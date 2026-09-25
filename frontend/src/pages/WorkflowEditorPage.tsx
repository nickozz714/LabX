/**
 * pages/WorkflowEditorPage.tsx — de workflow-editor als volwaardig scherm.
 *
 * Niet meer in een pop-up. Dat was niet alleen krap voor een doek met
 * activiteiten erop; het was ook de plek waar één klik ernaast je halve
 * workflow opat. Een eigen pagina met een eigen adres kun je bovendien delen,
 * herladen en in een tabblad openhouden.
 *
 * Drie delen: een balk met wat er over de hele workflow gaat, het doek, en het
 * paneel rechts voor de geselecteerde activiteit. Onderin, op verzoek, het
 * monitoring van eerdere runs — bij het bouwen kijk je afwisselend naar de
 * tekening en naar wat er de vorige keer gebeurde.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { workflowApi } from "@/lib/workflows";
import { labsApi } from "@/lib/labs";
import type { Lab, Verwijzing, WorkflowDto, WorkflowEdge, WorkflowNode } from "@/lib/types";
import { Badge, Button, Input, Label, Select } from "@/components/ui";
import { WorkflowCanvas } from "@/components/workflow/WorkflowCanvas";
import { Eigenschappen } from "@/components/workflow/Eigenschappen";
import { WorkflowRuns } from "@/components/WorkflowRuns";
import { useMelding } from "@/components/Meldingen";
import { ApiError } from "@/lib/api";

const NIEUW: Record<string, Partial<WorkflowNode>> = {
  agent: { type: "agent", naam: "Nieuwe stap", prompt: "" },
  shell: { type: "shell", naam: "Commando", commando: "" },
  als: { type: "als", naam: "Als", conditie: { links: "", operator: "==", rechts: "" } },
  wacht: { type: "wacht", naam: "Wachten", seconden: 30 },
  parallel: { type: "parallel", naam: "Tegelijk", max_gelijktijdig: 4 },
  voorelk: { type: "voorelk", naam: "Voor elk", max_items: 50 },
};

export function WorkflowEditorPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const melding = useMelding();
  const [wf, setWf] = useState<WorkflowDto | null>(null);
  const [naam, setNaam] = useState("");
  const [omschrijving, setOmschrijving] = useState("");
  const [nodes, setNodes] = useState<WorkflowNode[]>([]);
  const [edges, setEdges] = useState<WorkflowEdge[]>([]);
  const [selectie, setSelectie] = useState<string | null>(null);
  const [vuil, setVuil] = useState(false);
  const [labs, setLabs] = useState<Lab[]>([]);
  const [labId, setLabId] = useState("");
  const [monitoring, setMonitoring] = useState(false);
  // De status van de laatste run per activiteit, zodat het doek meekleurt: je
  // ziet de workflow lopen in plaats van hem te moeten volgen in een lijst.
  const [statusPerNode, setStatusPerNode] = useState<Record<string, string>>({});
  // Waar de geselecteerde activiteit naar kan verwijzen, afgeleid uit de
  // schema's van de andere. Dit vult de keuzelijsten in het paneel.
  const [verwijzingen, setVerwijzingen] = useState<Verwijzing[]>([]);
  const [lijsten, setLijsten] = useState<Verwijzing[]>([]);
  const [loopt, setLoopt] = useState(false);

  useEffect(() => {
    if (!id) return;
    workflowApi.get(Number(id)).then((w) => {
      setWf(w);
      setNaam(w.name);
      setOmschrijving(w.description || "");
      setNodes(w.nodes || []);
      setEdges(w.edges || []);
    }).catch(() => navigate("/workflows"));
    labsApi.list().then(setLabs).catch(() => {});
  }, [id, navigate]);

  // Afsluiten met werk dat nog niet opgeslagen is: de browser waarschuwt. Dat
  // is precies de klasse ongelukken waar dit scherm voor bestaat.
  useEffect(() => {
    if (!vuil) return;
    const waarschuw = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener("beforeunload", waarschuw);
    return () => window.removeEventListener("beforeunload", waarschuw);
  }, [vuil]);

  const wijzig = useCallback((n: WorkflowNode[], e: WorkflowEdge[]) => {
    setNodes(n);
    setEdges(e);
    setVuil(true);
  }, []);

  function voegToe(soort: string) {
    const bestaand = nodes.map((n) => Number((n.id.match(/\d+$/) || ["0"])[0]));
    const nr = Math.max(0, ...bestaand) + 1;
    const nieuw = {
      ...NIEUW[soort],
      id: `n${nr}`,
      sleutel: `stap_${nr}`,
      positie: { x: 80 + (nodes.length % 3) * 40, y: 60 + nodes.length * 110 },
    } as WorkflowNode;
    setNodes([...nodes, nieuw]);
    setSelectie(nieuw.id);
    setVuil(true);
  }

  function wijzigNode(bij: WorkflowNode) {
    setNodes((huidig) => huidig.map((n) => (n.id === bij.id ? bij : n)));
    setVuil(true);
  }

  function verwijderNode(nodeId: string) {
    setNodes((huidig) => huidig.filter((n) => n.id !== nodeId && n.groep !== nodeId));
    setEdges((huidig) => huidig.filter((e) => e.van !== nodeId && e.naar !== nodeId));
    setSelectie(null);
    setVuil(true);
  }

  async function opslaan() {
    if (!wf) return;
    try {
      const bij = await workflowApi.opslaan(wf.id, {
        name: naam, description: omschrijving, nodes, edges });
      setWf(bij);
      // De graaf die terugkomt is dezelfde, alleen genormaliseerd. Hem
      // terugzetten in de state laat het doek ALLE activiteiten opnieuw
      // opbouwen — bij elke keer opslaan, en dat is precies wat opslaan traag
      // laat aanvoelen. Alleen overnemen als er echt iets anders is.
      if (JSON.stringify({ n: bij.nodes, e: bij.edges })
          !== JSON.stringify({ n: nodes, e: edges })) {
        setNodes(bij.nodes);
        setEdges(bij.edges);
      }
      setVuil(false);
      melding.ok("Workflow opgeslagen");
    } catch (e) {
      melding.fout("Opslaan mislukt", e instanceof ApiError ? e.message : String(e));
    }
  }

  async function uitvoeren() {
    if (!wf || !labId) return;
    try {
      const run = await workflowApi.run(wf.id, labId);
      setMonitoring(true);
      melding.ok(`Gestart (run ${run.id.slice(0, 8)}) — de monitoring staat hieronder.`);
    } catch (e) {
      melding.fout("Starten mislukt", e instanceof ApiError ? e.message : String(e));
    }
  }

  // Let op de afhankelijkheid: het ID, niet het hele workflow-object. Op `wf`
  // hangen betekende dat elke keer opslaan deze lus opnieuw opzette — inclusief
  // twee verzoeken, midden in het opslaan.
  const wfId = wf?.id;
  useEffect(() => {
    if (!wfId) return;
    let weg = false;
    const kijk = async () => {
      try {
        const runs = await workflowApi.runs(wfId, 1);
        if (weg || runs.length === 0) return;
        const bezig = ["running", "pending"].includes(runs[0].status);
        setLoopt(bezig);
        const detail = await workflowApi.run_detail(runs[0].id);
        const perNode: Record<string, string> = {};
        for (const s of detail.stappen || []) perNode[s.node_id] = s.status;
        if (!weg) setStatusPerNode(perNode);
      } catch { /* een run die net weg is, is geen reden om iets te melden */ }
    };
    kijk();
    const t = setInterval(kijk, loopt ? 3000 : 15000);
    return () => { weg = true; clearInterval(t); };
  }, [wfId, loopt]);

  useEffect(() => {
    if (!wf) return;
    workflowApi.verwijzingen(wf.id, selectie || undefined)
      .then((r) => { setVerwijzingen(r.verwijzingen); setLijsten(r.lijsten); })
      .catch(() => { setVerwijzingen([]); setLijsten([]); });
    // Ook na een wijziging aan de graaf: een nieuw schema levert nieuwe velden.
  }, [wf, selectie, nodes]);

  const geselecteerd = useMemo(
    () => nodes.find((n) => n.id === selectie) || null, [nodes, selectie]);

  if (!wf) return <div className="p-6 text-sm text-muted-foreground">Laden…</div>;

  return (
    <div className="flex h-[calc(100vh-3.5rem)] flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-border p-3">
        <Button variant="ghost" onClick={() => navigate("/workflows")}>← Workflows</Button>
        <Input value={naam} onChange={(e) => { setNaam(e.target.value); setVuil(true); }}
               className="w-64 font-semibold" />
        <Input value={omschrijving} placeholder="Korte omschrijving"
               onChange={(e) => { setOmschrijving(e.target.value); setVuil(true); }}
               className="w-72" />
        <div className="flex-1" />
        {wf.waarschuwingen?.length > 0 && (
          <span className="text-xs text-yellow-600" title={wf.waarschuwingen.join("\n")}>
            ⚠ {wf.waarschuwingen.length} aandachtspunt(en)
          </span>
        )}
        {loopt && <Badge tone="yellow">draait</Badge>}
        {vuil && <Badge tone="yellow">niet opgeslagen</Badge>}
        {/* Ook labs die uit staan: een lab gaat vanzelf slapen na een tijd
            stilte, en dat mag geen reden zijn dat je je workflow niet kunt
            draaien. De motor zet hem als eerste stap aan. */}
        <Select value={labId} onChange={(e) => setLabId(e.target.value)} className="w-52">
          <option value="">Kies een lab…</option>
          {labs.map((l) => (
            <option key={l.id} value={l.id}>
              {l.name}{l.status === "running" ? "" : ` (${l.status} — wordt gestart)`}
            </option>
          ))}
        </Select>
        <Button variant="secondary" disabled={!labId} onClick={uitvoeren}>Uitvoeren</Button>
        <Button variant="secondary" onClick={() => setMonitoring(!monitoring)}>
          {monitoring ? "Monitoring sluiten" : "Monitoring"}
        </Button>
        <Button onClick={opslaan} disabled={!vuil}>Opslaan</Button>
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="flex w-40 flex-col gap-1 border-r border-border p-2">
          <div className="mb-1 text-[11px] font-semibold text-muted-foreground">Toevoegen</div>
          <Button variant="secondary" className="justify-start text-xs"
                  onClick={() => voegToe("agent")}>🤖 Agent</Button>
          <Button variant="secondary" className="justify-start text-xs"
                  onClick={() => voegToe("shell")}>&gt;_ Shell</Button>
          <Button variant="secondary" className="justify-start text-xs"
                  onClick={() => voegToe("als")}>? Als</Button>
          <Button variant="secondary" className="justify-start text-xs"
                  onClick={() => voegToe("wacht")}>⏱ Wachten</Button>
          <Button variant="secondary" className="justify-start text-xs"
                  onClick={() => voegToe("voorelk")}>↻ Lus</Button>
          <Button variant="secondary" className="justify-start text-xs"
                  onClick={() => voegToe("parallel")}>⇉ Bubbel</Button>
          <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
            Sleep activiteiten in een <strong>lus</strong> om ze per element van een lijst te
            laten draaien — een <code>als</code> erin beslist dan per element. In een
            <strong> bubbel</strong> draaien ze tegelijk.
          </p>
        </div>

        <div className="min-w-0 flex-1">
          <WorkflowCanvas nodes={nodes} edges={edges} status={statusPerNode}
                          geselecteerd={selectie} onSelect={setSelectie} onChange={wijzig} />
        </div>

        <div className="w-96 overflow-y-auto border-l border-border">
          <Eigenschappen node={geselecteerd} onChange={wijzigNode} onDelete={verwijderNode}
                         verwijzingen={verwijzingen} lijsten={lijsten}
                         groepen={nodes.filter((n) => n.type === "voorelk"
                                                      || n.type === "parallel")} />
        </div>
      </div>

      {monitoring && (
        <div className="max-h-[40vh] overflow-y-auto border-t border-border p-3">
          <div className="mb-2 flex items-center justify-between">
            <Label>Monitoring — handmatige en geplande runs door elkaar</Label>
            <Button variant="ghost" onClick={() => setMonitoring(false)}>Sluiten</Button>
          </div>
          <WorkflowRuns workflowId={wf.id} />
        </div>
      )}
    </div>
  );
}
