/**
 * components/TicketDrawer.tsx — het ticketdetail naast het bord.
 *
 * De indeling volgt één regel, en die is er niet voor de sier: **de opdracht,
 * het meetlint en het werklogboek staan uit elkaar.**
 * - *Omschrijving* = wat er moet gebeuren (Markdown, bewerkbaar).
 * - *Acceptatiecriteria* = wanneer het klaar is (Markdown, bewerkbaar).
 * - *Tijdlijn* = wat er gebeurd is: opmerkingen van mens en agent, nieuwste
 *   bovenaan. Hier — en nergens anders — hoort verslag.
 *
 * Zonder die scheiding schrijft de agent zijn bevindingen zowel in een
 * opmerking als onderaan de omschrijving, en is na twee runs niet meer terug
 * te vinden wat er oorspronkelijk gevraagd werd.
 *
 * Het werk van de agent eindigt dus op het ticket, niet in de chat: de thread
 * achter een agent-run is verborgen (Thread.source = "board").
 */
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { boardApi } from "@/lib/boards";
import { chatApi } from "@/lib/chat";
import type { BoardDto, ChatEvent, TicketCommentDto, TicketDto } from "@/lib/types";
import { Badge, Button, Card, Input, Label, Select, TextArea } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { Bot, ExternalLink, Pencil, Trash2, X } from "lucide-react";

const PRIORITIES = ["low", "normal", "high", "urgent"] as const;

const AGENT_TONE = {
  idle: "neutral", queued: "yellow", running: "yellow", done: "green", failed: "red",
} as const;

/**
 * Markdown-veld: standaard gerenderd, met één klik naar een editor. Bewust
 * niet "altijd een textarea" — een omschrijving met kopjes en lijstjes is
 * onleesbaar als ruwe tekst — en bewust niet "alleen lezen": de gebruiker
 * moet de opdracht kunnen bijstellen.
 */
