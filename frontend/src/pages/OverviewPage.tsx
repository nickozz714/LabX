/**
 * pages/OverviewPage.tsx — één beeld over alle borden heen.
 *
 * De vraag die dit scherm beantwoordt is niet "hoe staat bord X ervoor" maar
 * "wat draait er, wat wacht er, en hoe liep het laatste af" — zonder eerst
 * zeven borden af te gaan. Vandaar dat het in één verzoek komt (/boards/
 * overview): drie losse aanroepen zouden elk een fractie later zijn opgehaald
 * en dus een beeld geven dat nergens tegelijk waar was.
 */
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { boardApi } from "@/lib/boards";
import type { OverviewDto, OverviewRunDto, PlanDto } from "@/lib/types";
import { Badge, Button, Card, EmptyState } from "@/components/ui";
import { Bot, Pause, Play, RefreshCw, X } from "lucide-react";

const PLAN_TOON: Record<string, "green" | "red" | "yellow" | "neutral" | "violet"> = {
  running: "yellow", paused: "red", scheduled: "violet", done: "green",
  cancelled: "neutral", draft: "neutral",
};
const ITEM_TOON: Record<string, "green" | "red" | "yellow" | "neutral" | "violet"> = {
  running: "yellow", failed: "red", done: "green", completed: "green",
  cancelled: "neutral", interrupted: "red",
  // Geen rood: een gebruikslimiet is geen fout, het werk gaat vanzelf verder.
  limited: "violet",
};

