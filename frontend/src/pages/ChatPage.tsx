/**
 * pages/ChatPage.tsx
 *
 * The fix for "GUI pagina waarin we chatten aan een gekoppeld lab. Zonder
 * mag er niets werken": there is no unbound chat mode. A thread cannot be
 * created without picking a (running) lab first, and the input is disabled
 * until one is bound.
 *
 * Diezelfde binding is ook de indeling van de chatlijst: een chat hoort bij
 * precies één lab, dus de labs ZIJN de categorieën. De lijst is per lab
 * gegroepeerd en elke groep start zijn eigen chat — een losse "kies een
 * lab"-dropdown erboven zou hetzelfde nog eens vragen. De groep is afgeleid,
 * niet toe te kennen: een chat verplaatsen naar een ander lab zou hem van zijn
 * sandbox en zijn CLI-sessie losknippen.
 *
 * Hernoemen gebeurt in een pop-up. Het was een invoerveld ín de lijst, en dat
 * viel steeds over zichzelf: blur = opslaan, dus een klik naast het veld (of op
 * een andere chat) bevestigde ongemerkt, Escape deed niets, en in de smalle
 * balk was van de naam nauwelijks iets te zien.
 */
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Archive, ArchiveRestore, Bot, ChevronDown, ChevronRight, PanelRight, Pencil, Pin, Plus, SendHorizontal, Shield, Square, Terminal, Trash2, PanelLeft, Search, X } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { chatApi, chatBijlageMap } from "@/lib/chat";
import { labsApi, type Bijlage } from "@/lib/labs";
import { settingsApi } from "@/lib/settings";
import type { BackgroundRunDto, ChatEvent, Lab, Message, Thread } from "@/lib/types";
import { Badge, Button, Card, EmptyState, Input, Label, Modal } from "@/components/ui";
import { useMelding } from "@/components/Meldingen";
import { LabAllowlist } from "@/components/LabAllowlist";
import { LabTerminal } from "@/components/LabTerminal";
import { RunDetailModal, runDuration } from "@/components/BackgroundRunDetail";
import { BijlageKnop, BijlageLijst } from "@/components/Bijlagen";
import { getToken, ApiError } from "@/lib/api";
import { MODEL_OPTIONS } from "@/lib/modellen";
import { useBevestiging } from "@/components/Bevestiging";

// Chat-standaarden leven HIER, niet op de Instellingen-pagina: elk gesprek
// kan zijn eigen model/effort kiezen via deze dropdowns of de /model en
// /effort slash-commands hieronder, en de pin-knop maakt de huidige keuze de
// standaard voor NIEUWE chats (schrijft naar /api/settings — Instellingen
// blijft puur infrastructuur: CLI-pad, auth, budget, subagents).
const EFFORT_OPTIONS = [
  { value: "", label: "Standaard (instellingen)" },
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
  { value: "xhigh", label: "Xhigh" },
  { value: "max", label: "Max" },
];

/** Een datum zoals je hem in een lijst wilt lezen: vandaag en gisteren bij
 *  naam, deze week de dag, daarvoor de datum. "8/25/2026" zegt minder dan
 *  "gisteren" als je zoekt waar je gebleven was. */
function korteDatum(waarde?: string | null): string {
  if (!waarde) return "";
  const d = new Date(waarde);
  if (Number.isNaN(d.getTime())) return "";
  const nu = new Date();
  const dag = 24 * 60 * 60 * 1000;
  const begin = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const verschil = Math.round((begin(nu) - begin(d)) / dag);
  if (verschil === 0) return `vandaag ${d.toLocaleTimeString("nl-NL", { hour: "2-digit", minute: "2-digit" })}`;
  if (verschil === 1) return "gisteren";
  if (verschil < 7) return d.toLocaleDateString("nl-NL", { weekday: "long" });
  return d.toLocaleDateString("nl-NL", { day: "numeric", month: "short", year: "numeric" });
}

/** Hoe hoog het invoerveld hoogstens wordt: ongeveer vijf regels. Daarboven
 *  scrolt het veld in plaats van het gesprek weg te duwen. */
const MAX_INVOER_HOOGTE = 128;

