/**
 * pages/ChatPage.tsx
 *
 * The fix for "GUI pagina waarin we chatten aan een gekoppeld lab. Zonder
 * mag er niets werken": there is no unbound chat mode. A thread cannot be
 * created without picking a (running) lab first, and the input is disabled
 * until one is bound.
 */
import { useEffect, useRef, useState } from "react";
import { PanelRight, Pencil, Pin, Shield, Terminal, Trash2 } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { chatApi } from "@/lib/chat";
import { labsApi } from "@/lib/labs";
import { settingsApi } from "@/lib/settings";
import type { BackgroundRunDto, ChatEvent, Lab, Message, Thread } from "@/lib/types";
import { Badge, Button, Card, EmptyState, Input, Modal, TextArea } from "@/components/ui";
import { LabAllowlist } from "@/components/LabAllowlist";
import { LabTerminal } from "@/components/LabTerminal";
import { RunDetailModal, runDuration } from "@/components/BackgroundRunDetail";
import { getToken, ApiError } from "@/lib/api";

// Chat-standaarden leven HIER, niet op de Instellingen-pagina: elk gesprek
// kan zijn eigen model/effort kiezen via deze dropdowns of de /model en
// /effort slash-commands hieronder, en de pin-knop maakt de huidige keuze de
// standaard voor NIEUWE chats (schrijft naar /api/settings — Instellingen
// blijft puur infrastructuur: CLI-pad, auth, budget, subagents).
const MODEL_OPTIONS = [
  { value: "", label: "Standaard (instellingen)" },
  { value: "sonnet", label: "Sonnet" },
  { value: "opus", label: "Opus" },
  { value: "fable", label: "Fable" },
  { value: "haiku", label: "Haiku" },
];

const EFFORT_OPTIONS = [
  { value: "", label: "Standaard (instellingen)" },
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
  { value: "xhigh", label: "Xhigh" },
  { value: "max", label: "Max" },
];

