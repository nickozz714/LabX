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
  addEdge, useEdgesState, useNodesState,
} from "@xyflow/react";
import type { Connection, Edge, Node, NodeChange, EdgeChange } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { WorkflowEdge, WorkflowNode } from "@/lib/types";
import { MaatContext, NODE_TYPES, TAK_KLEUR } from "@/components/workflow/nodes";

const BUBBEL_BREEDTE = 620;
const BUBBEL_HOOGTE = 220;
/** De maat van een activiteitkaart (w-56 plus wat hij hoog wordt), waarmee we
 *  uitrekenen hoe klein een bubbel nog mag worden. */
const KAART_BREEDTE = 224;
const KAART_HOOGTE = 104;
const KANTLIJN = 16;

/** Hoe groot dit omhulsel is: wat je zelf instelde, anders de standaardmaat. */
function maatVan(n: WorkflowNode): { breedte: number; hoogte: number } {
  return { breedte: n.breedte || BUBBEL_BREEDTE, hoogte: n.hoogte || BUBBEL_HOOGTE };
}

/** De kleinste maat waarbij alles wat erin ligt nog binnen de rand past.
 *  Kleiner mogen maken zou een activiteit uit beeld duwen. */
function minimumMaat(nodes: WorkflowNode[], groep: string) {
  const kinderen = nodes.filter((n) => n.groep === groep);
  return {
    breedte: Math.max(280, ...kinderen.map(
      (k) => (k.positie?.x ?? 0) + KAART_BREEDTE + KANTLIJN)),
    hoogte: Math.max(140, ...kinderen.map(
      (k) => (k.positie?.y ?? 0) + KAART_HOOGTE + KANTLIJN)),
  };
}

function samenvatting(n: WorkflowNode): string {
  if (n.type === "agent") return (n.prompt || "").slice(0, 120) || "— nog geen opdracht —";
  if (n.type === "shell") return (n.commando || "").slice(0, 120) || "— nog geen commando —";
  if (n.type === "als") {
    const c = n.conditie;
    return c?.links ? `${c.links} ${c.operator} ${c.rechts ?? ""}` : "— nog geen voorwaarde —";
  }
  if (n.type === "wacht") return `${n.seconden ?? 30} seconden`;
  if (n.type === "parallel") return "activiteiten hierin draaien tegelijk";
  if (n.type === "voorelk") {
    return n.bron ? `één ronde per element van ${n.bron}` : "— nog geen lijst gekozen —";
  }
  return "";
}