export function ChatPage() {
  const bevestig = useBevestiging();
  const melding = useMelding();
  const [threads, setThreads] = useState<Thread[]>([]);
  const [zoekterm, setZoekterm] = useState("");
  // Sessies achter agent-runs op een ticket. Standaard uit: op een bord met
  // tachtig tickets zou de lijst niet meer te lezen zijn. Aan als je erin wilt
  // doorpraten — of vanzelf, als je via een ticket binnenkomt.
  const [toonBoard, setToonBoard] = useState(false);
  // Actief of archief — twee lijsten die je apart bekijkt. Een archief
  // tussen je lopende gesprekken door is geen archief.
  const [toonArchief, setToonArchief] = useState(false);
  const [zoekParams, setZoekParams] = useSearchParams();
  const [labs, setLabs] = useState<Lab[]>([]);
  const [activeThread, setActiveThread] = useState<Thread | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  // Bijlagen van het bericht dat je nu typt. Ze staan al in het lab (zie
  // components/Bijlagen.tsx); dit is alleen de lijst die meegaat.
  const [bijlagen, setBijlagen] = useState<Bijlage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [liveSteps, setLiveSteps] = useState<ChatEvent[]>([]);
  const [liveAnswer, setLiveAnswer] = useState("");
  const [renaming, setRenaming] = useState<Thread | null>(null);
  // Ingeklapte lab-groepen in de chatlijst (per lab-id).
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [labPanelOpen, setLabPanelOpen] = useState(false);
  const [labPanelTab, setLabPanelTab] = useState<"toegang" | "shell" | "audit">("toegang");
  const [threadRuns, setThreadRuns] = useState<BackgroundRunDto[]>([]);
  const [runDetail, setRunDetail] = useState<BackgroundRunDto | null>(null);
  // Op een telefoon staat het zijpaneel standaard dicht: 320px naast een
  // gesprek laat niets van het gesprek over. Op een groot scherm blijft het
  // openstaan zoals het was.
  const [sidePanelOpen, setSidePanelOpen] = useState(
    () => typeof window === "undefined" || window.innerWidth >= 1024);
  const [lijstOpen, setLijstOpen] = useState(false);
  const [sideTab, setSideTab] = useState<"lab" | "taken">("lab");
  const knownRunStatusRef = useRef<Record<string, string>>({});
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const invoerRef = useRef<HTMLTextAreaElement | null>(null);
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

  function applyThread(updated: Thread) {
    setThreads((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
    setActiveThread((prev) => (prev && prev.id === updated.id ? updated : prev));
  }

  useEffect(() => {
    let leeft = true;
    const haal = () => chatApi.listThreads(toonBoard, toonArchief)
      .then((r) => { if (leeft) setThreads(r); })
      .catch(() => {});
    haal();
    // Elke vijf seconden opnieuw. Zonder dat is "actief" een momentopname van
    // toen je de pagina opende, en dat is precies zo nutteloos als geen stip.
    const tik = window.setInterval(haal, 5000);
    return () => { leeft = false; window.clearInterval(tik); };
  }, [toonBoard, toonArchief]);

  useEffect(() => {
    labsApi.list().then(setLabs);
  }, []);

  // Rechtstreekse link vanaf een ticket: /chat?thread=<id>. Die thread staat
  // niet per se in de lijst (board-sessies zitten er standaard niet in), dus
  // hem apart ophalen en meteen openen.
  useEffect(() => {
    const gevraagd = zoekParams.get("thread");
    if (!gevraagd || activeThread?.id === gevraagd) return;
    chatApi
      .getThread(gevraagd)
      .then(async (t) => {
        if (t.source === "board") setToonBoard(true);
        await openThread(t);
        setZoekParams({}, { replace: true });
      })
      .catch(() => setZoekParams({}, { replace: true }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [zoekParams]);

  const activeThreadIdRef = useRef<string | null>(null);
  useEffect(() => {
    activeThreadIdRef.current = activeThread?.id ?? null;
  }, [activeThread?.id]);

  // Het invoerveld begint op één regel en groeit mee met wat je typt -- maar tot
  // een grens. Zonder die grens duwt een geplakte lap tekst het gesprek bijna
  // van het scherm, en juist dan wil je terugzien waar je op antwoordt. Vanaf
  // die grens scrolt het veld zelf.
  //
  // De hoogte moet eerst terug naar auto: anders kan hij alleen nog groeien en
  // blijft een leeggemaakt veld even hoog als het langste bericht dat erin
  // stond.
  useEffect(() => {
    const el = invoerRef.current;
    if (!el) return;
    el.style.height = "auto";
    const nodig = el.scrollHeight;
    el.style.height = `${Math.min(nodig, MAX_INVOER_HOOGTE)}px`;
    // Alleen een schuifbalk als er echt iets te schuiven valt; anders flikkert
    // hij in beeld bij de eerste regelovergang.
    el.style.overflowY = nodig > MAX_INVOER_HOOGTE ? "auto" : "hidden";
  }, [input]);

  async function openThread(t: Thread) {
    // Detach the local stream subscription of the previous thread — the turn
    // itself runs server-side and continues; we just stop listening here.
    abortRef.current?.abort();
    setStreaming(false);
    setLiveSteps([]);
    setLiveAnswer("");
    setActiveThread(t);
    // Bijlagen horen bij het bericht dat je aan het typen was, niet bij het
    // gesprek waar je naartoe gaat.
    setBijlagen([]);
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

  async function archiveThread(t: Thread) {
    const naarArchief = !t.archived_at;
    await chatApi.archiveThread(t.id, naarArchief);
    // Hij hoort niet meer in de lijst die je nu bekijkt: eruit halen in plaats
    // van bijwerken, anders blijft er een regel staan die er niet thuishoort.
    setThreads((prev) => prev.filter((x) => x.id !== t.id));
    setActiveThread((prev) =>
      prev && prev.id === t.id ? { ...prev, archived_at: naarArchief ? new Date().toISOString() : null } : prev);
    melding.ok(naarArchief ? "Chat gearchiveerd" : "Chat teruggehaald",
               naarArchief ? "Te vinden onder Archief; de inhoud blijft staan." : undefined);
  }

  async function removeThread(t: Thread) {
    const ja = await bevestig.vraag({
      titel: `Chat "${t.title}" verwijderen?`,
      tekst: "Het gesprek en alles wat de agent erin deed, verdwijnen.",
      bevestig: "Verwijderen",
    });
    if (!ja) return;
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
    if (!activeThread || (!input.trim() && !bijlagen.length) || streaming) return;
    const text = input;
    const mee = bijlagen;
    setInput("");
    setBijlagen([]);
    setMessages((prev) => [
      ...prev,
      { id: `tmp-user-${Date.now()}`, thread_id: activeThread.id, role: "user", content: text, steps: [], created_at: new Date().toISOString() },
    ]);
    try {
      const run = await chatApi.startBackground(activeThread.id, text, mee);
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

  /** De lopende beurt afbreken.
   *
   *  Het afbreken van de STREAM (abortRef) stopte alleen het meekijken — de
   *  beurt draait server-side door als foreground-run. Daarom eerst de run
   *  afbreken, dan pas de stream loslaten. Heet de run alleen nog in de
   *  database "running", dan wordt hij afgesloten; anders strandt elke
   *  volgende beurt op "er loopt al een beurt in dit gesprek".
   */
  async function stopBeurt() {
    if (!activeThread) return;
    const threadId = activeThread.id;
    try {
      const uit = await chatApi.cancelTurn(threadId);
      abortRef.current?.abort();
      setStreaming(false);
      setLiveSteps([]);
      setLiveAnswer("");
      const msgs = await chatApi.listMessages(threadId);
      setMessages((prev) => mergeServerMessages(prev, msgs));
      melding.ok(uit.afgebroken ? "Beurt afgebroken" : "Gesprek vrijgegeven", uit.detail);
    } catch (err) {
      melding.fout("Stoppen mislukt", err instanceof Error ? err.message : String(err));
    }
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
      // StartsWith en niet gelijkheid: bij een bericht met bijlagen zet de
      // server er een blok met de bestandspaden achter. Op gelijkheid zou de
      // optimistische bubbel dan niet herkend worden en naast de echte blijven
      // staan — hetzelfde bericht twee keer.
      return !server.some((s) => s.role === m.role && s.content.startsWith(m.content));
    });
    return [...server, ...keepLocal];
  }

  async function send() {
    if (!activeThread || (!input.trim() && !bijlagen.length) || streaming) return;
    const text = input;
    // De bijlagen van dít bericht vastpakken vóór het invoerveld leeggaat:
    // de gebruiker mag tijdens het streamen alweer een volgende bijlage kiezen.
    const mee = bijlagen;
    setInput("");
    setBijlagen([]);

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
        mee,
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

  // De categorieën. Ze worden afgeleid en niet opgeslagen: een chat draait ín
  // een lab (threads.lab_id is NOT NULL), dus dat lab ís zijn categorie.
  const threadGroups: ThreadGroup[] = (() => {
    // Filteren op titel. Een lab met twintig chats vind je niet meer terug op
    // het oog; dit zoekt binnen wat je nu ziet (je chats of het archief), dus
    // het blijft doen wat je verwacht.
    const zoek = zoekterm.trim().toLowerCase();
    const zichtbaar = zoek
      ? threads.filter((t) => (t.title || "").toLowerCase().includes(zoek))
      : threads;

    const byLab = new Map<string, Thread[]>();
    for (const t of zichtbaar) {
      byLab.set(t.lab_id, [...(byLab.get(t.lab_id) || []), t]);
    }
    let groups: ThreadGroup[] = labs.map((l) => ({
      id: l.id, name: l.name, lab: l, threads: byLab.get(l.id) || [],
    }));
    // Chats waarvan het lab niet in de lijst staat — terwijl de labs nog laden,
    // of als er een verdwijnt. Ze mogen niet uit beeld vallen.
    const known = new Set(labs.map((l) => l.id));
    const rest = zichtbaar.filter((t) => !known.has(t.lab_id));
    if (rest.length) {
      groups.push({ id: "__overig__", name: "Overige chats", lab: null, threads: rest });
    }
    // Terwijl je zoekt hebben lege labs geen betekenis: die zouden de ene
    // treffer wegdrukken tussen vijftien lege kopjes.
    if (zoek) groups = groups.filter((g) => g.threads.length > 0);
    return groups;
  })();
  // Wat de teller onderin telt: wat je nú in de lijst ziet, dus inclusief het
  // zoekfilter. Een chat hoort bij precies één lab, dus dubbel tellen kan niet.
  const zichtbareChats = threadGroups.reduce((n, g) => n + g.threads.length, 0);
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
    <div className="relative flex h-full">
      {/* De chatlijst is op een telefoon een lade die over het gesprek
          schuift; vanaf tablet blijft hij gewoon naast de chat staan. */}
      {lijstOpen && (
        <button type="button" aria-label="Chatlijst sluiten" onClick={() => setLijstOpen(false)}
                className="fixed inset-0 z-30 bg-foreground/30 lg:hidden" />
      )}
      <aside className={`${lijstOpen ? "absolute inset-y-0 left-0 z-40 flex w-[80vw] max-w-xs shadow-xl" : "hidden"} min-h-0 shrink-0 flex-col overflow-hidden border-r border-border bg-background lg:static lg:z-auto lg:flex lg:w-64 lg:max-w-none lg:shadow-none`}>
        {/* Kop en zoekveld blijven staan; alleen de lijst eronder scrollt. Bij
            veertig chats scrolde het zoekveld anders weg precies op het moment
            dat je het nodig had. */}
        <div className="shrink-0 border-b border-border p-3 pb-2">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold">{toonArchief ? "Archief" : "Chats"}</h2>
          <label className="flex cursor-pointer items-center gap-1 text-[11px] text-muted-foreground"
                 title="Ook de sessies achter agent-runs op een ticket tonen. Daar kun je gewoon in doorpraten — de agent hervat dan zijn eigen sessie.">
            <input type="checkbox" checked={toonBoard} onChange={(e) => setToonBoard(e.target.checked)} />
            board
          </label>
        </div>
        {/* Een nieuwe chat beginnen is de meest voorkomende handeling en zat
            achter een plusje dat pas bij hover verschijnt -- op een touchscreen
            dus helemaal niet. */}
        <Button
          className="mb-2 w-full justify-center text-xs"
          disabled={!labs.some((l) => l.status === "running")}
          title={labs.some((l) => l.status === "running")
            ? "Nieuwe chat in het eerste draaiende lab"
            : "Er draait geen lab"}
          onClick={() => {
            const lab = (activeThread && labs.find((l) => l.id === activeThread.lab_id
                                                   && l.status === "running"))
              || labs.find((l) => l.status === "running");
            if (lab) createThreadForLab(lab.id);
          }}
        >
          <Plus size={14} /> Nieuwe chat
        </Button>

        <div className="relative mb-2">
          <Search size={13} className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <input
            value={zoekterm}
            onChange={(e) => setZoekterm(e.target.value)}
            placeholder="Zoek een chat…"
            className="w-full rounded border border-input bg-background py-1.5 pl-7 pr-7 text-xs
                       placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
          />
          {zoekterm && (
            <button onClick={() => setZoekterm("")} aria-label="Zoekterm wissen"
                    className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:bg-muted">
              <X size={12} />
            </button>
          )}
        </div>
        <button
          onClick={() => setToonArchief((v) => !v)}
          className="mb-2 flex w-full items-center gap-1.5 rounded px-1 py-1 text-[11px] text-muted-foreground hover:bg-secondary/60 hover:text-foreground"
        >
          <Archive size={12} />
          {toonArchief ? "Terug naar je chats" : "Archief bekijken"}
        </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {labs.length === 0 && threadGroups.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            Nog geen labs — maak er eerst een aan op de Labs-pagina.
          </p>
        ) : (
          <div className="space-y-2">
            {threadGroups.map((group) => {
              const open = !collapsed[group.id];
              return (
                <section key={group.id}>
                  <div className="group flex items-center gap-1 rounded px-1 py-1 hover:bg-secondary/60">
                    <button
                      onClick={() => setCollapsed((prev) => ({ ...prev, [group.id]: open }))}
                      className="flex min-w-0 flex-1 items-center gap-1 text-left"
                      title={group.lab ? `Lab ${group.lab.name} — ${group.lab.status}` : group.name}
                    >
                      {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                      {/* Statusstip: of je in dit lab kúnt chatten is de eerste
                          vraag bij een chat, dus die staat bij de categorie. */}
                      <span
                        className={`size-1.5 shrink-0 rounded-full ${
                          group.lab?.status === "running" ? "bg-success" : "bg-muted-foreground/40"
                        }`}
                      />
                      <span className="truncate text-xs font-semibold">{group.name}</span>
                      <span className="shrink-0 text-[11px] text-muted-foreground">
                        {group.threads.length}
                      </span>
                      {group.threads.some((t) => t.actief) && (
                        <span className="shrink-0 rounded-full bg-success/15 px-1.5 text-[10px] font-semibold text-success"
                              title="Chats met een lopende beurt in dit lab">
                          {group.threads.filter((t) => t.actief).length} actief
                        </span>
                      )}
                    </button>
                    {group.lab && (
                      <button
                        onClick={() => createThreadForLab(group.lab!.id)}
                        className="shrink-0 text-muted-foreground opacity-0 hover:text-foreground group-hover:opacity-100"
                        title={`Nieuwe chat in ${group.lab.name}`}
                      >
                        <Plus size={14} />
                      </button>
                    )}
                  </div>

                  {open && (
                    <ul className="mt-0.5 space-y-1 pl-2">
                      {group.threads.length === 0 && (
                        <li className="px-2 py-1 text-xs text-muted-foreground">
                          {toonArchief ? "Niets in het archief" : "Nog geen chats"}
                        </li>
                      )}
                      {group.threads.map((t) => (
                        <li
                          key={t.id}
                          onClick={() => openThread(t)}
                          className={`group flex cursor-pointer items-center gap-1 rounded px-2 py-1.5 text-sm ${
                            activeThread?.id === t.id ? "bg-primary/10" : "hover:bg-secondary"
                          }`}
                        >
                          {/* Draait er nu iets? Die vraag stel je bij het scannen
                              van de lijst, dus staat het antwoord vóór de titel
                              en niet erachter — daar zou het wegvallen bij een
                              lange naam. */}
                          <span
                            className={`size-1.5 shrink-0 rounded-full ${
                              t.actief ? "animate-pulse bg-success" : "bg-transparent"
                            }`}
                            title={t.actief ? "Er loopt nu een beurt in deze chat" : undefined}
                          />
                          {/* Titel met de datum eronder. Een lijst van veertig
                              chats met alleen titels zegt niets over wanneer
                              je ergens mee bezig was, en dat is juist waarop
                              je zoekt. */}
                          <span className="flex min-w-0 flex-1 flex-col">
                            <span className="truncate">{t.title}</span>
                            <span className="text-[11px] text-muted-foreground">
                              {korteDatum(t.updated_at || t.created_at)}
                            </span>
                          </span>
                          {t.source === "board" && (
                            <span className="shrink-0 rounded bg-secondary px-1 text-[10px] text-muted-foreground"
                                  title="Sessie van een agent-run op een ticket">
                              ticket
                            </span>
                          )}
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              setRenaming(t);
                            }}
                            className="shrink-0 text-muted-foreground opacity-0 hover:text-foreground focus:opacity-100 group-hover:opacity-100"
                            title="Naam wijzigen"
                          >
                            <Pencil size={13} />
                          </button>
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              archiveThread(t);
                            }}
                            className="shrink-0 text-muted-foreground opacity-0 hover:text-foreground focus:opacity-100 group-hover:opacity-100"
                            title={t.archived_at
                              ? "Terughalen naar je chats"
                              : "Archiveren — uit de lijst, maar blijft te openen"}
                          >
                            {t.archived_at ? <ArchiveRestore size={13} /> : <Archive size={13} />}
                          </button>
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              removeThread(t);
                            }}
                            className="shrink-0 text-muted-foreground opacity-0 hover:text-destructive focus:opacity-100 group-hover:opacity-100"
                            title="Verwijderen"
                          >
                            <Trash2 size={13} />
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
              );
            })}
          </div>
        )}
        </div>

        {/* Hoeveel chats zie je eigenlijk? Onderin, zoals in ND3X: het is geen
            knop, het is het antwoord op "heb ik alles?" -- zeker als er een
            filter aan staat. */}
        <div className="shrink-0 border-t border-border px-3 py-2 text-[11px] text-muted-foreground">
          {zichtbareChats} {zichtbareChats === 1 ? "chat" : "chats"}
          {zoekterm.trim() && ` van ${threads.length}`}
          {toonArchief && " in het archief"}
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        {!activeThread ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-3 p-4">
            <EmptyState>
              <span className="hidden lg:inline">Kies een lab hiernaast om een nieuwe chat te starten.</span>
              <span className="lg:hidden">Open je chats om er een te kiezen, of start een nieuwe.</span>
            </EmptyState>
            <Button variant="secondary" className="lg:hidden" onClick={() => setLijstOpen(true)}>
              <PanelLeft size={15} /> Chats openen
            </Button>
          </div>
        ) : (
          <>
            {/* De balk was één rij waarin titel, lab, model, effort en het
                paneelknopje om dezelfde ruimte vochten en bij elke smalle
                breedte omklapten. Nu: de titel groot, en wát er onder de motor
                zit als kleine labels eronder -- dat lees je als bijschrift en
                niet als even belangrijk als de titel. */}
            <div className="flex items-start gap-2 border-b border-border px-3 py-2 sm:px-4">
              <button type="button" onClick={() => setLijstOpen(true)}
                      className="-ml-1 mt-0.5 flex h-9 shrink-0 items-center gap-1.5 rounded-md border border-border px-2 text-xs font-medium text-muted-foreground hover:text-foreground lg:hidden">
                <PanelLeft size={15} /> Chats
              </button>

              <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                {/* truncate werkt op de TEKST, niet op een flex-container:
                    stond het op de span eromheen, dan liep een lange
                    tickettitel gewoon door. Potlood blijft op desktop, waar
                    hover bestaat. */}
                <span className="group flex min-w-0 items-center gap-1">
                  <span className="truncate text-sm font-semibold">{activeThread.title}</span>
                  <button
                    onClick={() => setRenaming(activeThread)}
                    className="hidden shrink-0 text-muted-foreground opacity-0 hover:text-foreground group-hover:opacity-100 lg:inline-flex"
                    title="Naam wijzigen"
                  >
                    <Pencil size={13} />
                  </button>
                </span>

                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
                  {lab && (
                    <span className="flex items-center gap-1" title={`Lab ${lab.name} — ${lab.status}`}>
                      <span className={`size-1.5 rounded-full ${
                        lab.status === "running" ? "bg-success" : "bg-destructive"}`} />
                      <span className="truncate">{lab.name}</span>
                    </span>
                  )}
                  {lab && (
                    <span className="flex items-center gap-1">
                      {/* Borderloos: een select die eruitziet als een invulveld
                          trekt in een bijschriftregel te veel aandacht. */}
                      <select
                        value={activeThread.model || ""}
                        onChange={(e) => setThreadModel(activeThread.id, e.target.value || null)}
                        className="max-w-[9rem] cursor-pointer rounded border border-transparent bg-transparent py-0.5 pl-1 pr-4 text-[11px] text-foreground hover:border-input focus:border-input focus:outline-none"
                        title="Model voor deze chat — of typ /model <naam> in het bericht"
                      >
                        {MODEL_OPTIONS.map((m) => (
                          <option key={m.value} value={m.value}>{m.label}</option>
                        ))}
                      </select>
                      <button
                        onClick={() => pinAsDefault("model", activeThread.model)}
                        title="Maak dit het standaardmodel voor nieuwe chats"
                        className="hidden text-muted-foreground hover:text-foreground sm:inline-flex"
                      >
                        <Pin size={12} />
                      </button>
                    </span>
                  )}
                  {lab && (
                    <span className="flex items-center gap-1">
                      <select
                        value={activeThread.effort || ""}
                        onChange={(e) => setThreadEffort(activeThread.id, e.target.value || null)}
                        className="max-w-[9rem] cursor-pointer rounded border border-transparent bg-transparent py-0.5 pl-1 pr-4 text-[11px] text-foreground hover:border-input focus:border-input focus:outline-none"
                        title="Reasoning effort (hoeveelheid denkwerk) voor deze chat — of typ /effort <niveau> in het bericht"
                      >
                        {EFFORT_OPTIONS.map((o) => (
                          <option key={o.value} value={o.value}>{o.label}</option>
                        ))}
                      </select>
                      <button
                        onClick={() => pinAsDefault("effort", activeThread.effort)}
                        title="Maak dit de standaard-effort voor nieuwe chats"
                        className="hidden text-muted-foreground hover:text-foreground sm:inline-flex"
                      >
                        <Pin size={12} />
                      </button>
                    </span>
                  )}
                  {activeThread.archived_at && (
                    <span className="flex items-center gap-1">
                      <Archive size={11} /> gearchiveerd
                      <button
                        onClick={() => archiveThread(activeThread)}
                        className="font-semibold text-primary hover:underline"
                        title="Terug naar je chats"
                      >
                        terughalen
                      </button>
                    </span>
                  )}
                  {lab && lab.status !== "running" && (
                    <span className="text-destructive">Start dit lab om te kunnen chatten.</span>
                  )}
                </div>
              </div>

              {lab && (
                <button
                  onClick={() => setSidePanelOpen((v) => !v)}
                  className="mt-0.5 flex shrink-0 items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                  title={sidePanelOpen ? "Zijpaneel verbergen" : "Zijpaneel tonen (lab-beheer & achtergrondtaken)"}
                >
                  <PanelRight size={14} /> {sidePanelOpen ? "" : "Paneel"}
                </button>
              )}
            </div>
            <div ref={scrollRef} onScroll={handleChatScroll} className="flex-1 space-y-4 overflow-y-auto p-3 sm:p-4">
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
                  {/* Het afbreken van de STREAM stopte alleen het meekijken —
                      de beurt draait server-side door. Deze knop breekt de run
                      zelf af, en sluit hem af als hij alleen nog in de database
                      "running" heet; anders strandt elke volgende beurt op
                      "er loopt al een beurt in dit gesprek". */}
                  <div className="mt-2">
                    <Button variant="danger" className="text-xs" busyLabel="Stoppen…"
                            meldFouten={false} onClick={stopBeurt}>
                      Stoppen
                    </Button>
                  </div>
                </Card>
              )}
            </div>
            {/* Eén regel, zoals een berichtbalk hoort te zijn: paperclip links,
                het veld in het midden, versturen rechts. Het was een blok van
                drie knoppen onder elkaar naast een veld van twee regels hoog --
                dat at een kwart van het scherm op en schreeuwde harder dan het
                gesprek erboven. Het veld groeit mee met wat je typt, tot een
                regel of zeven; daarna scrolt het. */}
            <div className="balk-onder border-t border-border px-3 pt-3">
              <BijlageLijst bijlagen={bijlagen}
                            onVerwijder={(path) =>
                              setBijlagen((prev) => prev.filter((b) => b.path !== path))} />
              <div className="flex items-end gap-2">
                <BijlageKnop
                  labId={activeThread ? lab?.id : null}
                  dir={chatBijlageMap(activeThread?.id || "")}
                  disabled={inputDisabled}
                  alleenIcoon
                  onToegevoegd={(nieuwe) =>
                    setBijlagen((prev) => [
                      ...prev,
                      ...nieuwe.filter((n) => !prev.some((p) => p.path === n.path)),
                    ])}
                />

                <div className="flex min-w-0 flex-1 items-end rounded-lg border border-input bg-background px-3 focus-within:ring-2 focus-within:ring-ring">
                  <textarea
                    ref={invoerRef}
                    rows={1}
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        send();
                      }
                    }}
                    disabled={inputDisabled}
                    placeholder={inputDisabled
                      ? "Koppel en start eerst een lab…"
                      : "Typ een bericht… (Enter = sturen, Shift+Enter = nieuwe regel)"}
                    className="w-full resize-none bg-transparent py-2 text-sm text-foreground
                               outline-none placeholder:text-muted-foreground disabled:opacity-60"
                  />
                </div>

                {/* Achtergrondtaak: hetzelfde bericht, maar je kunt meteen
                    verder. Icoon, want hij is zeldzamer dan Stuur -- maar wel
                    naast Stuur, want je kiest ertussen op het moment van
                    versturen. */}
                <button
                  type="button"
                  onClick={sendBackground}
                  disabled={!activeThread || !lab || lab.status !== "running" || (!input.trim() && !bijlagen.length)}
                  title="Op de achtergrond starten: de chat blijft direct bruikbaar en je volgt de voortgang op het tabblad Taken"
                  aria-label="Op de achtergrond starten"
                  className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-border
                             text-muted-foreground hover:bg-secondary hover:text-foreground disabled:opacity-40"
                >
                  <Bot size={17} />
                </button>

                {streaming ? (
                  <button
                    type="button"
                    onClick={stopBeurt}
                    title="De lopende beurt afbreken"
                    className="flex h-9 shrink-0 items-center gap-1.5 rounded-lg bg-destructive px-3
                               text-sm font-medium text-white hover:opacity-90"
                  >
                    <Square size={14} /> Stop
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={send}
                    disabled={inputDisabled || (!input.trim() && !bijlagen.length)}
                    title="Versturen (Enter)"
                    className="flex h-9 shrink-0 items-center gap-1.5 rounded-lg bg-primary px-3
                               text-sm font-medium text-primary-foreground hover:opacity-90
                               disabled:opacity-40"
                  >
                    <SendHorizontal size={15} /> Stuur
                  </button>
                )}
              </div>
            </div>
          </>
        )}
      </div>

      {activeThread && lab && sidePanelOpen && (
        <aside className="absolute inset-0 z-40 flex flex-col border-l border-border bg-background lg:static lg:z-auto lg:w-80 lg:shrink-0">
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
                          onClick={(e) => {
                            e.stopPropagation();
                            chatApi.cancelBackgroundRun(r.id)
                              .then(() => melding.ok("Achtergrondtaak geannuleerd"))
                              .catch((err) => melding.fout("Annuleren mislukt", String(err)));
                          }}
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

      {renaming && (
        <RenameThreadModal
          thread={renaming}
          onClose={() => setRenaming(null)}
          onRenamed={applyThread}
        />
      )}
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

type ThreadGroup = { id: string; name: string; lab: Lab | null; threads: Thread[] };

/**
 * Hernoemen in een pop-up. Bewust géén opslaan-op-blur: dat maakte van elke
 * klik naast het veld een bevestiging, ook als je het net anders bedacht had.
 * Hier bevestig je met Enter of de knop, en sluiten is annuleren.
 */
function RenameThreadModal({
  thread, onClose, onRenamed,
}: {
  thread: Thread;
  onClose: () => void;
  onRenamed: (t: Thread) => void;
}) {
  const [value, setValue] = useState(thread.title);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    const title = value.trim();
    if (!title || title === thread.title) {
      onClose();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      onRenamed(await chatApi.renameThread(thread.id, title));
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Hernoemen mislukt");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open onClose={onClose} title="Chat hernoemen">
      <Label>Naam</Label>
      <Input
        autoFocus
        value={value}
        maxLength={255}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            submit();
          }
        }}
      />
      {error && <p className="mt-2 text-sm text-destructive">{error}</p>}
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="secondary" onClick={onClose}>
          Annuleren
        </Button>
        <Button onClick={submit} disabled={busy || !value.trim()}>
          {busy ? "Opslaan…" : "Opslaan"}
        </Button>
      </div>
    </Modal>
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

/** Tijd bij een bericht: alleen het uur als het van vandaag is, anders met de
 *  datum erbij. Volledige tijdstempel staat in de tooltip. */
function berichtTijd(waarde: string): string {
  const d = new Date(waarde);
  if (Number.isNaN(d.getTime())) return "";
  const klok = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  const vandaag = new Date();
  if (d.toDateString() === vandaag.toDateString()) return klok;
  return `${d.toLocaleDateString(undefined, { day: "2-digit", month: "2-digit" })} ${klok}`;
}


function ChatBubble({ message }: { message: Message }) {
  const isUser = message.role === "user";
  const visibleSteps = (message.steps || []).filter((s) => (s as any).kind !== "usage");
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div className={`min-w-0 max-w-[92%] overflow-hidden rounded-lg px-3 py-2 text-sm sm:max-w-2xl ${isUser ? "bg-primary text-primary-foreground" : "bg-secondary"}`}>
        {!isUser && visibleSteps.length > 0 && (
          <details className="mb-2 rounded-md border border-border/60 bg-background/40 px-2 py-1 text-xs">
            <summary className="cursor-pointer font-medium text-muted-foreground">
              🧠 Redenering &amp; stappen ({visibleSteps.length})
            </summary>
            <div className="mt-1 max-h-64 space-y-1 overflow-y-auto">
              {visibleSteps.map((s, i) =>
                s.kind === "tool" ? (
                  <div key={i} className="break-all font-mono text-muted-foreground">
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
        {/* Wannéér iets gezegd is, is in een gesprek met een agent geen detail:
            runs lopen uren, een antwoord kan van vanmorgen zijn, en zonder tijd
            is een draad niet te volgen. De datum staat er alleen bij als het
            bericht niet van vandaag is — anders is het ruis. */}
        <div className={`mt-1 text-[10px] ${isUser ? "text-primary-foreground/60" : "text-muted-foreground"}`}
             title={new Date(message.created_at).toLocaleString()}>
          {berichtTijd(message.created_at)}
        </div>
        {!isUser && <UsageFooter steps={message.steps || []} />}
      </div>
    </div>
  );
}
