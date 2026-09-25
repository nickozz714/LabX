/**
 * components/WorkflowRuns.tsx — wat een workflow gedaan heeft.
 *
 * Dit is de laag die er niet was. Een run bewaarde alleen de eindtekst, en de
 * redenatie stond ergens in een chattranscript — dus bij een run die vannacht
 * half misging viel niet na te gaan wát het model kreeg, wat het besloot en
 * waarom het die kant op ging.
 *
 * Nu staat per activiteit de invoer, het antwoord, de tool-aanroepen
 * ertussenin, de duur en de prijs. Handmatige en geplande runs staan door
 * elkaar, want het is hetzelfde ding — dat was eerder verdeeld over twee
 * schermen waarvan je er altijd één miste.
 */
import { useEffect, useState } from "react";
import { workflowApi } from "@/lib/workflows";
import type { WorkflowRunDto, WorkflowRunStapDto } from "@/lib/types";
import { Badge, Button, EmptyState } from "@/components/ui";

const TOON: Record<string, "green" | "red" | "yellow" | "neutral" | "violet"> = {
  completed: "green", failed: "red", running: "yellow", pending: "yellow",
  cancelled: "neutral", ok: "green", fout: "red", overgeslagen: "neutral",
};

function duur(ms: number | null | undefined): string {
  if (!ms) return "";
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`;
}

export function WorkflowRuns({ workflowId }: { workflowId: number }) {
  const [runs, setRuns] = useState<WorkflowRunDto[]>([]);
  const [open, setOpen] = useState<string | null>(null);

  function laad() {
    workflowApi.runs(workflowId).then(setRuns).catch(() => setRuns([]));
  }
  useEffect(laad, [workflowId]);

  // Zolang er iets loopt: blijven kijken. Een workflow van zeven activiteiten
  // duurt minuten, en dan wil je de stappen zien binnenkomen.
  useEffect(() => {
    if (!runs.some((r) => ["running", "pending"].includes(r.status))) return;
    const t = setInterval(laad, 4000);
    return () => clearInterval(t);
  }, [runs, workflowId]);

  if (runs.length === 0) {
    return <EmptyState>Deze workflow heeft nog niet gedraaid.</EmptyState>;
  }

  return (
    <div className="space-y-2">
      {runs.map((r) => (
        <div key={r.id} className="rounded-md border border-border">
          <button className="flex w-full flex-wrap items-center gap-2 p-2 text-left text-sm"
                  onClick={() => setOpen(open === r.id ? null : r.id)}>
            <Badge tone={TOON[r.status] || "neutral"}>{r.status}</Badge>
            <span className="text-muted-foreground">
              {new Date(r.created_at).toLocaleString()}
            </span>
            <span className="text-xs text-muted-foreground">
              {r.trigger_type === "cron" ? "gepland" : "handmatig"}
            </span>
            <span className="flex-1" />
            {r.totals?.stappen ? (
              <span className="text-xs text-muted-foreground">{r.totals.stappen} activiteiten</span>
            ) : null}
            {r.totals?.cost_usd ? (
              <span className="text-xs text-muted-foreground">
                ${Number(r.totals.cost_usd).toFixed(3)}
              </span>
            ) : null}
            {["running", "pending"].includes(r.status) && (
              <Button variant="ghost" className="px-2 py-0.5 text-xs"
                      onClick={(e) => { e.stopPropagation();
                                        workflowApi.cancelRun(r.id).then(laad); }}>
                Afbreken
              </Button>
            )}
          </button>
          {open === r.id && <RunDetail runId={r.id} />}
        </div>
      ))}
    </div>
  );
}

function RunDetail({ runId }: { runId: string }) {
  const [run, setRun] = useState<WorkflowRunDto | null>(null);

  useEffect(() => {
    let weg = false;
    const haal = () => workflowApi.run_detail(runId).then((r) => !weg && setRun(r)).catch(() => {});
    haal();
    const t = setInterval(() => {
      if (run && !["running", "pending"].includes(run.status)) return;
      haal();
    }, 4000);
    return () => { weg = true; clearInterval(t); };
  }, [runId, run?.status]);

  if (!run) return <div className="p-2 text-xs text-muted-foreground">Laden…</div>;

  return (
    <div className="space-y-2 border-t border-border p-2">
      {run.error && (
        <p className="rounded-md border border-red-500/40 bg-red-500/10 p-2 text-xs">{run.error}</p>
      )}
      <div className="flex flex-wrap gap-3 text-[11px] text-muted-foreground">
        {run.started_at && <span>gestart {new Date(run.started_at).toLocaleTimeString()}</span>}
        {run.finished_at && <span>klaar {new Date(run.finished_at).toLocaleTimeString()}</span>}
        {run.totals?.input_tokens ? (
          <span>{run.totals.input_tokens} in / {run.totals.output_tokens} uit tokens</span>
        ) : null}
        {run.thread_id && <span>sessie {run.thread_id.slice(0, 8)}</span>}
      </div>
      {/* Waarmee hij draaide. Twee runs van dezelfde workflow kunnen voor een
          andere klant zijn geweest; zonder dit is achteraf niet te zien welke. */}
      {Object.keys(run.input || {}).length > 0 && (
        <div className="flex flex-wrap gap-1 text-[11px]">
          {Object.entries(run.input).map(([k, v]) => (
            <span key={k} className="rounded bg-secondary px-1.5 py-0.5">
              <span className="text-muted-foreground">{k}</span> {String(v)}
            </span>
          ))}
        </div>
      )}
      {(run.stappen || []).map((s) => <Stap key={s.id} stap={s} />)}
      {(run.stappen || []).length === 0 && (
        <p className="text-xs text-muted-foreground">Nog geen activiteiten gedraaid.</p>
      )}
    </div>
  );
}

/** Een korte aanduiding van het element van deze ronde, zodat je in de lijst
 *  ziet wélk incident (of welke klant) er langskwam en niet alleen "ronde 7". */
function kortItem(item: string | null): string {
  if (!item) return "";
  try {
    const waarde = JSON.parse(item);
    if (typeof waarde === "string") return waarde.slice(0, 40);
    if (waarde && typeof waarde === "object") {
      for (const sleutel of ["title", "titel", "naam", "name", "id"]) {
        const v = (waarde as Record<string, unknown>)[sleutel];
        if (typeof v === "string" && v) return v.slice(0, 40);
      }
    }
  } catch {
    return item.slice(0, 40);
  }
  return "";
}

function Stap({ stap }: { stap: WorkflowRunStapDto }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-md border border-border bg-secondary/20">
      <button className="flex w-full flex-wrap items-center gap-2 p-2 text-left text-xs"
              onClick={() => setOpen(!open)}>
        <span className="font-mono text-muted-foreground">{stap.volgnummer}</span>
        <Badge tone={TOON[stap.status] || "neutral"}>{stap.status}</Badge>
        <span className="font-medium">{stap.naam}</span>
        <span className="text-muted-foreground">{stap.soort}</span>
        {stap.iteratie ? (
          <span className="text-muted-foreground">
            ronde {stap.iteratie}
            {kortItem(stap.item) && <span className="ml-1">· {kortItem(stap.item)}</span>}
          </span>
        ) : null}
        {stap.tak && <span className="text-muted-foreground">→ {stap.tak}</span>}
        <span className="flex-1" />
        {stap.duur_ms ? <span className="text-muted-foreground">{duur(stap.duur_ms)}</span> : null}
        {stap.cost_usd ? (
          <span className="text-muted-foreground">${stap.cost_usd.toFixed(3)}</span>
        ) : null}
      </button>
      {open && (
        <div className="space-y-2 border-t border-border p-2 text-xs">
          {stap.item && (
            <Blok titel="Element van deze ronde" tekst={stap.item} />
          )}
          <Blok
            titel={stap.soort === "als"
              ? "Waarop de keuze viel"
              : stap.soort === "voorelk"
                ? "De lijst waar hij langs liep"
                : "Invoer (wat het model kreeg)"}
            tekst={stap.invoer}
          />
          {stap.stappen?.length > 0 && (
            <div>
              <div className="mb-1 font-semibold text-muted-foreground">
                Onderweg ({stap.stappen.length})
              </div>
              <div className="space-y-0.5">
                {stap.stappen.map((s, i) => (
                  <div key={i} className="truncate rounded bg-background px-2 py-1">
                    {s.kind === "tool"
                      ? `🔧 ${s.name}${s.input ? " " + JSON.stringify(s.input).slice(0, 120) : ""}`
                      : `💭 ${(s.text || "").slice(0, 160)}`}
                  </div>
                ))}
              </div>
            </div>
          )}
          <Blok titel="Uitvoer" tekst={stap.uitvoer} />
          {stap.resultaat != null && stap.soort !== "als" && (
            <Blok titel="Gestructureerd resultaat"
                  tekst={JSON.stringify(stap.resultaat, null, 2)} />
          )}
          {stap.error && <Blok titel="Fout" tekst={stap.error} />}
          {stap.exit_code != null && (
            <div className="text-muted-foreground">exit code {stap.exit_code}</div>
          )}
        </div>
      )}
    </div>
  );
}

function Blok({ titel, tekst }: { titel: string; tekst: string | null }) {
  if (!tekst) return null;
  return (
    <div>
      <div className="mb-1 font-semibold text-muted-foreground">{titel}</div>
      <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded bg-background p-2">
        {tekst}
      </pre>
    </div>
  );
}
