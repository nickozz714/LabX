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
import { useNavigate } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { boardApi } from "@/lib/boards";
import { chatApi } from "@/lib/chat";
import type { BoardDto, ChatEvent, TicketCommentDto, TicketDto } from "@/lib/types";
import { Badge, Button, Card, Input, Label, Select, TextArea } from "@/components/ui";
import { useMelding } from "@/components/Meldingen";
import { useBevestiging } from "@/components/Bevestiging";
import { ApiError } from "@/lib/api";
import { Bot, ExternalLink, MessageSquare, Pencil, Trash2, X } from "lucide-react";
import { BijlageKnop, BijlageLijst } from "@/components/Bijlagen";
import { ticketBijlageMap } from "@/lib/boards";
import type { Bijlage } from "@/lib/labs";

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
  /** `false` = niet opgeslagen; dan blijft de editor open met de tekst erin. */
  onSave: (next: string) => Promise<boolean | void> | boolean | void;
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
      // Mislukt het opslaan, dan blijft de editor staan. Anders sloot hij alsof
      // het gelukt was en was het getypte werk weg — met alleen een regeltje
      // bovenaan het paneel als spoor.
      if ((await onSave(draft)) !== false) setEditing(false);
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
  const navigate = useNavigate();
  const melding = useMelding();
  const bevestig = useBevestiging();
  const [ticket, setTicket] = useState<TicketDto | null>(null);
  const [comments, setComments] = useState<TicketCommentDto[]>([]);
  const [draftInternal, setDraftInternal] = useState(false);
  const [commentBusy, setCommentBusy] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [instruction, setInstruction] = useState("");
  // Bijlagen bij DEZE run. Ze staan al in het lab; wat meegaat is het pad.
  const [bijlagen, setBijlagen] = useState<Bijlage[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Velden slaan op bij het verlaten van het veld. Zonder teken van leven is
  // dat niet te onderscheiden van niets doen; deze vlag zet er twee seconden
  // "opgeslagen" bij.
  const [opgeslagen, setOpgeslagen] = useState(false);

  // Live meelezen met de agent-run van dit ticket.
  // Aan wélke run dit paneel nu hangt. Een ref en geen state: hij stuurt geen
  // weergave aan, hij voorkomt dat gebeurtenissen van een verlaten run
  // binnendruppelen.
  const gehechtAan = useRef<string | null>(null);
  const [runSteps, setRunSteps] = useState<ChatEvent[]>([]);
  const [runStatus, setRunStatus] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // Wat er nú op de server staat. De velden werken tijdens het typen de lokale
  // `ticket` bij, dus daar valt niet meer aan af te lezen of er iets veranderd
  // is — en zonder dat verschil stuurt elk verlaten van een veld een PATCH, die
  // het ticket op "niet gesynct" zet en bij een two-way board ongevraagd naar
  // Jira gaat.
  const opServer = useRef<TicketDto | null>(null);

  async function load() {
    const t = await boardApi.ticket(board.id, ticketId);
    setTicket(t);
    opServer.current = t;
    // Nieuwste bovenaan: bij een ticket waar de agent een paar keer overheen is
    // gegaan wil je het laatste verslag zien zonder eerst door de historie te
    // scrollen. De backend levert oplopend (chronologisch) aan.
    setComments([...(t.comments || [])].reverse());
  }

  useEffect(() => {
    // Het verslag van de VORIGE run meteen weg. Het afbreken van de stream
    // stond hier al, maar de stappen bleven staan — en als het nieuwe ticket
    // geen lopende run heeft, stapt de effect hieronder er meteen uit en blijft
    // het verslag van een heel ander ticket in beeld. Precies wat er gebeurde.
    abortRef.current?.abort();
    gehechtAan.current = null;
    setRunSteps([]);
    setRunStatus(null);
    load().catch(() => setError("Ticket laden mislukt"));
    return () => abortRef.current?.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticketId]);

  // Loopt er al een run (bv. gestart door een schedule)? Dan meteen aanhaken.
  useEffect(() => {
    if (!ticket?.agent_run_id || ticket.agent_state !== "running") return;
    // `load()` is async: tijdens het laden van het NIEUWE ticket wijst `ticket`
    // nog naar het oude. Zonder deze toets haakt hij dan opnieuw aan bij de run
    // van het ticket dat je net verliet.
    if (ticket.id !== ticketId) return;
    attachToRun(ticket.agent_run_id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticket?.id, ticket?.agent_run_id, ticket?.agent_state, ticketId]);

  function attachToRun(runId: string) {
    if (gehechtAan.current === runId) return;   // al aangehaakt; niet opnieuw
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    gehechtAan.current = runId;
    setRunSteps([]);
    setRunStatus("running");
    chatApi
      .streamBackgroundRun(
        runId,
        (ev) => {
          // Alleen de voortgangsstappen: het eindantwoord van de agent komt
          // als opmerking in de tijdlijn terecht, dus dat hier óók tonen zou
          // hetzelfde verslag twee keer op het scherm zetten.
          // Een stream die net is afgebroken kan nog één gebeurtenis
          // nalopen; die hoort niet bij het ticket dat nu openstaat.
          if (gehechtAan.current !== runId) return;
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

  /** Geeft terug of het opslaan lukte; de aanroeper hoeft dat niet te vangen. */
  async function save(patch: Record<string, any>): Promise<boolean> {
    if (!ticket) return false;
    const heen = opServer.current;
    if (heen) {
      const anders = Object.fromEntries(
        Object.entries(patch).filter(
          ([k, v]) => JSON.stringify(v ?? null) !== JSON.stringify((heen as any)[k] ?? null)),
      );
      if (Object.keys(anders).length === 0) return true;   // niets veranderd
      patch = anders;
    }
    setError(null);
    try {
      const updated = await boardApi.updateTicket(board.id, ticket.id, patch);
      opServer.current = updated;
      setTicket({ ...updated, comments: ticket.comments });
      onChanged();
      setOpgeslagen(true);
      window.setTimeout(() => setOpgeslagen(false), 2000);
      return true;
    } catch (err) {
      const tekst = err instanceof ApiError ? err.message : "Opslaan mislukt";
      setError(tekst);
      // Ook als melding: het regeltje bovenaan het paneel staat bij een lang
      // ticket buiten beeld, en dan lijkt een mislukte opslag op een gelukte.
      melding.fout("Opslaan mislukt", tekst);
      return false;
    }
  }

  async function postComment() {
    if (!ticket || !draft.trim()) return;
    await boardApi.addComment(board.id, ticket.id, draft.trim(), draftInternal);
    setDraft("");
    await load();
    melding.ok(draftInternal ? "Interne opmerking geplaatst" : "Opmerking geplaatst");
  }

  /** Een interne opmerking alsnog naar de bron. Alleen deze kant op: uit Jira
   *  terughalen kan niet, dus vragen we het één keer expliciet. */
  async function promote(commentId: number) {
    if (!ticket) return;
    const ja = await bevestig.vraag({
      titel: "Deze opmerking naar de bron sturen?",
      tekst: `Hij komt in ${ticket.external_key || "de bron"} te staan. Terughalen kan daarna niet meer.`,
      bevestig: "Versturen",
      variant: "primary",
    });
    if (!ja) return;
    setCommentBusy(commentId);
    try {
      const r = await boardApi.promoteComment(board.id, ticket.id, commentId);
      if (r.pushed && !r.pushed.ok) {
        const tekst = r.pushed.error || "Plaatsen in de bron mislukt";
        setError(tekst);
        melding.fout("Niet in de bron geplaatst", tekst);
      } else if (r.pushed) {
        melding.ok("In de bron geplaatst");
      } else {
        // Geen two-way board: de opmerking staat nu klaar en gaat mee met de
        // eerstvolgende sync. Zonder dit onderscheid lijkt "niets gebeurd".
        melding.ok("Gemarkeerd voor de bron",
                   "Dit board synchroniseert niet twee kanten op; hij gaat mee met de volgende sync.");
      }
      await load();
    } catch (err) {
      const tekst = err instanceof ApiError ? err.message : "Promoveren mislukt";
      setError(tekst);
      melding.fout("Promoveren mislukt", tekst);
    } finally {
      setCommentBusy(null);
    }
  }

  async function runAgent() {
    if (!ticket) return;
    setBusy(true);
    setError(null);
    try {
      const started = await boardApi.runAgent(board.id, ticket.id,
                                              instruction.trim() || undefined, bijlagen);
      setInstruction("");
      attachToRun(started.run_id);
      await load();
      onChanged();
      melding.ok("Agent gestart", "Het verslag verschijnt hieronder in de tijdlijn.");
    } catch (err) {
      const tekst = err instanceof ApiError ? err.message : "Agent starten mislukt";
      setError(tekst);
      melding.fout("Agent starten mislukt", tekst);
    } finally {
      setBusy(false);
    }
  }

  async function removeTicket() {
    if (!ticket) return;
    const ja = await bevestig.vraag({
      titel: `Ticket ${ticket.key} verwijderen?`,
      tekst: `"${ticket.title}" en alle opmerkingen eronder verdwijnen uit LabX. Dit kan niet ongedaan gemaakt worden.`,
      bevestig: "Verwijderen",
    });
    if (!ja) return;
    try {
      await boardApi.removeTicket(board.id, ticket.id);
      melding.ok(`${ticket.key} verwijderd`);
      onChanged();
      onClose();
    } catch (err) {
      const tekst = err instanceof ApiError ? err.message : "Verwijderen mislukt";
      setError(tekst);
      melding.fout("Verwijderen mislukt", tekst);
    }
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
          {opgeslagen && <span className="text-[11px] text-emerald-500">opgeslagen</span>}
        </div>
        <div className="flex items-center gap-1">
          <Button variant="ghost" className="px-1.5 py-1 hover:text-destructive"
                  meldFouten={false} onClick={removeTicket} title="Verwijderen">
            <Trash2 size={15} />
          </Button>
          <Button variant="ghost" className="px-1.5 py-1" onClick={onClose} title="Sluiten">
            <X size={16} />
          </Button>
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

        <div>
          <Label>Wacht op (ticket-keys, komma-gescheiden)</Label>
          <Input
            value={(ticket.depends_on || []).join(", ")}
            onChange={(e) =>
              setTicket({ ...ticket, depends_on: e.target.value.split(",").map((s) => s.trim()) })
            }
            onBlur={(e) =>
              save({
                depends_on: e.target.value
                  .split(",")
                  .map((s) => s.trim().toUpperCase())
                  .filter(Boolean),
              })
            }
            placeholder="SWI-3, SWI-7"
          />
          <p className="mt-1 text-xs text-muted-foreground">
            Een planning pauzeert bij dit ticket zolang die tickets nog niet in een klaar-kolom
            staan. Blijft in LabX — het gaat niet mee naar Jira of DevOps.
          </p>
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
          <div className="mt-2">
            <BijlageLijst bijlagen={bijlagen}
                          onVerwijder={(path) =>
                            setBijlagen((prev) => prev.filter((b) => b.path !== path))} />
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Button
              variant="secondary"
              className="text-xs"
              disabled={busy || !board.lab_id || ticket.agent_state === "running"}
              onClick={runAgent}
              busyLabel="Starten…"
            >
              {ticket.agent_state === "running" ? "Agent werkt…" : "Agent starten"}
            </Button>
            {/* Zonder deze knop was een vastgelopen run een doodlopende weg:
                "Agent starten" blijft uitgeschakeld zolang agent_state op
                "running" staat, en er was geen tweede knop. Werkt ook als de
                run alleen nog in de database bestaat — dan geeft hij het ticket
                alsnog vrij. */}
            {ticket.agent_state === "running" && (
              <Button
                variant="danger"
                className="text-xs"
                busyLabel="Stoppen…"
                meldFouten={false}
                onClick={async () => {
                  try {
                    const uit = await boardApi.cancelAgent(board.id, ticket.id);
                    await load();
                    onChanged();
                    melding.ok(uit.afgebroken ? "Agent gestopt" : "Ticket vrijgegeven",
                               uit.afgebroken ? undefined
                                 : "De run was al gestopt; het ticket stond alleen nog op "
                                   + "'de agent werkt eraan'.");
                  } catch (err) {
                    melding.fout("Stoppen mislukt",
                                 err instanceof Error ? err.message : String(err));
                  }
                }}
              >
                Agent stoppen
              </Button>
            )}
            <BijlageKnop
              labId={board.lab_id}
              dir={ticketBijlageMap(ticket.key)}
              disabled={busy || ticket.agent_state === "running"}
              compact
              onToegevoegd={(nieuwe) =>
                setBijlagen((prev) => [
                  ...prev,
                  ...nieuwe.filter((n) => !prev.some((p) => p.path === n.path)),
                ])}
            />
            {ticket.agent_thread_id && (
              <Button
                variant="ghost"
                className="text-xs"
                title="Open de sessie van deze agent-run als chat en praat erin door"
                onClick={() => navigate(`/chat?thread=${ticket.agent_thread_id}`)}
              >
                <MessageSquare size={13} /> Verder chatten
              </Button>
            )}
            <span className="text-[11px] text-muted-foreground">
              Het verslag verschijnt hieronder in de tijdlijn.
            </span>
          </div>
          {ticket.agent_thread_id && (
            <p className="mt-1 text-[11px] text-muted-foreground">
              "Verder chatten" opent de sessie van de laatste run. De agent hervat daar zijn
              eigen sessie, dus hij weet nog wat hij gedaan en gezien heeft — je hoeft niets
              opnieuw uit te leggen.
            </p>
          )}
          {ticket.agent_last_error && (
            <p className="mt-2 text-xs text-destructive">{ticket.agent_last_error}</p>
          )}
          {runStatus && (
            <div className="mt-2 space-y-1">
              <Badge tone={runStatus === "completed" ? "green" : runStatus === "running" ? "yellow" : "red"}>
                {runStatus}
              </Badge>
              {/* Tools én afwegingen. Alleen de toolnamen tonen leest als een
                  logbestand: je ziet wát er gebeurde en nergens waarom. */}
              {runSteps.length > 0 && (
                <div className="max-h-64 space-y-1 overflow-y-auto rounded border border-border p-2 text-[11px]">
                  {runSteps.map((s, i) =>
                    s.kind === "tool" ? (
                      <div key={i} className="font-mono text-muted-foreground">
                        🔧 {(s as any).name}
                      </div>
                    ) : (s as any).text ? (
                      <div key={i} className="whitespace-pre-wrap border-l-2 border-border pl-2">
                        {(s as any).text}
                      </div>
                    ) : null,
                  )}
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
            <div className="flex flex-col items-end gap-1">
              <label className="flex items-center gap-1 text-[11px] text-muted-foreground">
                <input
                  type="checkbox"
                  checked={draftInternal}
                  onChange={(e) => setDraftInternal(e.target.checked)}
                />
                intern
              </label>
              <Button variant="secondary" className="text-xs" onClick={postComment} disabled={!draft.trim()}>
                Plaatsen
              </Button>
            </div>
          </div>
          <p className="mb-2 text-[11px] text-muted-foreground">
            Intern blijft in LabX. Alles wat de agent schrijft is intern; met "naar de bron" stuur je
            zo'n opmerking alsnog door.
          </p>
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
                  {c.kind === "comment" &&
                    (c.internal ? (
                      <Badge tone="yellow">intern</Badge>
                    ) : c.pushed || c.external_id ? (
                      <Badge tone="violet">in de bron</Badge>
                    ) : (
                      <Badge tone="neutral">gaat naar de bron</Badge>
                    ))}
                  {c.kind === "comment" && c.internal && board.provider !== "local" && (
                    <Button
                      variant="ghost"
                      className="ml-auto px-1.5 py-0.5 text-[11px] underline"
                      busy={commentBusy === c.id}
                      busyLabel="bezig…"
                      meldFouten={false}
                      onClick={() => promote(c.id)}
                    >
                      naar de bron
                    </Button>
                  )}
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