function tijd(waarde: string | null): string {
  if (!waarde) return "—";
  return new Date(waarde).toLocaleString(undefined, {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

function duur(van: string | null, tot: string | null): string {
  if (!van) return "";
  const eind = tot ? new Date(tot).getTime() : Date.now();
  const sec = Math.max(0, Math.round((eind - new Date(van).getTime()) / 1000));
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ${sec % 60}s`;
  return `${Math.floor(sec / 3600)}u ${Math.floor((sec % 3600) / 60)}m`;
}

export function OverviewPage() {
  const [data, setData] = useState<OverviewDto | null>(null);
  const [laden, setLaden] = useState(true);

  const refresh = useCallback(async () => {
    try {
      setData(await boardApi.overview());
    } finally {
      setLaden(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    // Kort interval: dit scherm is er juist om te zien wat er nú gebeurt.
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [refresh]);

  if (laden) return <div className="p-6 text-sm text-muted-foreground">Laden…</div>;
  if (!data || data.boards.length === 0) {
    return (
      <div className="p-6">
        <EmptyState>Nog geen boards. Maak er een aan bij Boards.</EmptyState>
      </div>
    );
  }

  // Wat er draait komt uit de RUNS zelf. Het meeste werk begint met "Agent
  // starten" op een ticket en hoort bij geen enkele planning — dit scherm
  // stond leeg zolang het alleen naar planningen keek.
  const lopend = data.running;
  const planningen = data.boards.flatMap((b) =>
    b.plans.map((p) => ({ board: b, plan: p })),
  );

  async function actie(fn: Promise<unknown>) {
    try {
      await fn;
    } finally {
      refresh();
    }
  }

  function PlanRegel({ board, plan }: { board: OverviewDto["boards"][number]; plan: PlanDto }) {
    const klaar = (plan.counts.done || 0) + (plan.counts.failed || 0);
    return (
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2 text-sm last:border-0">
        <Badge tone={PLAN_TOON[plan.state] || "neutral"}>{plan.state}</Badge>
        <Link to={`/boards/${board.id}`} className="font-medium hover:underline">
          {board.name}
        </Link>
        <span className="text-muted-foreground">·</span>
        <span>{plan.name}</span>
        <span className="text-xs text-muted-foreground">
          {klaar}/{plan.total}
          {plan.counts.failed ? ` · ${plan.counts.failed} mislukt` : ""}
          {plan.state === "scheduled" && plan.start_at ? ` · start ${tijd(plan.start_at)}` : ""}
          {plan.started_at ? ` · loopt ${duur(plan.started_at, plan.finished_at)}` : ""}
        </span>
        {board.lab_name && (
          <span className="text-xs text-muted-foreground">
            lab {board.lab_name}
            {board.lab_status && board.lab_status !== "running" ? ` (${board.lab_status})` : ""}
          </span>
        )}
        <div className="ml-auto flex items-center gap-2">
          {plan.state === "running" && (
            <button title="Pauzeren" onClick={() => actie(boardApi.pausePlan(board.id, plan.id))}>
              <Pause size={13} />
            </button>
          )}
          {["paused", "scheduled", "draft"].includes(plan.state) && (
            <button title="Starten / hervatten"
                    onClick={() => actie(boardApi.resumePlan(board.id, plan.id))}>
              <Play size={13} />
            </button>
          )}
          <button title="Afbreken" onClick={() => actie(boardApi.cancelPlan(board.id, plan.id))}>
            <X size={13} />
          </button>
        </div>
        {plan.note && <div className="w-full text-xs text-destructive">{plan.note}</div>}
      </div>
    );
  }

  /** Eén agent-run: welk ticket, waar, hoe lang, en hoe het afliep. */
  function RunRegel({ run, nu }: { run: OverviewRunDto; nu?: boolean }) {
    return (
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2 text-sm last:border-0">
        <Badge tone={ITEM_TOON[run.status] || "neutral"}>
          {run.status === "limited" ? "gepauzeerd" : run.status}
        </Badge>
        <Link to={`/boards/${run.board_id}`} className="font-mono text-xs hover:underline">
          {run.ticket_key}
        </Link>
        <span className="max-w-[22rem] truncate">{run.ticket_title}</span>
        <span className="text-xs text-muted-foreground">
          {run.board_name}
          {run.lab_name ? ` · lab ${run.lab_name}` : ""}
          {run.plan_name ? ` · planning ${run.plan_name}` : ""}
          {" · "}
          {tijd(run.started_at)}
          {run.started_at ? ` · ${duur(run.started_at, run.finished_at)}` : ""}
          {nu && run.steps ? ` · ${run.steps} stappen` : ""}
          {run.status === "limited" && run.resume_at
            ? ` · gaat verder om ${new Date(run.resume_at).toLocaleTimeString(undefined,
                { hour: "2-digit", minute: "2-digit" })}`
            : ""}
        </span>
        {run.thread_id && (
          <Link to={`/chat?thread=${run.thread_id}`}
                className="ml-auto whitespace-nowrap text-xs underline"
                title="Open de sessie van deze run als chat">
            meekijken
          </Link>
        )}
        {run.error && (
          <span className="w-full truncate text-xs text-destructive" title={run.error}>
            {run.error}
          </span>
        )}
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">Overzicht</h1>
        <Button variant="ghost" className="text-xs" onClick={refresh}>
          <RefreshCw size={13} /> Verversen
        </Button>
      </div>

      <Card className="p-0">
        <div className="border-b border-border px-3 py-2 text-sm font-semibold">
          Nu bezig ({lopend.length})
        </div>
        {lopend.length === 0 ? (
          <p className="px-3 py-2 text-sm text-muted-foreground">Er draait niets.</p>
        ) : (
          lopend.map((r) => <RunRegel key={r.run_id} run={r} nu />)
        )}
      </Card>

      {planningen.length > 0 && (
        <Card className="p-0">
          <div className="border-b border-border px-3 py-2 text-sm font-semibold">
            Planningen ({planningen.length})
          </div>
          {planningen.map(({ board, plan }) => <PlanRegel key={plan.id} board={board} plan={plan} />)}
        </Card>
      )}

      <div>
        <h2 className="mb-2 text-sm font-semibold">Boards</h2>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
          {data.boards.map((b) => (
            <Card key={b.id} className="p-3">
              <div className="flex items-center justify-between">
                <Link to={`/boards/${b.id}`} className="font-semibold hover:underline">
                  {b.name}
                </Link>
                <span className="text-xs text-muted-foreground">{b.ticket_total} tickets</span>
              </div>
              <div className="mt-1 flex flex-wrap gap-1 text-[11px] text-muted-foreground">
                {b.columns.map((c) => (
                  <span key={c.key} className="rounded bg-secondary px-1">
                    {c.name} {b.ticket_counts[c.key] || 0}
                  </span>
                ))}
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
                {b.lab_name ? (
                  <Badge tone={b.lab_status === "running" ? "green" : "neutral"}>
                    lab {b.lab_status}
                  </Badge>
                ) : (
                  <Badge tone="red">geen lab</Badge>
                )}
                {b.workers_total > 0 && (
                  <span className="text-muted-foreground" title="Bezette werkers van dit lab">
                    {b.workers_busy}/{b.workers_total} werkers bezig
                  </span>
                )}
                {b.wachtend_in_agentkolom > 0 && (
                  <span className="flex items-center gap-1 text-muted-foreground">
                    <Bot size={12} /> {b.wachtend_in_agentkolom} klaar om op te pakken
                  </span>
                )}
                {b.last_sync_error && (
                  <span className="text-destructive" title={b.last_sync_error}>sync-fout</span>
                )}
              </div>
            </Card>
          ))}
        </div>
      </div>

      <div>
        <h2 className="mb-2 text-sm font-semibold">Wat er gelopen heeft</h2>
        <Card className="p-0">
          {data.recent.length === 0 ? (
            <p className="px-3 py-2 text-sm text-muted-foreground">Nog niets gedraaid.</p>
          ) : (
            <div className="divide-y divide-border">
              {data.recent.map((r) => <RunRegel key={r.run_id} run={r} />)}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