function MarkdownField({
  label, value, placeholder, rows = 8, onSave,
}: {
  label: string;
  value: string | null;
  placeholder: string;
  rows?: number;
  onSave: (next: string) => Promise<void> | void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value || "");
  const [saving, setSaving] = useState(false);

  // Een agent-run kan dit veld tijdens het kijken wijzigen; die update mag
  // alleen doorkomen als de gebruiker niet zelf aan het typen is.
  useEffect(() => {
    if (!editing) setDraft(value || "");
  }, [value, editing]);

  async function commit() {
    setSaving(true);
    try {
      await onSave(draft);
      setEditing(false);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <Label>{label}</Label>
        {!editing && (
          <button
            onClick={() => setEditing(true)}
            className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
          >
            <Pencil size={11} /> Bewerken
          </button>
        )}
      </div>
      {editing ? (
        <div className="space-y-2">
          <TextArea
            rows={rows}
            className="font-mono text-xs"
            value={draft}
            placeholder={placeholder}
            onChange={(e) => setDraft(e.target.value)}
            autoFocus
          />
          <div className="flex gap-2">
            <Button className="text-xs" onClick={commit} disabled={saving}>
              {saving ? "Opslaan…" : "Opslaan"}
            </Button>
            <Button
              variant="ghost"
              className="text-xs"
              onClick={() => {
                setDraft(value || "");
                setEditing(false);
              }}
            >
              Annuleren
            </Button>
          </div>
        </div>
      ) : value?.trim() ? (
        <div className="markdown-body rounded-md border border-border p-3 text-sm">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{value}</ReactMarkdown>
        </div>
      ) : (
        <button
          onClick={() => setEditing(true)}
          className="w-full rounded-md border border-dashed border-border p-3 text-left text-xs text-muted-foreground hover:text-foreground"
        >
          {placeholder}
        </button>
      )}
    </div>
  );
}

export function TicketDrawer({
  board, ticketId, onClose, onChanged,
}: {
  board: BoardDto;
  ticketId: number;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [ticket, setTicket] = useState<TicketDto | null>(null);
  const [comments, setComments] = useState<TicketCommentDto[]>([]);
  const [draft, setDraft] = useState("");
  const [instruction, setInstruction] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Live meelezen met de agent-run van dit ticket.
  const [runSteps, setRunSteps] = useState<ChatEvent[]>([]);
  const [runStatus, setRunStatus] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  async function load() {
    const t = await boardApi.ticket(board.id, ticketId);
    setTicket(t);
    // Nieuwste bovenaan: bij een ticket waar de agent een paar keer overheen is
    // gegaan wil je het laatste verslag zien zonder eerst door de historie te
    // scrollen. De backend levert oplopend (chronologisch) aan.
    setComments([...(t.comments || [])].reverse());
  }

  useEffect(() => {
    load().catch(() => setError("Ticket laden mislukt"));
    return () => abortRef.current?.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticketId]);

  // Loopt er al een run (bv. gestart door een schedule)? Dan meteen aanhaken.
  useEffect(() => {
    if (!ticket?.agent_run_id || ticket.agent_state !== "running") return;
    attachToRun(ticket.agent_run_id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticket?.agent_run_id, ticket?.agent_state]);

  function attachToRun(runId: string) {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setRunSteps([]);
    setRunStatus("running");
    chatApi
      .streamBackgroundRun(
        runId,
        (ev) => {
          // Alleen de voortgangsstappen: het eindantwoord van de agent komt
          // als opmerking in de tijdlijn terecht, dus dat hier óók tonen zou
          // hetzelfde verslag twee keer op het scherm zetten.
          if (ev.kind === "thinking" || ev.kind === "tool") setRunSteps((prev) => [...prev, ev]);
          if (ev.kind === "run_status") {
            setRunStatus(ev.status);
            // De afloop-hook zet de opmerking en de kolom; even opnieuw laden.
            setTimeout(() => {
              load().catch(() => {});
              onChanged();
            }, 700);
          }
        },
        controller.signal,
      )
      .catch(() => {});
  }

  async function save(patch: Record<string, any>) {
    if (!ticket) return;
    setError(null);
    try {
      const updated = await boardApi.updateTicket(board.id, ticket.id, patch);
      setTicket({ ...updated, comments: ticket.comments });
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Opslaan mislukt");
    }
  }

  async function postComment() {
    if (!ticket || !draft.trim()) return;
    await boardApi.addComment(board.id, ticket.id, draft.trim());
    setDraft("");
    await load();
  }

  async function runAgent() {
    if (!ticket) return;
    setBusy(true);
    setError(null);
    try {
      const started = await boardApi.runAgent(board.id, ticket.id, instruction.trim() || undefined);
      setInstruction("");
      attachToRun(started.run_id);
      await load();
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Agent starten mislukt");
    } finally {
      setBusy(false);
    }
  }

  async function removeTicket() {
    if (!ticket || !confirm(`Ticket ${ticket.key} verwijderen?`)) return;
    await boardApi.removeTicket(board.id, ticket.id);
    onChanged();
    onClose();
  }

  if (!ticket) {
    return (
      <aside className="flex w-[30rem] shrink-0 flex-col border-l border-border bg-card p-4">
        <p className="text-sm text-muted-foreground">Laden…</p>
      </aside>
    );
  }

  return (
    <aside className="flex w-[30rem] shrink-0 flex-col overflow-y-auto border-l border-border bg-card">
      <div className="sticky top-0 z-10 flex items-center justify-between gap-2 border-b border-border bg-card px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="font-mono text-xs text-muted-foreground">{ticket.key}</span>
          <Badge tone={AGENT_TONE[ticket.agent_state] || "neutral"}>agent: {ticket.agent_state}</Badge>
          {ticket.dirty && <Badge tone="yellow">niet gesynct</Badge>}
        </div>
        <div className="flex items-center gap-1">
          <button onClick={removeTicket} className="text-muted-foreground hover:text-destructive" title="Verwijderen">
            <Trash2 size={15} />
          </button>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground">
            <X size={16} />
          </button>
        </div>
      </div>

      <div className="space-y-4 p-4">
        {error && <p className="text-sm text-destructive">{error}</p>}

        <div>
          <Label>Titel</Label>
          <Input
            value={ticket.title}
            onChange={(e) => setTicket({ ...ticket, title: e.target.value })}
            onBlur={(e) => e.target.value.trim() && save({ title: e.target.value })}
          />
        </div>

        <div className="grid grid-cols-3 gap-2">
          <div>
            <Label>Kolom</Label>
            <Select value={ticket.status} onChange={(e) => save({ status: e.target.value })}>
              {board.columns.map((c) => (
                <option key={c.key} value={c.key}>
                  {c.name}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label>Prioriteit</Label>
            <Select value={ticket.priority} onChange={(e) => save({ priority: e.target.value })}>
              {PRIORITIES.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label>Toegewezen</Label>
            <Input
              value={ticket.assignee || ""}
              onChange={(e) => setTicket({ ...ticket, assignee: e.target.value })}
              onBlur={(e) => save({ assignee: e.target.value || null })}
            />
          </div>
        </div>

        <div>
          <Label>Labels (komma-gescheiden)</Label>
          <Input
            value={(ticket.labels || []).join(", ")}
            onChange={(e) => setTicket({ ...ticket, labels: e.target.value.split(",").map((s) => s.trim()) })}
            onBlur={(e) =>
              save({ labels: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })
            }
          />
        </div>

        <MarkdownField
          label="Omschrijving — de opdracht"
          value={ticket.description}
          placeholder="Beschrijf wat er moet gebeuren (Markdown). Voortgang en bevindingen horen in de tijdlijn, niet hier."
          onSave={(next) => save({ description: next })}
        />

        <MarkdownField
          label="Acceptatiecriteria — wanneer is het klaar?"
          value={ticket.acceptance_criteria}
          rows={6}
          placeholder="Toetsbare criteria, bv. een lijstje met '- [ ] …'. De agent meet zijn werk hieraan af."
          onSave={(next) => save({ acceptance_criteria: next })}
        />

        {ticket.external_url && (
          <a
            href={ticket.external_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
          >
            <ExternalLink size={12} /> {ticket.external_key} in{" "}
            {ticket.external_provider === "jira" ? "Jira" : "Azure DevOps"}
          </a>
        )}

        {/* ── agent ───────────────────────────────────────────────────── */}
        <Card className="p-3">
          <div className="mb-2 flex items-center gap-2 text-sm font-semibold">
            <Bot size={15} /> Laat de AI dit oppakken
          </div>
          {!board.lab_id && (
            <p className="text-xs text-destructive">
              Dit board heeft geen gekoppeld lab — koppel er een lab aan in de board-instellingen.
            </p>
          )}
          <TextArea
            rows={2}
            className="text-xs"
            placeholder="Extra instructie voor deze run (optioneel)"
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
          />
          <div className="mt-2 flex items-center gap-2">
            <Button
              variant="secondary"
              className="text-xs"
              disabled={busy || !board.lab_id || ticket.agent_state === "running"}
              onClick={runAgent}
            >
              {ticket.agent_state === "running" ? "Agent werkt…" : "Agent starten"}
            </Button>
            <span className="text-[11px] text-muted-foreground">
              Het verslag verschijnt hieronder in de tijdlijn.
            </span>
          </div>
          {ticket.agent_last_error && (
            <p className="mt-2 text-xs text-destructive">{ticket.agent_last_error}</p>
          )}
          {runStatus && (
            <div className="mt-2 space-y-1">
              <Badge tone={runStatus === "completed" ? "green" : runStatus === "running" ? "yellow" : "red"}>
                {runStatus}
              </Badge>
              {runSteps.length > 0 && (
                <div className="max-h-32 space-y-0.5 overflow-y-auto rounded border border-border p-2 text-[11px] text-muted-foreground">
                  {runSteps.map((s, i) => (
                    <div key={i}>{s.kind === "tool" ? `🔧 ${(s as any).name}` : (s as any).text}</div>
                  ))}
                </div>
              )}
            </div>
          )}
        </Card>

        {/* ── tijdlijn ────────────────────────────────────────────────── */}
        <div>
          <div className="mb-1 flex items-center justify-between">
            <Label>Tijdlijn</Label>
            <span className="text-[11px] text-muted-foreground">nieuwste bovenaan</span>
          </div>
          <div className="mb-3 flex gap-2">
            <TextArea
              rows={2}
              className="text-xs"
              placeholder="Opmerking toevoegen… (Markdown)"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
            />
            <Button variant="secondary" className="self-end text-xs" onClick={postComment} disabled={!draft.trim()}>
              Plaatsen
            </Button>
          </div>
          <div className="space-y-2">
            {comments.length === 0 && <p className="text-xs text-muted-foreground">Nog niets.</p>}
            {comments.map((c) => (
              <div
                key={c.id}
                className={`rounded-md border p-2 text-xs ${
                  c.kind === "activity"
                    ? "border-dashed border-border text-muted-foreground"
                    : "border-border bg-secondary/40"
                }`}
              >
                <div className="mb-1 flex items-center gap-2 text-[11px] text-muted-foreground">
                  <span className="font-semibold">{c.author}</span>
                  <span>{new Date(c.created_at).toLocaleString()}</span>
                  {c.external_id && <Badge tone="violet">extern</Badge>}
                </div>
                {c.kind === "comment" ? (
                  <div className="markdown-body">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{c.body}</ReactMarkdown>
                  </div>
                ) : (
                  c.body
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </aside>
  );
}
