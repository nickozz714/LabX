/**
 * components/workflow/nodes.tsx — hoe een activiteit eruitziet op het doek.
 *
 * Bewust in de stijl van een pipeline-editor: een kaart met een icoon, een
 * naam, een regel die zegt wat hij doet, en gekleurde punten waar de
 * verbindingen aan hangen. Groen is "als dit lukt", rood "als dit misgaat" —
 * dat onderscheid is de helft van wat een workflow leesbaar maakt.
 *
 * De bubbel (`parallel`) en de lus (`voorelk`) zijn een groter vlak waar je
 * activiteiten in sleept. Ze hebben een streepjesrand, zodat je ziet dat het
 * een omhulsel is en geen stap, en ze zijn te verslepen aan de randen: hoeveel
 * er in moet passen weet alleen jij, dus een vaste maat werkt niet. Kleiner
 * dan wat erin ligt kan niet — dan zou je een activiteit buiten beeld
 * duwen.
 */
import { createContext, useContext } from "react";
import { Handle, NodeResizer, Position } from "@xyflow/react";
import type { NodeProps } from "@xyflow/react";

const ICOON: Record<string, string> = {
  agent: "🤖", shell: ">_", als: "?", wacht: "⏱", parallel: "⇉", voorelk: "↻",
};

const SOORT_LABEL: Record<string, string> = {
  agent: "agent", shell: "shell", als: "als", wacht: "wacht", parallel: "tegelijk",
  voorelk: "voor elk",
};

/** De kleuren van de verbindingen: groen lukt, rood mislukt, blauw altijd. */
export const TAK_KLEUR: Record<string, string> = {
  succes: "#22c55e", fout: "#ef4444", altijd: "#3b82f6",
  ja: "#22c55e", nee: "#f59e0b",
};

function statusRand(status?: string): string {
  if (status === "ok") return "border-green-500";
  if (status === "fout") return "border-red-500";
  if (status === "running") return "border-yellow-500 animate-pulse";
  if (status === "overgeslagen") return "border-dashed border-muted-foreground";
  return "border-border";
}

/** Hoe een bubbel zijn nieuwe maat teruggeeft aan het doek. Via een context,
 *  zodat de node zelf geen callback in zijn data hoeft te dragen: die data
 *  wordt alleen herbouwd als de graaf verandert, en een callback zou dan oud
 *  zijn. */
export const MaatContext = createContext<
  (id: string, maat: { x: number; y: number; width: number; height: number }) => void>(
  () => {});

type Data = {
  naam: string;
  soort: string;
  samenvatting: string;
  status?: string;
  herhaalt?: string;
  verseSessie?: boolean;
  rol?: boolean;
  aantal?: number;
  /** Alleen op een bubbel: hoe klein hij hoogstens mag worden zonder dat er
   *  iets uit valt. */
  minBreedte?: number;
  minHoogte?: number;
};

