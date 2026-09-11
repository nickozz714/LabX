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
import type { BoardDto, PlanDto, TicketDto } from "@/lib/types";
import { Badge, Button, Card, Input, Label, Modal, Select, TextArea, Toggle } from "@/components/ui";
import { TicketDrawer } from "@/components/TicketDrawer";
import { BoardSettings } from "@/components/BoardSettings";
import { ApiError } from "@/lib/api";
import { ArrowLeft, Bot, ListOrdered, Pause, Play, RefreshCw, Settings2, X } from "lucide-react";
import { BijlageKnop, BijlageLijst } from "@/components/Bijlagen";
import type { Bijlage } from "@/lib/labs";

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
  // Selectiemodus: aanvinken welke tickets in een planning moeten, in de
  // volgorde waarin je ze aanvinkt — dat is meestal precies de bedoelde
  // volgorde, en anders sleep je ze in het planningsvenster nog om.
  const [selectie, setSelectie] = useState<number[]>([]);
  const [planOpen, setPlanOpen] = useState(false);
  const [plans, setPlans] = useState<PlanDto[]>([]);

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

  /**
   * Loslaten op een kolom, of tussen twee kaarten in. `voorTicketId` is de
   * kaart waar hij bovenop komt; die bepaalt de nieuwe positie — en positie IS
   * de prioriteit: wie bovenaan staat, is als eerste aan de beurt.
   */
  async function onDrop(columnKey: string, voorTicketId?: number) {
    const ticketId = dragged.current;
    dragged.current = null;
    if (ticketId == null || ticketId === voorTicketId) return;
    const ticket = tickets.find((t) => t.id === ticketId);
    if (!ticket) return;

    const kolomkaarten = tickets
      .filter((t) => t.status === columnKey && t.id !== ticketId)
      .sort((a, b) => a.position - b.position);
    let positie: number | undefined;
    if (voorTicketId != null) {
      const index = kolomkaarten.findIndex((t) => t.id === voorTicketId);
      const doel = kolomkaarten[index];
      const ervoor = kolomkaarten[index - 1];
      // Precies tussen de buren in — met floats hoeft de rest van de kolom
      // niet hernummerd te worden.
      positie = ervoor ? (ervoor.position + doel.position) / 2 : doel.position - 100;
    } else if (ticket.status === columnKey) {
      return; // op de kolom zelf laten vallen terwijl hij er al in staat
    }

    setTickets((prev) =>
      prev.map((t) =>
        t.id === ticketId
          ? { ...t, status: columnKey, position: positie ?? t.position }
          : t,
      ),
    );
    try {
      await boardApi.moveTicket(id, ticketId, columnKey, positie);
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Verplaatsen mislukt");
    }
    refresh().catch(() => {});
  }

  const laadPlans = useCallback(() => {
    boardApi.plans(id).then(setPlans).catch(() => {});
  }, [id]);

  useEffect(() => {
    laadPlans();
    const t = setInterval(laadPlans, 5000);
    return () => clearInterval(t);
  }, [laadPlans]);

  async function pakKolomOp() {
    if (!board) return;
    setBusy(true);
    setNotice(null);
    try {
      // De hele kolom als één planning: hij werkt hem van boven naar beneden
      // af, in de volgorde die op het bord staat.
      const plan = await boardApi.planFromColumn(id, {});
      setNotice(`Planning '${plan.name}' gestart met ${plan.total} ticket(s).`);
      laadPlans();
      refresh().catch(() => {});
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Oppakken mislukt");
    } finally {
      setBusy(false);
    }
  }

  function toggleSelectie(ticketId: number) {
    setSelectie((cur) =>
      cur.includes(ticketId) ? cur.filter((x) => x !== ticketId) : [...cur, ticketId],
    );
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
          (stats.reconciled ? `, ${stats.reconciled} bijgewerkt buiten de query` : "") +
          (stats.skipped_dirty ? `, ${stats.skipped_dirty} overgeslagen (nog niet gepusht)` : "") +
          // De mapping kan zichzelf hebben gerepareerd; dan verspringen er
          // tickets en hoort erbij te staan waarom.
          (stats.mapping?.length ? ` — statusmapping bijgewerkt: ${stats.mapping.join("; ")}` : "") +
          // Zonder deze regel lijkt het of de sync de status negeert: een niet
          // gemapte status komt in de eerste kolom terecht.
          (stats.unmapped_states?.length
            ? ` — niet gekoppelde status(sen): ${stats.unmapped_states.join(", ")}; ` +
              `koppel ze in Instellingen → Statusmapping, anders belanden die tickets in de eerste kolom`
            : "") +
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
            {selectie.length > 0 ? (
              <>
                <span className="text-xs text-muted-foreground">{selectie.length} geselecteerd</span>
                <Button className="text-xs" onClick={() => setPlanOpen(true)} disabled={!board.lab_id}>
                  <ListOrdered size={13} /> Inplannen
                </Button>
                <Button variant="ghost" className="text-xs" onClick={() => setSelectie([])}>
                  Selectie wissen
                </Button>
              </>
            ) : (
              <Button variant="secondary" className="text-xs" onClick={pakKolomOp}
                      disabled={busy || !board.lab_id}>
                <Bot size={13} /> Pak hele kolom op
              </Button>
            )}
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

        <PlanBalk
          boardId={id}
          plans={plans}
          onChanged={() => {
            laadPlans();
            refresh().catch(() => {});
          }}
        />

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
                      onDragOver={(e) => e.preventDefault()}
                      // Loslaten óp een kaart betekent "hierboven invoegen" —
                      // zo bepaal je met slepen de volgorde binnen de kolom.
                      onDrop={(e) => {
                        e.stopPropagation();
                        onDrop(col.key, t.id);
                      }}
                      onClick={() => setSelected(t.id)}
                      className={`cursor-pointer p-2 transition hover:border-primary/50 ${
                        selected === t.id ? "border-primary" : ""
                      } ${selectie.includes(t.id) ? "ring-1 ring-primary" : ""}`}
                    >
                      <div className="flex items-center justify-between gap-1">
                        <span className="flex items-center gap-1">
                          <input
                            type="checkbox"
                            checked={selectie.includes(t.id)}
                            onClick={(e) => e.stopPropagation()}
                            onChange={() => toggleSelectie(t.id)}
                            title="Meenemen in een planning"
                          />
                          <span className="font-mono text-[11px] text-muted-foreground">{t.key}</span>
                        </span>
                        <div className="flex items-center gap-1">
                          {t.agent_state === "running" && <Badge tone="yellow">agent</Badge>}
                          {t.agent_state === "failed" && <Badge tone="red">mislukt</Badge>}
                          {t.priority !== "normal" && (
                            <Badge tone={PRIORITY_TONE[t.priority]}>{t.priority}</Badge>
                          )}
                        </div>
                      </div>
                      <div className="mt-1 text-sm">{t.title}</div>
                      {t.depends_on?.length > 0 && (
                        <div className="mt-1 text-[10px] text-muted-foreground">
                          wacht op {t.depends_on.join(", ")}
                        </div>
                      )}
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

      {planOpen && (
        <PlanVenster
          boardId={id}
          labId={board?.lab_id ?? null}
          tickets={selectie
            .map((tid) => tickets.find((t) => t.id === tid))
            .filter((t): t is TicketDto => Boolean(t))}
          onClose={() => setPlanOpen(false)}
          onCreated={(plan) => {
            setPlanOpen(false);
            setSelectie([]);
            setNotice(
              plan.state === "scheduled"
                ? `Planning '${plan.name}' staat klaar voor ${new Date(plan.start_at || "").toLocaleString()}.`
                : `Planning '${plan.name}' gestart met ${plan.total} ticket(s).`,
            );
            laadPlans();
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

const PLAN_TOON: Record<string, "green" | "red" | "yellow" | "neutral" | "violet"> = {
  running: "yellow", paused: "red", scheduled: "violet", done: "green",
  cancelled: "neutral", draft: "neutral",
};

/** De lopende en wachtende planningen van dit bord, met de knoppen om ze te sturen. */
function PlanBalk({ boardId, plans, onChanged }: {
  boardId: number;
  plans: PlanDto[];
  onChanged: () => void;
}) {
  const [open, setOpen] = useState<number | null>(null);
  const actief = plans.filter((p) => ["running", "paused", "scheduled", "draft"].includes(p.state));
  if (actief.length === 0) return null;

  async function actie(fn: Promise<unknown>) {
    try {
      await fn;
    } finally {
      onChanged();
    }
  }

  return (
    <div className="border-b border-border bg-secondary/20 px-4 py-2">
      <div className="flex flex-wrap gap-2">
        {actief.map((p) => (
          <div key={p.id} className="rounded-md border border-border bg-background px-2 py-1 text-xs">
            <div className="flex items-center gap-2">
              <Badge tone={PLAN_TOON[p.state] || "neutral"}>{p.state}</Badge>
              <button className="font-medium hover:underline"
                      onClick={() => setOpen(open === p.id ? null : p.id)}>
                {p.name}
              </button>
              <span className="text-muted-foreground">
                {(p.counts.done || 0) + (p.counts.failed || 0)}/{p.total}
                {p.start_at && p.state === "scheduled" && ` · ${new Date(p.start_at).toLocaleString()}`}
              </span>
              {p.state === "running" && (
                <button title="Pauzeren" onClick={() => actie(boardApi.pausePlan(boardId, p.id))}>
                  <Pause size={12} />
                </button>
              )}
              {(p.state === "paused" || p.state === "draft" || p.state === "scheduled") && (
                <button title="Starten / hervatten"
                        onClick={() => actie(boardApi.resumePlan(boardId, p.id))}>
                  <Play size={12} />
                </button>
              )}
              <button title="Afbreken" onClick={() => actie(boardApi.cancelPlan(boardId, p.id))}>
                <X size={12} />
              </button>
            </div>
            {p.note && <div className="mt-0.5 max-w-md text-[11px] text-destructive">{p.note}</div>}
            {open === p.id && <PlanRegels boardId={boardId} planId={p.id} onChanged={onChanged} />}
          </div>
        ))}
      </div>
    </div>
  );
}

const ITEM_TOON: Record<string, "green" | "red" | "yellow" | "neutral" | "violet"> = {
  running: "yellow", failed: "red", done: "green", blocked: "red",
  waiting: "neutral", skipped: "neutral",
};

function PlanRegels({ boardId, planId, onChanged }: {
  boardId: number; planId: number; onChanged: () => void;
}) {
  const [plan, setPlan] = useState<PlanDto | null>(null);
  useEffect(() => {
    boardApi.plan(boardId, planId).then(setPlan).catch(() => {});
  }, [boardId, planId]);
  if (!plan?.items) return null;
  return (
    <div className="mt-1 space-y-0.5 border-t border-border pt-1">
      {plan.items.map((it) => (
        <div key={it.id} className="flex flex-wrap items-center gap-2">
          <Badge tone={it.resume_at ? "violet" : (ITEM_TOON[it.state] || "neutral")}>
            {it.resume_at ? "wacht" : it.state}
          </Badge>
          <span className="font-mono">{it.ticket_key}</span>
          <span className="max-w-[16rem] truncate text-muted-foreground">{it.ticket_title}</span>
          {it.resume_at && (
            <span className="text-[11px] text-muted-foreground"
                  title="Dit ticket wacht op iets dat tijd kost; de planning gaat ondertussen verder">
              tot {new Date(it.resume_at).toLocaleTimeString(undefined,
                    { hour: "2-digit", minute: "2-digit" })}
            </span>
          )}
          {it.claims?.length > 0 && (
            <span className="rounded bg-secondary px-1 text-[11px] text-muted-foreground"
                  title={`Houdt vast: ${it.claims.join(", ")} — andere tickets die hieraan komen wachten`}>
              🔒 {it.claims.length}
            </span>
          )}
          {["waiting", "blocked"].includes(it.state) && (
            <button
              title="Uit de planning halen"
              onClick={() =>
                boardApi.removePlanItem(boardId, planId, it.id).then((p) => {
                  setPlan(p);
                  onChanged();
                })
              }
            >
              <X size={11} />
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

/**
 * Een selectie inplannen: volgorde bepalen, en kiezen of hij meteen begint of
 * op een tijdstip. De volgorde in deze lijst is wat de agent aanhoudt — niet
 * de volgorde op het bord.
 */
function PlanVenster({ boardId, labId, tickets, onClose, onCreated }: {
  boardId: number;
  /** Het lab van dit bord — nodig om bijlagen te kunnen neerzetten. */
  labId: string | null;
  tickets: TicketDto[];
  onClose: () => void;
  onCreated: (plan: PlanDto) => void;
}) {
  const [rij, setRij] = useState<TicketDto[]>(tickets);
  const [naam, setNaam] = useState("");
  const [wanneer, setWanneer] = useState<"nu" | "later" | "klaarzetten">("nu");
  const [tijdstip, setTijdstip] = useState("");
  const [instructie, setInstructie] = useState("");
  // Leeg = zoveel als er werkers vrij zijn. Dat is de standaard omdat het lab
  // dan de enige knop is die je hoeft te begrijpen.
  const [parallel, setParallel] = useState<string>("");
  const [apart, setApart] = useState(false);
  // Bijlagen bij de PLANNING: ze gelden voor elk ticket erin. Handig bij één
  // specificatie die voor de hele reeks geldt.
  const [bijlagen, setBijlagen] = useState<Bijlage[]>([]);
  const [busy, setBusy] = useState(false);
  const [fout, setFout] = useState<string | null>(null);

  function verplaats(index: number, richting: -1 | 1) {
    const doel = index + richting;
    if (doel < 0 || doel >= rij.length) return;
    const kopie = [...rij];
    [kopie[index], kopie[doel]] = [kopie[doel], kopie[index]];
    setRij(kopie);
  }

  async function opslaan() {
    setBusy(true);
    setFout(null);
    try {
      const plan = await boardApi.createPlan(boardId, {
        name: naam.trim() || undefined,
        ticket_ids: rij.map((t) => t.id),
        // datetime-local levert lokale tijd zonder zone; als ISO doorgeven is
        // hier goed genoeg omdat server en gebruiker dezelfde zone delen.
        start_at: wanneer === "later" && tijdstip ? new Date(tijdstip).toISOString() : undefined,
        start_now: wanneer === "nu",
        instruction: instructie.trim() || undefined,
        attachments: bijlagen,
        max_parallel: parallel ? Number(parallel) : undefined,
        workspace_mode: apart ? "apart" : "gedeeld",
      });
      onCreated(plan);
    } catch (err) {
      setFout(err instanceof ApiError ? err.message : "Inplannen mislukt");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open onClose={onClose} title={`${rij.length} ticket(s) inplannen`} wide>
      <div className="space-y-3">
        <div>
          <Label>Volgorde</Label>
          <p className="mb-1 text-xs text-muted-foreground">
            Van boven naar beneden. De agent doet er één tegelijk; wacht een ticket op een ander
            ticket dat nog niet klaar is, dan pauzeert de planning daar.
          </p>
          <div className="divide-y divide-border rounded-md border border-border">
            {rij.map((t, i) => (
              <div key={t.id} className="flex items-center gap-2 px-2 py-1 text-sm">
                <span className="w-5 text-right text-xs text-muted-foreground">{i + 1}</span>
                <span className="font-mono text-xs">{t.key}</span>
                <span className="flex-1 truncate">{t.title}</span>
                {t.depends_on?.length > 0 && (
                  <span className="text-[10px] text-muted-foreground">wacht op {t.depends_on.join(", ")}</span>
                )}
                <button className="px-1 text-xs" onClick={() => verplaats(i, -1)} disabled={i === 0}>↑</button>
                <button className="px-1 text-xs" onClick={() => verplaats(i, 1)}
                        disabled={i === rij.length - 1}>↓</button>
                <button className="px-1 text-xs"
                        onClick={() => setRij(rij.filter((x) => x.id !== t.id))}>×</button>
              </div>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label>Naam</Label>
            <Input value={naam} onChange={(e) => setNaam(e.target.value)}
                   placeholder="bv. Silver-herstel TST" />
          </div>
          <div>
            <Label>Starten</Label>
            <Select value={wanneer} onChange={(e) => setWanneer(e.target.value as typeof wanneer)}>
              <option value="nu">Meteen</option>
              <option value="later">Op een tijdstip</option>
              <option value="klaarzetten">Alleen klaarzetten</option>
            </Select>
          </div>
        </div>
        {wanneer === "later" && (
          <div>
            <Label>Tijdstip</Label>
            <Input type="datetime-local" value={tijdstip} onChange={(e) => setTijdstip(e.target.value)} />
          </div>
        )}
        <div>
          <Label>Extra instructie voor deze planning (optioneel)</Label>
          <TextArea rows={2} value={instructie} onChange={(e) => setInstructie(e.target.value)}
                    placeholder="Geldt voor elk ticket in deze planning, bovenop de vaste werkafspraken van het bord." />
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <BijlageKnop
              labId={labId}
              dir={`/workspace/uploads/planning`}
              compact
              onToegevoegd={(nieuwe) =>
                setBijlagen((prev) => [
                  ...prev,
                  ...nieuwe.filter((n) => !prev.some((p) => p.path === n.path)),
                ])}
            />
            <BijlageLijst bijlagen={bijlagen}
                          onVerwijder={(path) =>
                            setBijlagen((prev) => prev.filter((b) => b.path !== path))} />
          </div>
        </div>

        <div className="rounded border border-border p-2">
          <Label>Tegelijk werken</Label>
          <div className="mt-1 flex items-center gap-2">
            <Input type="number" min={1} className="w-24" placeholder="auto"
                   value={parallel} onChange={(e) => setParallel(e.target.value)} />
            <span className="text-xs text-muted-foreground">
              tickets tegelijk. Leeg = zoveel als er werkers vrij zijn in het lab.
            </span>
          </div>
          <p className="mt-1 text-[11px] text-muted-foreground">
            Tickets die niets met elkaar te maken hebben hoeven niet op elkaar te wachten.
            Een agent meldt met <code>board__claim</code> waar hij aan zit, en LabX start geen
            ticket dat aan hetzelfde zou komen — dus je hoeft dat niet vooraf uit te zoeken.
          </p>
          <div className="mt-2">
            <Toggle checked={apart} onChange={setApart}
                    label="Elk ticket een eigen werkmap" />
            <p className="mt-1 text-[11px] text-muted-foreground">
              Werkers delen /workspace. Voor werk dat vooral API's aanroept (Fabric, Azure) is
              dat prima. Zitten deze tickets in BESTANDEN, zet dit dan aan: elk ticket krijgt
              dan een eigen map om in te werken. Scheiding, geen isolatie — de agent kán er nog
              omheen, hij krijgt de instructie het niet te doen.
            </p>
          </div>
        </div>

        {fout && <p className="text-sm text-destructive">{fout}</p>}
        <Button className="w-full" onClick={opslaan}
                disabled={busy || rij.length === 0 || (wanneer === "later" && !tijdstip)}>
          {busy ? "Bezig…" : wanneer === "later" ? "Inplannen" : wanneer === "nu" ? "Starten" : "Klaarzetten"}
        </Button>
      </div>
    </Modal>
  );
}