/** De graaf van LabX → wat React Flow tekent. */
function naarFlow(nodes: WorkflowNode[], status: Record<string, string>): Node[] {
  const bubbels = nodes.filter((n) => n.type === "parallel" || n.type === "voorelk");
  const gewoon = nodes.filter((n) => n.type !== "parallel" && n.type !== "voorelk");
  // Bubbels eerst: React Flow wil een ouder vóór zijn kinderen in de lijst.
  return [
    ...bubbels.map((n) => {
      const maat = maatVan(n);
      const minimum = minimumMaat(nodes, n.id);
      return {
        id: n.id,
        type: "bubbel",
        position: n.positie || { x: 0, y: 0 },
        style: { width: maat.breedte, height: maat.hoogte },
        width: maat.breedte,
        height: maat.hoogte,
        data: {
          naam: n.naam, soort: n.type, samenvatting: samenvatting(n),
          status: status[n.id],
          aantal: nodes.filter((k) => k.groep === n.id).length,
          minBreedte: minimum.breedte, minHoogte: minimum.hoogte,
        },
      };
    }),
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
  // De laatste graaf die we ZELF naar buiten stuurden. Zonder dit zou elke
  // wijziging van buiten (opslaan geeft de genormaliseerde graaf terug) de
  // posities weer terugzetten naar waar ze stonden.
  const eigen = useRef<string>("");
  const edgesRef = useRef<Edge[]>([]);
  edgesRef.current = rfEdges;

  // De graaf opnieuw opbouwen doen we ALLEEN als de graaf zelf veranderde.
  useEffect(() => {
    const sleutel = JSON.stringify({ nodes, edges });
    if (sleutel === eigen.current) return;
    eigen.current = sleutel;
    setRfNodes(naarFlow(nodes, status));
    setRfEdges(naarFlowEdges(edges));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edges, setRfNodes, setRfEdges]);

  // De status hangt er los overheen: alleen het veld `status` van elke node
  // bijwerken, zodat er niets opnieuw gemaakt of verplaatst wordt.
  useEffect(() => {
    setRfNodes((huidig) => huidig.map((n) => (
      n.data?.status === status[n.id]
        ? n
        : { ...n, data: { ...n.data, status: status[n.id] } })));
  }, [status, setRfNodes]);

  /** De graaf naar buiten sturen. `herbouw` als de OUDERS veranderd zijn: dan
   *  moet React Flow zijn nodes opnieuw opbouwen, want een kind verhuizen is
   *  meer dan een positie. */
  const stuur = useCallback((uitNodes: WorkflowNode[], uitEdges: WorkflowEdge[],
                             herbouw = false) => {
    eigen.current = herbouw ? "" : JSON.stringify({ nodes: uitNodes, edges: uitEdges });
    onChange(uitNodes, uitEdges);
  }, [onChange]);

  /**
   * Loslaten na het slepen. Dit is de enige plek waar een positie of een groep
   * wordt vastgelegd — eerder deed `onNodesChange` dat óók, met de nodes van
   * vóór de herindeling, en dan won die: je activiteit sprong meteen de lus
   * weer uit.
   *
   * We rekenen met de graaf van LabX zelf en niet met wat React Flow in zijn
   * state heeft: die lijst is in deze callback een render oud, en een groep
   * heeft daar geen betrouwbare afmeting.
   */
  const opDragStop = useCallback((_e: unknown, rfNode: Node) => {
    const gesleept = nodes.find((n) => n.id === rfNode.id);
    if (!gesleept) return;
    const groepen = nodes.filter((n) => n.type === "parallel" || n.type === "voorelk");
    const ouderNu = nodes.find((n) => n.id === gesleept.groep);
    // De positie van React Flow is relatief aan de huidige ouder; wij rekenen
    // alles om naar het doek zelf.
    const abs = {
      x: rfNode.position.x + (ouderNu?.positie?.x ?? 0),
      y: rfNode.position.y + (ouderNu?.positie?.y ?? 0),
    };
    // Het MIDDEN van de kaart bepaalt waar hij in valt — een hoek die net over
    // de rand steekt hoort niet te tellen.
    const midden = { x: abs.x + 112, y: abs.y + 40 };
    const zelfEenGroep = gesleept.type === "parallel" || gesleept.type === "voorelk";
    const doel = zelfEenGroep ? undefined : groepen.find((g) => {
      const gx = g.positie?.x ?? 0;
      const gy = g.positie?.y ?? 0;
      const { breedte, hoogte } = maatVan(g);
      return midden.x >= gx && midden.x <= gx + breedte
          && midden.y >= gy && midden.y <= gy + hoogte;
    });
    const nieuweGroep = doel?.id;
    const positie = nieuweGroep
      ? { x: Math.max(12, Math.round(abs.x - (doel?.positie?.x ?? 0))),
          y: Math.max(44, Math.round(abs.y - (doel?.positie?.y ?? 0))) }
      : { x: Math.round(abs.x), y: Math.round(abs.y) };
    const veranderdeOuder = (nieuweGroep || undefined) !== (gesleept.groep || undefined);
    stuur(nodes.map((n) => (n.id === gesleept.id
      ? { ...n, positie, groep: nieuweGroep }
      : n)), edges, veranderdeOuder);
  }, [edges, nodes, stuur]);

  /** Een bubbel is van maat veranderd. Slepen aan de bovenkant of de
   *  linkerkant verplaatst hem óók, dus we leggen positie én maat vast. */
  const opMaat = useCallback((id: string,
                              maat: { x: number; y: number; width: number; height: number }) => {
    stuur(nodes.map((n) => (n.id === id
      ? { ...n, positie: { x: Math.round(maat.x), y: Math.round(maat.y) },
          breedte: Math.round(maat.width), hoogte: Math.round(maat.height) }
      : n)), edges);
  }, [edges, nodes, stuur]);

  const opNodesChange = useCallback((changes: NodeChange<Node>[]) => {
    onNodesChange(changes);
    // Alleen verwijderen hoort hier nog door te gaan; posities en groepen gaan
    // via opDragStop, zodat er maar één plek is die ze vastlegt.
    const weg = new Set(changes.filter((c) => c.type === "remove").map((c) => c.id));
    if (!weg.size) return;
    const over = nodes.filter((n) => !weg.has(n.id) && !weg.has(n.groep || ""));
    const ids = new Set(over.map((n) => n.id));
    queueMicrotask(() => stuur(over, edges.filter((e) => ids.has(e.van) && ids.has(e.naar)),
                               true));
  }, [edges, nodes, onNodesChange, stuur]);

  const opEdgesChange = useCallback((changes: EdgeChange<Edge>[]) => {
    onEdgesChange(changes);
    const weg = new Set(changes.filter((c) => c.type === "remove").map((c) => c.id));
    if (!weg.size) return;
    const over = edgesRef.current.filter((e) => !weg.has(e.id));
    queueMicrotask(() => stuur(nodes, over.map((re) => ({
      van: re.source, naar: re.target,
      soort: ((re.data as { soort?: string })?.soort || re.sourceHandle
              || "succes") as WorkflowEdge["soort"],
    }))));
  }, [nodes, onEdgesChange, stuur]);

  const opVerbinden = useCallback((verbinding: Connection) => {
    const soort = (verbinding.sourceHandle || "succes") as WorkflowEdge["soort"];
    stuur(nodes, [...edges, { van: verbinding.source, naar: verbinding.target, soort }]);
    setRfEdges((huidig) => addEdge({
      ...verbinding,
      label: soort,
      style: { stroke: TAK_KLEUR[soort] || "#94a3b8", strokeWidth: 2 },
      labelStyle: { fontSize: 10, fill: TAK_KLEUR[soort] || "#94a3b8" },
      data: { soort },
    }, huidig));
  }, [edges, nodes, setRfEdges, stuur]);

  const gekleurd = useMemo(
    () => rfNodes.map((n) => ({ ...n, selected: n.id === geselecteerd })),
    [rfNodes, geselecteerd]);

  return (
    <MaatContext.Provider value={opMaat}>
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
    </MaatContext.Provider>
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