export function ChatPage() {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [labs, setLabs] = useState<Lab[]>([]);
  const [activeThread, setActiveThread] = useState<Thread | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [liveSteps, setLiveSteps] = useState<ChatEvent[]>([]);
  const [liveAnswer, setLiveAnswer] = useState("");
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [labPanelOpen, setLabPanelOpen] = useState(false);
  const [labPanelTab, setLabPanelTab] = useState<"toegang" | "shell" | "audit">("toegang");
  const [threadRuns, setThreadRuns] = useState<BackgroundRunDto[]>([]);
  const [runDetail, setRunDetail] = useState<BackgroundRunDto | null>(null);
  const [sidePanelOpen, setSidePanelOpen] = useState(true);
  const [sideTab, setSideTab] = useState<"lab" | "taken">("lab");
  const knownRunStatusRef = useRef<Record<string, string>>({});
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  // Sticky auto-scroll: follow new content only while the user is (near) the
  // bottom — scrolling up to reread must never be hijacked by incoming
  // tokens. Updated by the container's own onScroll, read by the effect
  // below. Starts true so a freshly opened thread lands at the newest turn.
  const stickToBottomRef = useRef(true);

  function handleChatScroll() {
    const el = scrollRef.current;
    if (!el) return;
    stickToBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  }

  useEffect(() => {
    const el = scrollRef.current;
    if (el && stickToBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages, liveSteps, liveAnswer, streaming]);

  useEffect(() => {
    // A newly opened thread always starts at the latest message.
    stickToBottomRef.current = true;
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [activeThread?.id]);

  useEffect(() => {
    // Inline background-task panel: poll this thread's runs; when one
    // reaches a terminal status, refresh the transcript so the injected
    // "[Achtergrondtaak ...]" message appears immediately (CCC-style),
    // without any manual action.
    if (!activeThread) {
      setThreadRuns([]);
      return;
    }
    const threadId = activeThread.id;
    knownRunStatusRef.current = {};
    let cancelled = false;
    const poll = async () => {
      try {
        const runs = await chatApi.listBackgroundRuns({ thread_id: threadId });
        if (cancelled) return;
        setThreadRuns(runs);
        const known = knownRunStatusRef.current;
        let finishedNow = false;
        for (const r of runs) {
          const prev = known[r.id];
          if (prev === "running" && r.status !== "running") finishedNow = true;
          known[r.id] = r.status;
        }
        if (finishedNow) {
          const msgs = await chatApi.listMessages(threadId);
          if (!cancelled) setMessages((prev) => mergeServerMessages(prev, msgs));
        }
      } catch {
        /* polling must never break the chat */
      }
    };
    poll();
    const t = setInterval(poll, 5000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [activeThread?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  function startRename(t: Thread) {
    setRenamingId(t.id);
    setRenameValue(t.title);
  }

  async function commitRename() {
    if (!renamingId) return;
    const title = renameValue.trim();
    const id = renamingId;
    setRenamingId(null);
    if (!title) return;
    const updated = await chatApi.renameThread(id, title);
    setThreads((prev) => prev.map((t) => (t.id === id ? updated : t)));
    setActiveThread((prev) => (prev && prev.id === id ? updated : prev));
  }

  useEffect(() => {
    chatApi.listThreads().then(setThreads);
    labsApi.list().then(setLabs);
  }, []);

  const activeThreadIdRef = useRef<string | null>(null);
  useEffect(() => {
    activeThreadIdRef.current = activeThread?.id ?? null;
  }, [activeThread?.id]);

  async function openThread(t: Thread) {
    // Detach the local stream subscription of the previous thread — the turn
    // itself runs server-side and continues; we just stop listening here.
    abortRef.current?.abort();
    setStreaming(false);
    setLiveSteps([]);
    setLiveAnswer("");
    setActiveThread(t);
    setMessages(await chatApi.listMessages(t.id));
  }

  useEffect(() => {
    // Reattach: if this thread has a turn in flight (started here earlier, or
    // in another tab), subscribe to its live stream so progress shows exactly
    // as if we never left.
    if (!activeThread) return;
    const threadId = activeThread.id;
    let cancelled = false;
    chatApi.listBackgroundRuns({ thread_id: threadId, status: "running", mode: "foreground" }).then((runs) => {
      if (cancelled || runs.length === 0 || streaming) return;
      const run = runs[0];
      setStreaming(true);
      setLiveSteps([]);
      setLiveAnswer("");
      const controller = new AbortController();
      abortRef.current = controller;
      chatApi
        .streamBackgroundRun(
          run.id,
          (ev) => {
            if (ev.kind === "thinking" || ev.kind === "tool") setLiveSteps((prev) => [...prev, ev]);
            if (ev.kind === "delta") setLiveAnswer((prev) => prev + ev.text);
            if (ev.kind === "answer") setLiveAnswer(ev.text);
          },
          controller.signal,
        )
        .catch(() => {})
        .finally(async () => {
          if (cancelled || activeThreadIdRef.current !== threadId) return;
          const msgs = await chatApi.listMessages(threadId);
          setMessages((prev) => mergeServerMessages(prev, msgs));
          setStreaming(false);
          setLiveSteps([]);
          setLiveAnswer("");
        });
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeThread?.id]);

  async function createThreadForLab(labId: string) {
    const t = await chatApi.createThread(labId);
    setThreads((prev) => [t, ...prev]);
    setActiveThread(t);
    setMessages([]);
  }

  async function removeThread(t: Thread) {
    if (!confirm(`Chat "${t.title}" verwijderen?`)) return;
    await chatApi.deleteThread(t.id);
    setThreads((prev) => prev.filter((x) => x.id !== t.id));
    setActiveThread((prev) => (prev && prev.id === t.id ? null : prev));
  }

  async function setThreadModel(id: string, model: string | null) {
    const updated = await chatApi.setThreadModel(id, model);
    setThreads((prev) => prev.map((t) => (t.id === id ? updated : t)));
    setActiveThread((prev) => (prev && prev.id === id ? updated : prev));
  }

  async function setThreadEffort(id: string, effort: string | null) {
    const updated = await chatApi.setThreadEffort(id, effort);
    setThreads((prev) => prev.map((t) => (t.id === id ? updated : t)));
    setActiveThread((prev) => (prev && prev.id === id ? updated : prev));
  }

  async function sendBackground() {
    if (!activeThread || !input.trim() || streaming) return;
    const text = input;
    setInput("");
    setMessages((prev) => [
      ...prev,
      { id: `tmp-user-${Date.now()}`, thread_id: activeThread.id, role: "user", content: text, steps: [], created_at: new Date().toISOString() },
    ]);
    try {
      const run = await chatApi.startBackground(activeThread.id, text);
      pushLocalNotice(activeThread.id,
        `Gestart als achtergrondtaak \`${run.id.slice(0, 8)}\` — de voortgang verschijnt hieronder bij het invoerveld en het resultaat landt vanzelf in dit gesprek.`);
    } catch (err) {
      pushLocalNotice(activeThread.id,
        `Achtergrondtaak starten mislukt: ${err instanceof ApiError ? err.message : String(err)}`);
    }
  }

  async function pinAsDefault(kind: "model" | "effort", value: string | null) {
    if (!activeThread) return;
    await settingsApi.update(kind === "model" ? { default_model: value } : { default_effort: value });
    pushLocalNotice(activeThread.id,
      `"${value || "(standaard)"}" is nu de standaard-${kind === "model" ? "model" : "effort"} voor nieuwe chats.`);
  }

  function pushLocalNotice(threadId: string, text: string) {
    setMessages((prev) => [
      ...prev,
      { id: `tmp-notice-${Date.now()}`, thread_id: threadId, role: "assistant", content: text, steps: [], created_at: new Date().toISOString() },
    ]);
  }

  /** Replace the list with the server's version WITHOUT losing local-only
   * bubbles: optimistic user messages the server doesn't have yet (the
   * background-task poll could otherwise wipe a just-typed bubble for the
   * whole duration of a streaming answer — the "verdwenen tekstballonnen"
   * bug) and local notices (slash-command feedback). */
  function mergeServerMessages(prev: Message[], server: Message[]): Message[] {
    const keepLocal = prev.filter((m) => {
      if (!m.id.startsWith("tmp-")) return false;
      if (m.id.startsWith("tmp-notice-")) return true;
      return !server.some((s) => s.role === m.role && s.content === m.content);
    });
    return [...server, ...keepLocal];
  }

  async function send() {
    if (!activeThread || !input.trim() || streaming) return;
    const text = input;
    setInput("");

    // `/model <naam>` and `/effort <niveau>` are local LabX affordances, not
    // sent to the agent — each turn is its own CLI subprocess (no live REPL
    // to redirect), so "switch model/effort" is a per-thread setting change,
    // same mechanism as the dropdowns in the header.
    const trimmed = text.trim();
    if (trimmed.startsWith("/model") || trimmed.startsWith("/effort")) {
      const isModel = trimmed.startsWith("/model");
      const cmd = isModel ? "/model" : "/effort";
      const options = isModel ? MODEL_OPTIONS : EFFORT_OPTIONS;
      const arg = trimmed.slice(cmd.length).trim().toLowerCase();
      if (!arg || arg === "help" || arg === "list") {
        pushLocalNotice(activeThread.id,
          `Beschikbare opties voor ${cmd}: ${options.filter((o) => o.value).map((o) => o.value).join(", ")} ` +
          `— gebruik bv. \`${cmd} ${options[1]?.value}\` of de dropdown hierboven. Leeg = standaard uit Instellingen.`);
        return;
      }
      if (isModel) await setThreadModel(activeThread.id, arg);
      else await setThreadEffort(activeThread.id, arg);
      pushLocalNotice(activeThread.id, `${isModel ? "Model" : "Effort"} voor deze chat gewijzigd naar "${arg}".`);
      return;
    }

    setMessages((prev) => [
      ...prev,
      { id: `tmp-user-${Date.now()}`, thread_id: activeThread.id, role: "user", content: text, steps: [], created_at: new Date().toISOString() },
    ]);
    setStreaming(true);
    setLiveSteps([]);
    setLiveAnswer("");
    const threadId = activeThread.id;
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await chatApi.ask(
        threadId,
        text,
        (ev) => {
          if (ev.kind === "thinking" || ev.kind === "tool") setLiveSteps((prev) => [...prev, ev]);
          if (ev.kind === "delta") setLiveAnswer((prev) => prev + ev.text);
          if (ev.kind === "answer") setLiveAnswer(ev.text);
        },
        controller.signal,
      );
    } catch (err) {
      if (err instanceof ApiError && activeThreadIdRef.current === threadId) {
        pushLocalNotice(threadId, `Kon de beurt niet starten: ${err.message}`);
      }
      // AbortError (thread switch / navigation) is fine: the turn keeps
      // running server-side; the reattach effect picks it up again.
    } finally {
      // Only touch UI state if the user is still looking at this thread —
      // otherwise this finally (fired by the abort during a switch) would
      // inject the OLD thread's messages into the NEW thread's view.
      if (activeThreadIdRef.current === threadId) {
        setStreaming(false);
        const msgs = await chatApi.listMessages(threadId);
        setMessages((prev) => mergeServerMessages(prev, msgs));
        setLiveSteps([]);
        setLiveAnswer("");
      }
    }
  }

  const lab = activeThread ? labs.find((l) => l.id === activeThread.lab_id) : null;
  const inputDisabled = !activeThread || !lab || lab.status !== "running" || streaming;

  // Cumulative token/cost counter for this conversation, summed from the
  // per-turn usage events persisted in each assistant message's steps.
  const threadUsage = messages.reduce(
    (acc, m) => {
      for (const s of m.steps || []) {
        if ((s as any).kind === "usage") {
          acc.input_tokens += (s as any).input_tokens || 0;
          acc.output_tokens += (s as any).output_tokens || 0;
          acc.cost_usd += (s as any).cost_usd || 0;
        }
      }
      return acc;
    },
    { input_tokens: 0, output_tokens: 0, cost_usd: 0 },
  );

  // The most recent turn's input-token count IS the current context size
  // (everything the model saw that turn, cache included).
  const lastContextTokens = (() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const u = (messages[i].steps || []).find((s) => (s as any).kind === "usage") as any;
      if (u) return u.input_tokens || 0;
    }
    return 0;
  })();

  return (
    <div className="flex h-full">
      <aside className="w-64 shrink-0 overflow-y-auto border-r border-border p-3">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold">Chats</h2>
        </div>
        <LabPicker labs={labs} onPick={createThreadForLab} />
        <ul className="mt-3 space-y-1">
          {threads.map((t) => (
            <li
              key={t.id}
              onClick={() => renamingId !== t.id && openThread(t)}
              className={`group flex items-center gap-1 rounded px-2 py-1.5 text-sm ${
                renamingId === t.id ? "" : "cursor-pointer"
              } ${
                activeThread?.id === t.id ? "bg-primary/10" : "hover:bg-secondary"
              }`}
            >
              {renamingId === t.id ? (
                <Input
                  autoFocus
                  value={renameValue}
                  onChange={(e) => setRenameValue(e.target.value)}
                  onClick={(e) => e.stopPropagation()}
                  onKeyDown={(e) => e.key === "Enter" && commitRename()}
                  onBlur={commitRename}
                  className="py-0.5"
                />
              ) : (
                <>
                  <span className="flex-1 truncate">{t.title}</span>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      startRename(t);
                    }}
                    className="hidden shrink-0 text-muted-foreground hover:text-foreground group-hover:block"
                    title="Naam wijzigen"
                  >
                    <Pencil size={13} />
                  </button>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      removeThread(t);
                    }}
                    className="hidden shrink-0 text-muted-foreground hover:text-destructive group-hover:block"
                    title="Verwijderen"
                  >
                    <Trash2 size={13} />
                  </button>
                </>
              )}
            </li>
          ))}
        </ul>
      </aside>

      <div className="flex flex-1 flex-col">
        {!activeThread ? (
          <div className="flex flex-1 items-center justify-center">
            <EmptyState>Kies een lab hiernaast om een nieuwe chat te starten.</EmptyState>
          </div>
        ) : (
          <>
            <div className="flex items-center gap-2 border-b border-border px-4 py-2 text-sm">
              {renamingId === activeThread.id ? (
                <Input
                  autoFocus
                  value={renameValue}
                  onChange={(e) => setRenameValue(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && commitRename()}
                  onBlur={commitRename}
                  className="max-w-xs py-0.5"
                />
              ) : (
                <span className="group flex items-center gap-1 font-medium">
                  {activeThread.title}
                  <button
                    onClick={() => startRename(activeThread)}
                    className="text-muted-foreground opacity-0 hover:text-foreground group-hover:opacity-100"
                    title="Naam wijzigen"
                  >
                    <Pencil size={13} />
                  </button>
                </span>
              )}
              {lab && <Badge tone={lab.status === "running" ? "green" : "red"}>⬢ {lab.name} — {lab.status}</Badge>}
              {lab && lab.status !== "running" && (
                <span className="ml-2 text-xs text-muted-foreground">Start dit lab om te kunnen chatten.</span>
              )}
              {lab && (
                <div className="flex items-center gap-1">
                  <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Model</span>
                  <select
                    value={activeThread.model || ""}
                    onChange={(e) => setThreadModel(activeThread.id, e.target.value || null)}
                    className="rounded-md border border-input bg-background px-2 py-1 text-xs"
                    title="Model voor deze chat — of typ /model <naam> in het bericht"
                  >
                    {MODEL_OPTIONS.map((m) => (
                      <option key={m.value} value={m.value}>{m.label}</option>
                    ))}
                  </select>
                  <button
                    onClick={() => pinAsDefault("model", activeThread.model)}
                    className="text-muted-foreground hover:text-foreground"
                    title="Maak dit het standaardmodel voor nieuwe chats"
                  >
                    <Pin size={13} />
                  </button>
                </div>
              )}
              {lab && (
                <div className="flex items-center gap-1">
                  <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground" title="Hoeveel denkwerk het model per beurt mag doen (reasoning effort)">Effort</span>
                  <select
                    value={activeThread.effort || ""}
                    onChange={(e) => setThreadEffort(activeThread.id, e.target.value || null)}
                    className="rounded-md border border-input bg-background px-2 py-1 text-xs"
                    title="Reasoning effort (hoeveelheid denkwerk) voor deze chat — of typ /effort <niveau> in het bericht"
                  >
                    {EFFORT_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>{o.label}</option>
                    ))}
                  </select>
                  <button
                    onClick={() => pinAsDefault("effort", activeThread.effort)}
                    className="text-muted-foreground hover:text-foreground"
                    title="Maak dit de standaard-effort voor nieuwe chats"
                  >
                    <Pin size={13} />
                  </button>
                </div>
              )}
              {lab && (
                <button
                  onClick={() => setSidePanelOpen((v) => !v)}
                  className="ml-auto flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                  title={sidePanelOpen ? "Zijpaneel verbergen" : "Zijpaneel tonen (lab-beheer & achtergrondtaken)"}
                >
                  <PanelRight size={14} /> {sidePanelOpen ? "" : "Paneel"}
                </button>
              )}
            </div>
            <div ref={scrollRef} onScroll={handleChatScroll} className="flex-1 space-y-4 overflow-y-auto p-4">
              {messages.map((m) => (
                <ChatBubble key={m.id} message={m} />
              ))}
              {streaming && (
                <Card className="p-3 text-sm">
                  {liveSteps.map((s, i) => (
                    <div key={i} className="text-xs text-muted-foreground">
                      {s.kind === "tool" ? `🔧 ${(s as any).name}` : (s as any).text}
                    </div>
                  ))}
                  {liveAnswer && <div className="markdown-body mt-1"><ReactMarkdown remarkPlugins={[remarkGfm]}>{liveAnswer}</ReactMarkdown></div>}
                  {!liveAnswer && <div className="text-muted-foreground">Bezig…</div>}
                </Card>
              )}
            </div>
            <div className="border-t border-border p-3">
              <div className="flex gap-2">
                <TextArea
                  rows={2}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      send();
                    }
                  }}
                  disabled={inputDisabled}
                  placeholder={inputDisabled ? "Koppel en start eerst een lab…" : "Typ een bericht… (Markdown, Shift+Enter voor nieuwe regel, /model <naam> om het model te wisselen)"}
                  className="resize-none"
                />
                <div className="flex flex-col justify-end gap-1">
                  <Button onClick={send} disabled={inputDisabled || !input.trim()}>
                    Stuur
                  </Button>
                  <Button
                    variant="secondary"
                    onClick={sendBackground}
                    disabled={!activeThread || !lab || lab.status !== "running" || !input.trim()}
                    title="Start dit als achtergrondtaak: de chat blijft direct bruikbaar en je volgt de voortgang op het tabblad Achtergrondtaken"
                  >
                    Op de achtergrond
                  </Button>
                </div>
              </div>
            </div>
          </>
        )}
      </div>

      {activeThread && lab && sidePanelOpen && (
        <aside className="flex w-80 shrink-0 flex-col border-l border-border">
          <div className="flex shrink-0 gap-1 border-b border-border px-2 text-sm">
            {(["lab", "taken"] as const).map((t) => (
              <button
                key={t}
                onClick={() => setSideTab(t)}
                className={`px-3 py-2 ${sideTab === t ? "border-b-2 border-primary font-medium" : "text-muted-foreground"}`}
              >
                {t === "lab" ? "Lab" : `Taken${threadRuns.filter((r) => r.status === "running" && r.mode === "background").length ? ` (${threadRuns.filter((r) => r.status === "running" && r.mode === "background").length})` : ""}`}
              </button>
            ))}
          </div>
          <div className="flex-1 overflow-y-auto p-3">
            {sideTab === "lab" ? (
              <div className="space-y-4 text-sm">
                <div className="space-y-1">
                  <div className="flex items-center justify-between">
                    <span className="font-semibold">{lab.name}</span>
                    <Badge tone={lab.status === "running" ? "green" : "red"}>{lab.status}</Badge>
                  </div>
                  <div className="text-xs text-muted-foreground">{lab.image}</div>
                </div>
                <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs text-muted-foreground">
                  <span>CPU</span><span className="text-foreground">{lab.cpu_limit}</span>
                  <span>RAM</span><span className="text-foreground">{lab.mem_limit_mb} MB</span>
                  <span>TTL</span><span className="text-foreground">{lab.ttl_hours}u</span>
                  <span>Netwerk</span><span className="text-foreground">{lab.allow_network ? "aan" : "uit"}</span>
                  <span>Data-guard</span><span className="text-foreground">{lab.data_guard ? "aan" : "uit"}</span>
                  <span>LLM-guard</span><span className="text-foreground">{lab.llm_guard ? "aan" : "uit"}</span>
                </div>
                <div className="space-y-1.5">
                  {lab.status === "running" ? (
                    <Button variant="secondary" className="w-full" onClick={() => labsApi.stop(lab.id).then(() => labsApi.list().then(setLabs))}>
                      Lab stoppen
                    </Button>
                  ) : (
                    <Button className="w-full" onClick={() => labsApi.start(lab.id).then(() => labsApi.list().then(setLabs))}>
                      Lab starten
                    </Button>
                  )}
                  <Button
                    variant="secondary" className="w-full"
                    onClick={() => { setLabPanelTab("shell"); setLabPanelOpen(true); }}
                  >
                    <Terminal size={14} /> Shell & commando's
                  </Button>
                  <Button
                    variant="secondary" className="w-full"
                    onClick={() => { setLabPanelTab("toegang"); setLabPanelOpen(true); }}
                  >
                    <Shield size={14} /> Toegang & guard-audit
                  </Button>
                </div>
                {threadUsage.output_tokens > 0 && (
                  <div className="rounded-md border border-border p-2 text-xs text-muted-foreground">
                    <div className="mb-1 font-semibold text-foreground">Verbruik dit gesprek</div>
                    <div>↑ {threadUsage.input_tokens.toLocaleString()} in · ↓ {threadUsage.output_tokens.toLocaleString()} uit</div>
                    {threadUsage.cost_usd > 0 && <div>${threadUsage.cost_usd.toFixed(4)}</div>}
                    {lastContextTokens > 0 && (
                      <div className="mt-1 border-t border-border pt-1" title="De input-tokens van de laatste beurt = alles wat het model toen zag (systeeminstructies + gesprek + tools). De Claude Code CLI compact automatisch zodra het venster vol raakt (instelbaar via Instellingen → Geavanceerd → Autocompact).">
                        Contextvenster (laatste beurt): {lastContextTokens.toLocaleString()} tokens
                      </div>
                    )}
                  </div>
                )}
              </div>
            ) : (
              <div className="space-y-2">
                {threadRuns.filter((r) => r.mode === "background").length === 0 && (
                  <p className="text-xs text-muted-foreground">
                    Nog geen achtergrondtaken in dit gesprek. Start er een met "Op de achtergrond",
                    of vraag de agent iets langlopends — die zet het zelf als taak weg.
                  </p>
                )}
                {threadRuns.filter((r) => r.mode === "background").map((r) => (
                  <button
                    key={r.id}
                    onClick={() => setRunDetail(r)}
                    className="block w-full rounded-md border border-border p-2 text-left text-xs hover:border-primary/40"
                  >
                    <div className="flex items-center gap-2">
                      {r.status === "running" && <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-warning" />}
                      <Badge tone={r.status === "running" ? "yellow" : r.status === "completed" ? "green" : r.status === "failed" ? "red" : "neutral"}>
                        {r.status}
                      </Badge>
                      <span className="ml-auto text-muted-foreground">{runDuration(r)}</span>
                    </div>
                    <div className="mt-1 truncate text-muted-foreground">{r.prompt}</div>
                    <div className="mt-0.5 flex items-center justify-between text-muted-foreground">
                      <span>{(r.steps || []).length} stappen</span>
                      {r.status === "running" && (
                        <span
                          role="button"
                          className="text-destructive hover:underline"
                          onClick={(e) => { e.stopPropagation(); chatApi.cancelBackgroundRun(r.id); }}
                        >
                          Annuleer
                        </span>
                      )}
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        </aside>
      )}

      {labPanelOpen && lab && (
        <Modal open onClose={() => setLabPanelOpen(false)} title={`Lab-paneel — ${lab.name}`} wide>
          <div className="mb-3 flex gap-1 border-b border-border text-sm">
            {(["toegang", "shell", "audit"] as const).map((t) => (
              <button
                key={t}
                onClick={() => setLabPanelTab(t)}
                className={`px-3 py-1.5 ${labPanelTab === t ? "border-b-2 border-primary font-medium" : "text-muted-foreground"}`}
              >
                {{ toegang: "Toegang", shell: "Shell", audit: "Guard-audit" }[t]}
              </button>
            ))}
          </div>
          {labPanelTab === "toegang" && (
            <LabAllowlist
              lab={lab}
              onSaved={(updated) => {
                setLabs((prev) => prev.map((l) => (l.id === updated.id ? updated : l)));
              }}
            />
          )}
          {labPanelTab === "shell" && <ChatShellPanel lab={lab} />}
          {labPanelTab === "audit" && <ChatGuardAudit labId={lab.id} />}
        </Modal>
      )}

      {runDetail && <RunDetailModal run={runDetail} onClose={() => setRunDetail(null)} />}
    </div>
  );
}

function ChatShellPanel({ lab }: { lab: Lab }) {
  const [tab, setTab] = useState<"exec" | "terminal">("exec");
  return (
    <div>
      <div className="mb-3 flex gap-1 text-xs">
        {(["exec", "terminal"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`rounded-md px-2 py-1 ${tab === t ? "bg-secondary font-medium" : "text-muted-foreground"}`}
          >
            {t === "exec" ? "Commando" : "Terminal"}
          </button>
        ))}
      </div>
      {lab.status !== "running" && (
        <p className="mb-2 text-xs text-muted-foreground">Start dit lab om shell-acties uit te voeren.</p>
      )}
      {tab === "exec" ? <ChatExecPanel lab={lab} /> : <LabTerminal labId={lab.id} token={getToken() || ""} />}
    </div>
  );
}

function ChatExecPanel({ lab }: { lab: Lab }) {
  const [command, setCommand] = useState("");
  const [result, setResult] = useState<{ exit_code: number; output: string; guarded?: boolean; guard_reason?: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const r = await labsApi.exec(lab.id, command);
      setResult(r);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Uitvoeren mislukt");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="mb-2 flex gap-2">
        <Input
          value={command}
          onChange={(e) => setCommand(e.target.value)}
          placeholder="echo hallo"
          disabled={lab.status !== "running"}
          onKeyDown={(e) => e.key === "Enter" && run()}
        />
        <Button onClick={run} disabled={busy || lab.status !== "running" || !command.trim()}>
          {busy ? "…" : "Run"}
        </Button>
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
      {result && (
        <div>
          {result.guarded && <Badge tone="red">geblokkeerd door data-guard: {result.guard_reason}</Badge>}
          <pre className="mt-2 max-h-64 overflow-auto rounded bg-secondary p-3 text-xs whitespace-pre-wrap">
            exit {result.exit_code}
            {"\n"}
            {result.output}
          </pre>
        </div>
      )}
    </div>
  );
}

function ChatGuardAudit({ labId }: { labId: string }) {
  const [items, setItems] = useState<any[]>([]);
  useEffect(() => {
    labsApi.guardAudit(labId, 50).then((r) => setItems(r.items));
  }, [labId]);
  if (items.length === 0) return <p className="text-xs text-muted-foreground">Nog geen guard-activiteit.</p>;
  return (
    <div className="max-h-48 overflow-auto rounded border border-border text-xs">
      <table className="w-full">
        <thead className="sticky top-0 bg-secondary">
          <tr>
            <th className="p-2 text-left">Tijd</th>
            <th className="p-2 text-left">Blocked</th>
            <th className="p-2 text-left">Reden</th>
          </tr>
        </thead>
        <tbody>
          {items.map((it, i) => (
            <tr key={i} className="border-t border-border">
              <td className="p-2">{new Date(it.ts).toLocaleTimeString()}</td>
              <td className="p-2">{it.data?.blocked ? <Badge tone="red">ja</Badge> : <Badge tone="green">nee</Badge>}</td>
              <td className="p-2">{it.data?.guard_reason || "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function LabPicker({ labs, onPick }: { labs: Lab[]; onPick: (id: string) => void }) {
  const [value, setValue] = useState("");
  if (labs.length === 0) {
    return <p className="text-xs text-muted-foreground">Nog geen labs — maak er eerst een aan op de Labs-pagina.</p>;
  }
  return (
    <div className="flex gap-1">
      <select value={value} onChange={(e) => setValue(e.target.value)} className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm">
        <option value="">Nieuwe chat met lab…</option>
        {labs.map((l) => (
          <option key={l.id} value={l.id}>
            {l.name} ({l.status})
          </option>
        ))}
      </select>
      <Button variant="secondary" disabled={!value} onClick={() => value && onPick(value)}>
        Start
      </Button>
    </div>
  );
}

/**
 * react-markdown silently DROPS raw-HTML nodes (no rehype-raw plugin), so a
 * message containing e.g. `<pad>` or `<naam>` renders with those parts
 * invisible — observed in real assistant answers in this database. Escape
 * `<` when it starts a tag-like sequence, but never inside code fences or
 * inline code (where markdown already renders it literally).
 */
function escapeRawHtml(md: string): string {
  const parts = md.split(/(```[\s\S]*?```|`[^`\n]*`)/g);
  return parts
    .map((part, i) => (i % 2 === 1 ? part : part.replace(/<(?=[A-Za-z/!?])/g, "\\<")))
    .join("");
}

function UsageFooter({ steps }: { steps: ChatEvent[] }) {
  const usage = (steps || []).find((s) => (s as any).kind === "usage") as any;
  if (!usage) return null;
  const parts = [
    `↑ ${(usage.input_tokens || 0).toLocaleString()}`,
    `↓ ${(usage.output_tokens || 0).toLocaleString()} tok`,
  ];
  if (usage.cost_usd) parts.push(`$${Number(usage.cost_usd).toFixed(4)}`);
  if (usage.duration_ms) parts.push(`${Math.round(usage.duration_ms / 1000)}s`);
  return <div className="mt-1.5 text-[10px] text-muted-foreground/70">{parts.join(" · ")}</div>;
}

function ChatBubble({ message }: { message: Message }) {
  const isUser = message.role === "user";
  const visibleSteps = (message.steps || []).filter((s) => (s as any).kind !== "usage");
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div className={`max-w-2xl rounded-lg px-3 py-2 text-sm ${isUser ? "bg-primary text-primary-foreground" : "bg-secondary"}`}>
        {!isUser && visibleSteps.length > 0 && (
          <details className="mb-2 rounded-md border border-border/60 bg-background/40 px-2 py-1 text-xs">
            <summary className="cursor-pointer font-medium text-muted-foreground">
              🧠 Redenering &amp; stappen ({visibleSteps.length})
            </summary>
            <div className="mt-1 max-h-64 space-y-1 overflow-y-auto">
              {visibleSteps.map((s, i) =>
                s.kind === "tool" ? (
                  <div key={i} className="font-mono text-muted-foreground">
                    🔧 {(s as any).name}
                    {(s as any).input && (
                      <span className="opacity-70"> {JSON.stringify((s as any).input).slice(0, 160)}</span>
                    )}
                  </div>
                ) : (
                  <div key={i} className="italic text-muted-foreground">{(s as any).text}</div>
                ),
              )}
            </div>
          </details>
        )}
        <div className={isUser ? "markdown-body markdown-body-invert" : "markdown-body"}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{escapeRawHtml(message.content)}</ReactMarkdown>
        </div>
        {!isUser && <UsageFooter steps={message.steps || []} />}
      </div>
    </div>
  );
}
