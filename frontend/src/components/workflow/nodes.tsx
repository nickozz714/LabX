/**
 * components/workflow/nodes.tsx — hoe een activiteit eruitziet op het doek.
 *
 * Bewust in de stijl van een pipeline-editor: een kaart met een icoon, een
 * naam, een regel die zegt wat hij doet, en gekleurde punten waar de
 * verbindingen aan hangen. Groen is "als dit lukt", rood "als dit misgaat" —
 * dat onderscheid is de helft van wat een workflow leesbaar maakt.
 *
 * De bubbel (`parallel`) is een groter vlak waar je activiteiten in sleept;
 * die draaien dan tegelijk. Hij heeft een streepjesrand, zodat je ziet dat het
 * een omhulsel is en geen stap.
 */
import { Handle, Position } from "@xyflow/react";
import type { NodeProps } from "@xyflow/react";

const ICOON: Record<string, string> = {
  agent: "🤖", shell: ">_", als: "?", wacht: "⏱", parallel: "⇉",
};

const SOORT_LABEL: Record<string, string> = {
  agent: "agent", shell: "shell", als: "als", wacht: "wacht", parallel: "tegelijk",
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

type Data = {
  naam: string;
  soort: string;
  samenvatting: string;
  status?: string;
  herhaalt?: string;
  verseSessie?: boolean;
  rol?: boolean;
  aantal?: number;
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

export function BubbelNode({ data, selected }: NodeProps) {
  const d = data as unknown as Data;
  return (
    <div
      className={`h-full w-full rounded-xl border-2 border-dashed bg-violet-500/5 ${
        selected ? "border-primary" : "border-violet-400/60"
      } ${d.status === "running" ? "animate-pulse" : ""}`}
    >
      <Handle type="target" position={Position.Top} className="!h-2 !w-2 !bg-muted-foreground" />
      <div className="flex items-center gap-2 px-3 py-1.5">
        <span className="text-sm">⇉</span>
        <span className="flex-1 truncate text-xs font-semibold">{d.naam}</span>
        <span className="rounded bg-violet-500/20 px-1 text-[10px] text-violet-700">
          {d.aantal ?? 0} tegelijk
        </span>
      </div>
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
