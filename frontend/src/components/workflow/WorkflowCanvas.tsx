/**
 * components/workflow/WorkflowCanvas.tsx — de workflow als tekening.
 *
 * Vertaalt de graaf van LabX (activiteiten + verbindingen met een soort) naar
 * React Flow en terug. Twee dingen zijn hier de moeite waard om te weten:
 *
 * - **De soort van een verbinding komt uit het punt waar je begint te slepen.**
 *   Een activiteit heeft een groen punt (bij succes) en een rood (bij fout),
 *   een `als` heeft ja en nee. Zo hoef je na het verbinden niets meer in te
 *   stellen — de tekening zegt meteen wat er gebeurt.
 * - **Een activiteit in een bubbel slepen maakt hem parallel.** Laat je hem op
 *   een bubbel vallen, dan wordt hij er kind van; sleep je hem eruit, dan staat
 *   hij weer in de hoofdstroom. Dat is de hele bediening van parallel draaien.
 */
import { useCallback, useEffect, useMemo, useRef } from "react";
import {
  Background, BackgroundVariant, Controls, MiniMap, ReactFlow, ReactFlowProvider,
  addEdge, useEdgesState, useNodesState, useReactFlow,
} from "@xyflow/react";
import type { Connection, Edge, Node, NodeChange, EdgeChange } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { WorkflowEdge, WorkflowNode } from "@/lib/types";
import { NODE_TYPES, TAK_KLEUR } from "@/components/workflow/nodes";

const BUBBEL_BREEDTE = 620;
const BUBBEL_HOOGTE = 220;

function samenvatting(n: WorkflowNode): string {
  if (n.type === "agent") return (n.prompt || "").slice(0, 120) || "— nog geen opdracht —";
  if (n.type === "shell") return (n.commando || "").slice(0, 120) || "— nog geen commando —";
  if (n.type === "als") {
    const c = n.conditie;
    return c?.links ? `${c.links} ${c.operator} ${c.rechts ?? ""}` : "— nog geen voorwaarde —";
  }
  if (n.type === "wacht") return `${n.seconden ?? 30} seconden`;
  if (n.type === "parallel") return "activiteiten hierin draaien tegelijk";
  return "";
}

/** De graaf van LabX → wat React Flow tekent. */
function naarFlow(nodes: WorkflowNode[], status: Record<string, string>): Node[] {
  const bubbels = nodes.filter((n) => n.type === "parallel");
  const gewoon = nodes.filter((n) => n.type !== "parallel");
  // Bubbels eerst: React Flow wil een ouder vóór zijn kinderen in de lijst.
  return [
    ...bubbels.map((n) => ({
      id: n.id,
      type: "bubbel",
      position: n.positie || { x: 0, y: 0 },
      style: { width: BUBBEL_BREEDTE, height: BUBBEL_HOOGTE },
      data: {
        naam: n.naam, soort: n.type, samenvatting: samenvatting(n),
        status: status[n.id],
        aantal: nodes.filter((k) => k.groep === n.id).length,
      },
    })),
    ...gewoon.map((n) => ({
      id: n.id,
      type: "activiteit",
      position: n.positie || { x: 0, y: 0 },
      parentId: n.groep || undefined,
      extent: n.groep ? ("parent" as const) : undefined,
      data: {
        naam: n.naam, soort: n.type, samenvatting: samenvatting(n),
        status: status[n.id],
        herhaalt: n.herhaal_over ? `herhaalt over ${n.herhaal_over}`
                  : n.herhaal_tot ? "herhaalt tot een voorwaarde klopt" : undefined,
        verseSessie: n.verse_sessie, rol: Boolean(n.rol),
      },
    })),
  ];
}

function naarFlowEdges(edges: WorkflowEdge[]): Edge[] {
  return edges.map((e, i) => ({
    id: `e${i}-${e.van}-${e.naar}-${e.soort}`,
    source: e.van,
    target: e.naar,
    sourceHandle: ["ja", "nee"].includes(e.soort) ? e.soort
                  : e.soort === "fout" ? "fout" : "succes",
    label: e.soort,
    animated: e.soort === "altijd",
    style: { stroke: TAK_KLEUR[e.soort] || "#94a3b8", strokeWidth: 2 },
    labelStyle: { fontSize: 10, fill: TAK_KLEUR[e.soort] || "#94a3b8" },
    data: { soort: e.soort },
  }));
}