export function ActiviteitNode({ data, selected }: NodeProps) {
  const d = data as unknown as Data;
  const alsNode = d.soort === "als";
  return (
    <div
      className={`w-56 rounded-lg border-2 bg-card px-3 py-2 shadow-sm ${statusRand(d.status)} ${
        selected ? "ring-2 ring-primary" : ""
      }`}
    >
      <Handle type="target" position={Position.Top} className="!h-2 !w-2 !bg-muted-foreground" />
      <div className="flex items-center gap-2">
        <span className="text-sm">{ICOON[d.soort] || "•"}</span>
        <span className="flex-1 truncate text-xs font-semibold">{d.naam}</span>
        <span className="rounded bg-secondary px-1 text-[10px] text-muted-foreground">
          {SOORT_LABEL[d.soort] || d.soort}
        </span>
      </div>
      <div className="mt-1 line-clamp-2 text-[11px] text-muted-foreground">{d.samenvatting}</div>
      {(d.herhaalt || d.verseSessie || d.rol) && (
        <div className="mt-1 flex flex-wrap gap-1">
          {d.herhaalt && (
            <span className="rounded bg-violet-500/15 px-1 text-[10px] text-violet-600"
                  title={d.herhaalt}>↻ herhaalt</span>
          )}
          {d.verseSessie && (
            <span className="rounded bg-blue-500/15 px-1 text-[10px] text-blue-600"
                  title="Begint met een schone sessie">✧ vers</span>
          )}
          {d.rol && (
            <span className="rounded bg-secondary px-1 text-[10px] text-muted-foreground"
                  title="Heeft een eigen rolbeschrijving">rol</span>
          )}
        </div>
      )}
      {alsNode ? (
        <>
          <Handle id="ja" type="source" position={Position.Bottom}
                  style={{ left: "30%", background: TAK_KLEUR.ja }}
                  className="!h-2.5 !w-2.5" />
          <Handle id="nee" type="source" position={Position.Bottom}
                  style={{ left: "70%", background: TAK_KLEUR.nee }}
                  className="!h-2.5 !w-2.5" />
        </>
      ) : (
        <>
          <Handle id="succes" type="source" position={Position.Bottom}
                  style={{ left: "35%", background: TAK_KLEUR.succes }}
                  className="!h-2.5 !w-2.5" />
          <Handle id="fout" type="source" position={Position.Bottom}
                  style={{ left: "65%", background: TAK_KLEUR.fout }}
                  className="!h-2.5 !w-2.5" />
        </>
      )}
    </div>
  );
}

export function BubbelNode({ id, data, selected }: NodeProps) {
  const d = data as unknown as Data;
  const maatGewijzigd = useContext(MaatContext);
  // Een lus en een bubbel zien er bewust anders uit: de een doet alles ACHTER
  // elkaar per element, de ander alles TEGELIJK. Dat verschil moet je op het
  // doek kunnen zien zonder het paneel te openen.
  const lus = d.soort === "voorelk";
  return (
    <div
      className={`h-full w-full rounded-xl border-2 border-dashed ${
        lus ? "bg-amber-500/5" : "bg-violet-500/5"
      } ${selected ? "border-primary" : lus ? "border-amber-400/70" : "border-violet-400/60"
      } ${d.status === "running" ? "animate-pulse" : ""}`}
    >
      <NodeResizer
        isVisible={Boolean(selected)}
        minWidth={d.minBreedte ?? 280}
        minHeight={d.minHoogte ?? 140}
        color={lus ? "#f59e0b" : "#8b5cf6"}
        handleClassName="!h-2.5 !w-2.5 !rounded-sm"
        onResizeEnd={(_e, maat) => maatGewijzigd(id, maat)}
      />
      <Handle type="target" position={Position.Top} className="!h-2 !w-2 !bg-muted-foreground" />
      <div className="flex items-center gap-2 px-3 py-1.5">
        <span className="text-sm">{lus ? "↻" : "⇉"}</span>
        <span className="flex-1 truncate text-xs font-semibold">{d.naam}</span>
        <span className={`rounded px-1 text-[10px] ${
          lus ? "bg-amber-500/20 text-amber-700" : "bg-violet-500/20 text-violet-700"}`}>
          {lus ? `${d.aantal ?? 0} per element` : `${d.aantal ?? 0} tegelijk`}
        </span>
      </div>
      {lus && d.samenvatting && (
        <div className="truncate px-3 text-[10px] text-muted-foreground">{d.samenvatting}</div>
      )}
      <Handle id="succes" type="source" position={Position.Bottom}
              style={{ left: "35%", background: TAK_KLEUR.succes }}
              className="!h-2.5 !w-2.5" />
      <Handle id="fout" type="source" position={Position.Bottom}
              style={{ left: "65%", background: TAK_KLEUR.fout }}
              className="!h-2.5 !w-2.5" />
    </div>
  );
}

export const NODE_TYPES = { activiteit: ActiviteitNode, bubbel: BubbelNode };
