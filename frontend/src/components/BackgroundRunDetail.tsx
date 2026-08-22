/**
 * components/BackgroundRunDetail.tsx — shared background-run rendering:
 * status/duration helpers + the live-tailing detail modal, used by both the
 * Achtergrondtaken page and the inline task panel in ChatPage.
 */
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { chatApi } from "@/lib/chat";
import type { BackgroundRunDto, ChatEvent } from "@/lib/types";
import { Badge, Button, Modal } from "@/components/ui";

export function statusTone(status: BackgroundRunDto["status"]) {
  return ({ running: "yellow", completed: "green", failed: "red", cancelled: "neutral", interrupted: "neutral" } as const)[status];
}

export function runDuration(r: Pick<BackgroundRunDto, "started_at" | "finished_at">): string {
  if (!r.started_at) return "-";
  const end = r.finished_at ? new Date(r.finished_at).getTime() : Date.now();
  const secs = Math.max(0, Math.round((end - new Date(r.started_at).getTime()) / 1000));
  if (secs < 60) return `${secs}s`;
  return `${Math.floor(secs / 60)}m ${secs % 60}s`;
}

export function RunDetailModal({ run, onClose }: { run: BackgroundRunDto; onClose: () => void }) {
  const [steps, setSteps] = useState<ChatEvent[]>([]);
  const [answer, setAnswer] = useState("");
  const [status, setStatus] = useState<string>(run.status);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    setSteps([]);
    setAnswer("");
    setStatus(run.status);
    const controller = new AbortController();
    abortRef.current = controller;
    chatApi
      .streamBackgroundRun(
        run.id,
        (ev) => {
          if (ev.kind === "thinking" || ev.kind === "tool") setSteps((prev) => [...prev, ev]);
          if (ev.kind === "delta") setAnswer((prev) => prev + ev.text);
          if (ev.kind === "answer") setAnswer(ev.text);
          if (ev.kind === "run_status") setStatus(ev.status);
        },
        controller.signal,
      )
      .catch(() => {});
    return () => controller.abort();
  }, [run.id]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Modal open onClose={onClose} title="Achtergrondtaak" wide>
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <Badge tone={statusTone(status as BackgroundRunDto["status"]) || "neutral"}>{status}</Badge>
          <span className="text-xs text-muted-foreground">
            taak {run.id.slice(0, 8)} · {runDuration({ ...run })}
          </span>
          {status === "running" && (
            <Button
              variant="danger"
              className="ml-auto px-2 py-1 text-xs"
              onClick={() => chatApi.cancelBackgroundRun(run.id).then(() => setStatus("cancelled"))}
            >
              Annuleren
            </Button>
          )}
        </div>
        <div className="rounded-md border border-border bg-secondary/50 p-2 text-sm">{run.prompt}</div>
        {run.error && <p className="text-sm text-destructive">{run.error}</p>}
        {steps.length > 0 && (
          <div className="max-h-48 space-y-0.5 overflow-y-auto rounded-md border border-border p-2 text-xs text-muted-foreground">
            {steps.map((s, i) => (
              <div key={i}>{s.kind === "tool" ? `🔧 ${(s as any).name}` : (s as any).text}</div>
            ))}
          </div>
        )}
        {answer ? (
          <div className="markdown-body max-h-96 overflow-y-auto rounded-md border border-border p-3">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{answer}</ReactMarkdown>
          </div>
        ) : (
          status === "running" && <p className="text-sm text-muted-foreground">Bezig…</p>
        )}
      </div>
    </Modal>
  );
}