function Doek({ nodes, edges, status, geselecteerd, onSelect, onChange }: {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  status: Record<string, string>;
  geselecteerd: string | null;
  onSelect: (id: string | null) => void;
  onChange: (nodes: WorkflowNode[], edges: WorkflowEdge[]) => void;
}) {
  const [rfNodes, setRfNodes, onNodesChange] = useNodesState<Node>([]);
  const [rfEdges, setRfEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const { getIntersectingNodes } = useReactFlow();
  // De laatste graaf die we ZELF naar buiten stuurden. Zonder dit zou elke
  // wijziging van buiten (opslaan geeft de genormaliseerde graaf terug) de
  // posities weer terugzetten naar waar ze stonden.
  const eigen = useRef<string>("");

  useEffect(() => {
    const sleutel = JSON.stringify({ nodes, edges });
    if (sleutel === eigen.current) return;
    setRfNodes(naarFlow(nodes, status));
    setRfEdges(naarFlowEdges(edges));
  }, [nodes, edges, status, setRfNodes, setRfEdges]);

  const stuurDoor = useCallback((vNodes: Node[], vEdges: Edge[]) => {
    const uit: WorkflowNode[] = vNodes.map((rn) => {
      const origineel = nodes.find((n) => n.id === rn.id);
      return {
        ...(origineel as WorkflowNode),
        positie: { x: Math.round(rn.position.x), y: Math.round(rn.position.y) },
        groep: (rn.parentId as string) || undefined,
      };
    });
    const uitEdges: WorkflowEdge[] = vEdges.map((re) => ({
      van: re.source,
      naar: re.target,
      soort: ((re.data as { soort?: string })?.soort
              || re.sourceHandle
              || "succes") as WorkflowEdge["soort"],
    }));
    eigen.current = JSON.stringify({ nodes: uit, edges: uitEdges });
    onChange(uit, uitEdges);
  }, [nodes, onChange]);

  const opNodesChange = useCallback((changes: NodeChange<Node>[]) => {
    onNodesChange(changes);
    // Alleen doorsturen als er iets BLIJVENDS veranderde: een selectie of een
    // muisbeweging is geen wijziging van de workflow.
    if (changes.some((c) => c.type === "position" && !c.dragging)) {
      setRfNodes((huidig) => { stuurDoor(huidig, rfEdges); return huidig; });
    }
  }, [onNodesChange, rfEdges, setRfNodes, stuurDoor]);

  const opEdgesChange = useCallback((changes: EdgeChange<Edge>[]) => {
    onEdgesChange(changes);
    if (changes.some((c) => c.type === "remove")) {
      setRfEdges((huidig) => { stuurDoor(rfNodes, huidig); return huidig; });
    }
  }, [onEdgesChange, rfNodes, setRfEdges, stuurDoor]);

  const opVerbinden = useCallback((verbinding: Connection) => {
    const soort = (verbinding.sourceHandle || "succes") as WorkflowEdge["soort"];
    const nieuw = addEdge({
      ...verbinding,
      label: soort,
      style: { stroke: TAK_KLEUR[soort] || "#94a3b8", strokeWidth: 2 },
      labelStyle: { fontSize: 10, fill: TAK_KLEUR[soort] || "#94a3b8" },
      data: { soort },
    }, rfEdges);
    setRfEdges(nieuw);
    stuurDoor(rfNodes, nieuw);
  }, [rfEdges, rfNodes, setRfEdges, stuurDoor]);

  /** Een activiteit die op een bubbel valt, wordt er kind van — en andersom. */
  const opDragStop = useCallback((_e: unknown, node: Node) => {
    if (node.type === "bubbel") { stuurDoor(rfNodes, rfEdges); return; }
    const overlap = getIntersectingNodes(node).filter((n) => n.type === "bubbel");
    const nieuweOuder = overlap[0]?.id;
    if (nieuweOuder === node.parentId) { stuurDoor(rfNodes, rfEdges); return; }

    const bijgewerkt = rfNodes.map((n) => {
      if (n.id !== node.id) return n;
      const bubbel = rfNodes.find((b) => b.id === (nieuweOuder || n.parentId));
      if (nieuweOuder && bubbel) {
        // Positie wordt relatief aan de bubbel.
        return { ...n, parentId: nieuweOuder, extent: "parent" as const,
                 position: { x: Math.max(8, node.position.x - bubbel.position.x),
                             y: Math.max(32, node.position.y - bubbel.position.y) } };
      }
      // Eruit gesleept: terug naar absolute positie.
      return { ...n, parentId: undefined, extent: undefined,
               position: { x: node.position.x + (bubbel?.position.x || 0),
                           y: node.position.y + (bubbel?.position.y || 0) } };
    });
    setRfNodes(bijgewerkt);
    stuurDoor(bijgewerkt, rfEdges);
  }, [getIntersectingNodes, rfEdges, rfNodes, setRfNodes, stuurDoor]);

  const gekleurd = useMemo(
    () => rfNodes.map((n) => ({ ...n, selected: n.id === geselecteerd })),
    [rfNodes, geselecteerd]);

  return (
    <ReactFlow
      nodes={gekleurd}
      edges={rfEdges}
      nodeTypes={NODE_TYPES}
      onNodesChange={opNodesChange}
      onEdgesChange={opEdgesChange}
      onConnect={opVerbinden}
      onNodeDragStop={opDragStop}
      onNodeClick={(_e, n) => onSelect(n.id)}
      onPaneClick={() => onSelect(null)}
      fitView
      proOptions={{ hideAttribution: false }}
    >
      <Background variant={BackgroundVariant.Dots} gap={16} size={1} />
      <Controls />
      <MiniMap pannable zoomable className="!bg-card" />
    </ReactFlow>
  );
}

export function WorkflowCanvas(props: {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  status?: Record<string, string>;
  geselecteerd: string | null;
  onSelect: (id: string | null) => void;
  onChange: (nodes: WorkflowNode[], edges: WorkflowEdge[]) => void;
}) {
  return (
    <ReactFlowProvider>
      <Doek {...props} status={props.status || {}} />
    </ReactFlowProvider>
  );
}
