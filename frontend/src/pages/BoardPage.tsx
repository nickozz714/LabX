/**
 * pages/BoardPage.tsx — het kanban-bord zelf.
 *
 * Kolommen naast elkaar, tickets versleepbaar (HTML5 drag & drop — geen extra
 * dependency voor wat neerkomt op "kaart naar kolom"), en rechts een
 * detailpaneel. Het bord ververst zichzelf zolang er een agent op een ticket
 * werkt, zodat je een run die door een schedule is gestart ziet binnenkomen
 * zonder de pagina te herladen.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { boardApi } from "@/lib/boards";
import type { BoardDto, TicketDto } from "@/lib/types";
import { Badge, Button, Card, Input, Label, Modal, Select, TextArea } from "@/components/ui";
import { TicketDrawer } from "@/components/TicketDrawer";
import { BoardSettings } from "@/components/BoardSettings";
import { ApiError } from "@/lib/api";
import { ArrowLeft, Bot, RefreshCw, Settings2 } from "lucide-react";

const PRIORITY_TONE = {
  urgent: "red", high: "yellow", normal: "neutral", low: "neutral",
} as const;

export function BoardPage() {
  const { boardId } = useParams();
  const id = Number(boardId);
  const navigate = useNavigate();

  const [board, setBoard] = useState<BoardDto | null>(null);
  const [tickets, setTickets] = useState<TicketDto[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [creatingIn, setCreatingIn] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const dragged = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    const [b, t] = await Promise.all([boardApi.get(id), boardApi.tickets(id)]);
    setBoard(b);
    setTickets(t);
  }, [id]);

  useEffect(() => {
    refresh().catch(() => setNotice("Board laden mislukt"));
  }, [refresh]);

  // Zolang de agent ergens aan werkt: blijven verversen. Stopt vanzelf als
  // er niets meer loopt — geen eeuwige poll op een stil bord.
  useEffect(() => {
    if (!tickets.some((t) => t.agent_state === "running")) return;
    const timer = setInterval(() => {
      refresh().catch(() => {});
    }, 5000);
    return () => clearInterval(timer);
  }, [tickets, refresh]);

  async function onDrop(columnKey: string) {
    const ticketId = dragged.current;
    dragged.current = null;
    if (ticketId == null) return;
    const ticket = tickets.find((t) => t.id === ticketId);
    if (!ticket || ticket.status === columnKey) return;
    // Optimistisch verplaatsen: de kaart springt meteen, de server bevestigt.
    setTickets((prev) => prev.map((t) => (t.id === ticketId ? { ...t, status: columnKey } : t)));
    try {
      await boardApi.moveTicket(id, ticketId, columnKey);
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Verplaatsen mislukt");
    }
    refresh().catch(() => {});
  }

  async function pickUp() {
    if (!board) return;
    setBusy(true);
    setNotice(null);
    try {
      const result = await boardApi.pickUp(id, { max_tickets: 1 });
      setNotice(
        result.count === 0
          ? `Geen tickets klaar in de kolom '${board.agent_column || "-"}'.`
          : `Agent gestart op ${result.started.map((s) => s.ticket_key).join(", ")}.`,
      );
      refresh().catch(() => {});
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Oppakken mislukt");
    } finally {
      setBusy(false);
    }
  }

  async function sync() {
    setBusy(true);
    setNotice(null);
    try {
      const stats = await boardApi.sync(id);
      setNotice(
        `Sync klaar: ${stats.created_local} nieuw, ${stats.updated_local} bijgewerkt, ` +
          `${stats.pushed + stats.created_external} teruggeschreven, ` +
          `${stats.comments_pulled} opmerking(en) opgehaald` +
          (stats.skipped_dirty ? `, ${stats.skipped_dirty} overgeslagen (nog niet gepusht)` : "") +
          (stats.errors.length ? ` — fouten: ${stats.errors.join("; ")}` : ""),
      );
      refresh().catch(() => {});
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Synchronisatie mislukt");
    } finally {
      setBusy(false);
    }
  }

  if (!board) {
    return <div className="p-6 text-sm text-muted-foreground">Laden…</div>;
  }

  return (
    <div className="flex h-full">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3">
          <button onClick={() => navigate("/boards")} className="text-muted-foreground hover:text-foreground">
            <ArrowLeft size={16} />
          </button>
          <h1 className="text-lg font-bold">{board.name}</h1>
          {board.lab_id ? (
            <Badge tone={board.lab_status === "running" ? "green" : "yellow"}>
              lab: {board.lab_name} ({board.lab_status})
            </Badge>
          ) : (
            <Badge tone="red">geen lab</Badge>
          )}
          {board.provider !== "local" && (
            <Badge tone="violet">
              {board.provider === "jira" ? "Jira" : "Azure DevOps"} ·{" "}
              {board.sync_direction === "two_way" ? "two-way" : "alleen lezen"}
            </Badge>
          )}

          <div className="ml-auto flex items-center gap-2">
            <Button variant="secondary" className="text-xs" onClick={pickUp} disabled={busy || !board.lab_id}>
              <Bot size={13} /> Pak werk op
            </Button>
            {board.provider !== "local" && (
              <Button variant="secondary" className="text-xs" onClick={sync} disabled={busy}>
                <RefreshCw size={13} className={busy ? "animate-spin" : ""} /> Sync
              </Button>
            )}
            <Button variant="secondary" className="text-xs" onClick={() => setSettingsOpen(true)}>
              <Settings2 size={13} /> Instellingen
            </Button>
          </div>
        </div>

        {notice && (
          <div className="border-b border-border bg-secondary/40 px-4 py-2 text-xs">
            {notice}
            <button className="ml-2 text-muted-foreground" onClick={() => setNotice(null)}>
              sluiten
            </button>
          </div>
        )}
        {board.last_sync_error && (
          <div className="border-b border-border bg-destructive/10 px-4 py-2 text-xs text-destructive">
            Laatste sync mislukte: {board.last_sync_error}
          </div>
        )}

        <div className="flex flex-1 gap-3 overflow-x-auto p-4">
          {board.columns.map((col) => {
            const cards = tickets.filter((t) => t.status === col.key);
            const overLimit = col.wip_limit != null && cards.length > col.wip_limit;
            return (
              <div
                key={col.key}
                className="flex w-72 shrink-0 flex-col rounded-lg border border-border bg-secondary/30"
                onDragOver={(e) => e.preventDefault()}
                onDrop={() => onDrop(col.key)}
              >
                <div className="flex items-center justify-between border-b border-border px-3 py-2">
                  <span className="text-sm font-semibold">
                    {col.name}
                    {board.agent_column === col.key && (
                      <Bot size={12} className="ml-1 inline text-muted-foreground" />
                    )}
                  </span>
                  <span className={`text-xs ${overLimit ? "text-destructive" : "text-muted-foreground"}`}>
                    {cards.length}
                    {col.wip_limit != null ? `/${col.wip_limit}` : ""}
                  </span>
                </div>
                <div className="flex-1 space-y-2 overflow-y-auto p-2">
                  {cards.map((t) => (
                    <Card
                      key={t.id}
                      draggable
                      onDragStart={() => (dragged.current = t.id)}
                      onClick={() => setSelected(t.id)}
                      className={`cursor-pointer p-2 transition hover:border-primary/50 ${
                        selected === t.id ? "border-primary" : ""
                      }`}
                    >
                      <div className="flex items-center justify-between gap-1">
                        <span className="font-mono text-[11px] text-muted-foreground">{t.key}</span>
                        <div className="flex items-center gap-1">
                          {t.agent_state === "running" && <Badge tone="yellow">agent</Badge>}
                          {t.agent_state === "failed" && <Badge tone="red">mislukt</Badge>}
                          {t.priority !== "normal" && (
                            <Badge tone={PRIORITY_TONE[t.priority]}>{t.priority}</Badge>
                          )}
                        </div>
                      </div>
                      <div className="mt-1 text-sm">{t.title}</div>
                      {(t.labels?.length > 0 || t.external_key) && (
                        <div className="mt-1 flex flex-wrap items-center gap-1 text-[10px] text-muted-foreground">
                          {t.external_key && <span className="font-mono">{t.external_key}</span>}
                          {t.labels?.map((l) => (
                            <span key={l} className="rounded bg-secondary px-1">
                              {l}
                            </span>
                          ))}
                        </div>
                      )}
                    </Card>
                  ))}
                  <button
                    className="w-full rounded-md border border-dashed border-border py-1 text-xs text-muted-foreground hover:text-foreground"
                    onClick={() => setCreatingIn(col.key)}
                  >
                    + Ticket
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {selected != null && (
        <TicketDrawer
          board={board}
          ticketId={selected}
          onClose={() => setSelected(null)}
          onChanged={() => refresh().catch(() => {})}
        />
      )}

      {creatingIn && (
        <NewTicketModal
          board={board}
          column={creatingIn}
          onClose={() => setCreatingIn(null)}
          onCreated={() => {
            setCreatingIn(null);
            refresh().catch(() => {});
          }}
        />
      )}

      {settingsOpen && (
        <BoardSettings
          board={board}
          onClose={() => setSettingsOpen(false)}
          onSaved={() => {
            setSettingsOpen(false);
            refresh().catch(() => {});
          }}
          onDeleted={() => navigate("/boards")}
        />
      )}
    </div>
  );
}

function NewTicketModal({
  board, column, onClose, onCreated,
}: {
  board: BoardDto;
  column: string;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [acceptance, setAcceptance] = useState("");
  const [priority, setPriority] = useState("normal");
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    try {
      await boardApi.createTicket(board.id, {
        title, description, acceptance_criteria: acceptance.trim() || null,
        status: column, priority,
      });
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Aanmaken mislukt");
    }
  }

  return (
    <Modal open onClose={onClose} title={`Nieuw ticket — ${board.columns.find((c) => c.key === column)?.name}`}>
      <div className="space-y-3">
        <div>
          <Label>Titel</Label>
          <Input value={title} onChange={(e) => setTitle(e.target.value)} autoFocus />
        </div>
        <div>
          <Label>Omschrijving — de opdracht (Markdown)</Label>
          <TextArea
            rows={6}
            className="font-mono text-xs"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Wat moet er gebeuren?"
          />
        </div>
        <div>
          <Label>Acceptatiecriteria (Markdown, optioneel)</Label>
          <TextArea
            rows={4}
            className="font-mono text-xs"
            value={acceptance}
            onChange={(e) => setAcceptance(e.target.value)}
            placeholder={"- [ ] …\n- [ ] …"}
          />
          <p className="mt-1 text-xs text-muted-foreground">
            De agent toetst zijn werk hieraan en meldt per criterium of eraan voldaan is.
          </p>
        </div>
        <div>
          <Label>Prioriteit</Label>
          <Select value={priority} onChange={(e) => setPriority(e.target.value)}>
            {["low", "normal", "high", "urgent"].map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </Select>
        </div>
        {error && <p className="text-sm text-destructive">{error}</p>}
        <Button className="w-full" onClick={submit} disabled={!title.trim()}>
          Aanmaken
        </Button>
      </div>
    </Modal>
  );
}
