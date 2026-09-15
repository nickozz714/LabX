/**
 * components/NotificationsCard.tsx — meldingen instellen.
 *
 * Twee dingen die de vorm van dit scherm bepalen:
 *
 * 1. **Antwoorden werken zonder tunnel.** LabX staat op een prive server zonder
 *    domein, dus een provider kan hier niets afleveren. Beide kanalen HALEN
 *    daarom hun antwoorden op — Telegram met getUpdates, mail met IMAP. Voor de
 *    gebruiker betekent dat: vul de inbox-velden in, anders is het eenrichting.
 *    Het scherm zegt dat ook, want een kanaal dat wél meldt maar niet
 *    terugluistert ziet er verder identiek uit.
 * 2. **Een bot mag niemand als eerste aanschrijven.** Daarom is er een knop
 *    "Chats ophalen" in plaats van een veld waarin je je gebruikersnaam typt:
 *    jij stuurt de bot 'hoi', en dan pas bestaat het chat-id.
 */
import { useEffect, useState } from "react";
import { Bell, RefreshCw, Send, Trash2 } from "lucide-react";
import { ApiError } from "@/lib/api";
import { notifyApi, type MeldGebeurtenis, type MeldKanaal } from "@/lib/notify";
import { Badge, Button, Card, Input, Label, Select, Toggle } from "@/components/ui";
import { useBevestiging } from "@/components/Bevestiging";

const LEEG_EMAIL = {
  smtp_host: "", smtp_port: 587, smtp_user: "", smtp_tls: "starttls",
  from: "", to: "", imap_host: "", imap_port: 993, imap_user: "",
  imap_folder: "INBOX", imap_ssl: true,
};

function tijd(waarde: string | null): string {
  if (!waarde) return "nooit";
  return new Date(waarde).toLocaleString(undefined, {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function NotificationsCard() {
  const [kanalen, setKanalen] = useState<MeldKanaal[]>([]);
  const [gebeurtenissen, setGebeurtenissen] = useState<MeldGebeurtenis[]>([]);
  const [nieuw, setNieuw] = useState<"email" | "telegram" | null>(null);
  const [fout, setFout] = useState<string | null>(null);

  async function laad() {
    setKanalen(await notifyApi.channels());
  }

  useEffect(() => {
    notifyApi.events().then(setGebeurtenissen);
    laad();
  }, []);

  async function maak(kind: "email" | "telegram") {
    setFout(null);
    try {
      await notifyApi.create({
        kind, name: kind === "email" ? "Mail" : "Telegram",
        config: kind === "email" ? LEEG_EMAIL : { chat_id: "" },
        events: [], allow_reply: true, enabled: false,
      });
      setNieuw(null);
      await laad();
    } catch (e) {
      setFout(e instanceof ApiError ? e.message : "Aanmaken mislukt");
    }
  }

  return (
    <Card className="p-4 space-y-3">
      <h2 className="flex items-center gap-2 text-sm font-semibold">
        <Bell size={14} /> Meldingen
      </h2>
      <p className="text-xs text-muted-foreground">
        Krijg bericht als een agent klaar is, vastloopt of iets van je nodig heeft — met de
        samenvatting erin. <strong>Antwoorden kan ook</strong>: wat je terugstuurt wordt de
        volgende beurt in dezelfde sessie, alsof je het in de chat had getypt. Beide kanalen
        halen hun antwoorden zelf op (Telegram via getUpdates, mail via IMAP), dus er hoeft
        niets van buiten bij deze server te kunnen.
      </p>
      {fout && <p className="text-sm text-destructive">{fout}</p>}

      <div className="space-y-2">
        {kanalen.map((k) => (
          <KanaalRegel key={k.id} kanaal={k} gebeurtenissen={gebeurtenissen}
                       onGewijzigd={laad} />
        ))}
        {kanalen.length === 0 && (
          <p className="rounded border border-dashed border-border p-3 text-xs text-muted-foreground">
            Nog geen kanaal. Telegram is het snelst opgezet: maak een bot bij @BotFather,
            stuur hem één bericht, en plak het token hieronder.
          </p>
        )}
      </div>

      <div className="flex items-center gap-2">
        <Select value={nieuw || ""} onChange={(e) => setNieuw((e.target.value || null) as any)}>
          <option value="">Kanaal toevoegen…</option>
          <option value="telegram">Telegram</option>
          <option value="email">Mail</option>
        </Select>
        {nieuw && <Button className="text-xs" onClick={() => maak(nieuw)}>Aanmaken</Button>}
      </div>
    </Card>
  );
}

function KanaalRegel({ kanaal, gebeurtenissen, onGewijzigd }: {
  kanaal: MeldKanaal;
  gebeurtenissen: MeldGebeurtenis[];
  onGewijzigd: () => void;
}) {
  const bevestig = useBevestiging();
  const [open, setOpen] = useState(false);
  const [config, setConfig] = useState<Record<string, any>>({ ...kanaal.config });
  const [geheim, setGeheim] = useState("");
  const [naam, setNaam] = useState(kanaal.name);
  const [events, setEvents] = useState<string[]>(kanaal.events);
  const [antwoord, setAntwoord] = useState(kanaal.allow_reply);
  const [bezig, setBezig] = useState(false);
  const [melding, setMelding] = useState<string | null>(null);
  const [chats, setChats] = useState<{ chat_id: string; naam: string }[] | null>(null);

  async function bewaar(extra: Record<string, unknown> = {}) {
    setBezig(true);
    setMelding(null);
    try {
      await notifyApi.update(kanaal.id, {
        name: naam, config, events, allow_reply: antwoord,
        ...(geheim ? { secret: geheim } : {}),
        ...extra,
      });
      setGeheim("");
      onGewijzigd();
      setMelding("Opgeslagen.");
    } catch (e) {
      setMelding(e instanceof ApiError ? e.message : "Opslaan mislukt");
    } finally {
      setBezig(false);
    }
  }

  async function test(send: boolean) {
    setBezig(true);
    setMelding(null);
    try {
      if (geheim || naam !== kanaal.name) await bewaar();
      const r = await notifyApi.test(kanaal.id, send);
      setMelding(r.ok
        ? (send ? "Verbonden en testbericht verstuurd — antwoord erop om de weg terug te testen."
                : `Verbonden${r.bot ? ` met @${r.bot}` : ""}.`)
        : `Niet gelukt: ${JSON.stringify(r)}`);
      onGewijzigd();
    } catch (e) {
      setMelding(e instanceof ApiError ? e.message : "Test mislukt");
    } finally {
      setBezig(false);
    }
  }

  async function haalChats() {
    setBezig(true);
    setMelding(null);
    try {
      if (geheim) await bewaar();
      const r = await notifyApi.telegramChats({ channel_id: kanaal.id });
      setChats(r.chats);
      if (!r.chats.length) setMelding(r.hint);
    } catch (e) {
      setMelding(e instanceof ApiError ? e.message : "Chats ophalen mislukt");
    } finally {
      setBezig(false);
    }
  }

  const kanTerugpraten = kanaal.kind === "telegram"
    ? true
    : Boolean(config.imap_host);

  return (
    <div className="rounded-md border border-border">
      <div className="flex flex-wrap items-center gap-2 p-2">
        <Toggle checked={kanaal.enabled}
                onChange={(v) => notifyApi.update(kanaal.id, { enabled: v }).then(onGewijzigd)} />
        <button className="flex-1 text-left text-sm" onClick={() => setOpen(!open)}>
          {kanaal.name}
          <span className="ml-2 text-xs text-muted-foreground">{kanaal.kind}</span>
        </button>
        {!kanaal.has_secret && <Badge tone="yellow">geen token</Badge>}
        {kanaal.enabled && kanaal.allow_reply && !kanTerugpraten && (
          <Badge tone="yellow">alleen heen</Badge>
        )}
        {kanaal.last_error && <Badge tone="red">fout</Badge>}
        <span className="text-[11px] text-muted-foreground">
          laatst verstuurd {tijd(kanaal.last_sent_at)}
        </span>
      </div>

      {kanaal.last_error && (
        <p className="px-2 pb-2 text-xs text-destructive">{kanaal.last_error}</p>
      )}

      {open && (
        <div className="space-y-3 border-t border-border p-3">
          <div>
            <Label>Naam</Label>
            <Input value={naam} onChange={(e) => setNaam(e.target.value)} />
          </div>

          {kanaal.kind === "telegram" ? (
            <>
              <div>
                <Label>Bot-token</Label>
                <Input type="password" value={geheim} onChange={(e) => setGeheim(e.target.value)}
                       placeholder={kanaal.has_secret ? "opgeslagen — leeg laten om te behouden"
                                                      : "van @BotFather"} />
              </div>
              <div>
                <Label>Chat</Label>
                <div className="flex gap-2">
                  <Input value={String(config.chat_id || "")}
                         onChange={(e) => setConfig({ ...config, chat_id: e.target.value })}
                         placeholder="chat-id" />
                  <Button variant="secondary" className="text-xs" disabled={bezig}
                          onClick={haalChats}>Chats ophalen</Button>
                </div>
                <p className="mt-1 text-[11px] text-muted-foreground">
                  Een bot kan niemand als eerste aanschrijven. Stuur de bot in Telegram eerst
                  zelf een bericht; dan verschijnt hij hier.
                </p>
                {chats && chats.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {chats.map((c) => (
                      <button key={c.chat_id} className="rounded bg-secondary px-2 py-0.5 text-[11px]"
                              onClick={() => setConfig({ ...config, chat_id: c.chat_id })}>
                        {c.naam} ({c.chat_id})
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-2">
                <div><Label>SMTP-server</Label>
                  <Input value={String(config.smtp_host || "")}
                         onChange={(e) => setConfig({ ...config, smtp_host: e.target.value })} /></div>
                <div><Label>Poort</Label>
                  <Input type="number" value={String(config.smtp_port ?? 587)}
                         onChange={(e) => setConfig({ ...config, smtp_port: Number(e.target.value) })} /></div>
                <div><Label>Gebruiker</Label>
                  <Input value={String(config.smtp_user || "")}
                         onChange={(e) => setConfig({ ...config, smtp_user: e.target.value })} /></div>
                <div><Label>Beveiliging</Label>
                  <Select value={String(config.smtp_tls || "starttls")}
                          onChange={(e) => setConfig({ ...config, smtp_tls: e.target.value })}>
                    <option value="starttls">STARTTLS</option>
                    <option value="ssl">SSL</option>
                    <option value="none">geen</option>
                  </Select></div>
                <div><Label>Afzender</Label>
                  <Input value={String(config.from || "")}
                         onChange={(e) => setConfig({ ...config, from: e.target.value })} /></div>
                <div><Label>Naar</Label>
                  <Input value={String(config.to || "")}
                         onChange={(e) => setConfig({ ...config, to: e.target.value })}
                         placeholder="meerdere met komma's" /></div>
              </div>
              <div>
                <Label>Wachtwoord</Label>
                <Input type="password" value={geheim} onChange={(e) => setGeheim(e.target.value)}
                       placeholder={kanaal.has_secret ? "opgeslagen — leeg laten om te behouden" : ""} />
              </div>
              <div className="rounded border border-border p-2">
                <p className="mb-2 text-[11px] text-muted-foreground">
                  <strong>Antwoorden ontvangen (IMAP).</strong> Zonder deze velden is dit kanaal
                  eenrichting: je krijgt meldingen, maar wat je terugmailt komt nergens aan.
                  Meestal dezelfde mailbox als hierboven.
                </p>
                <div className="grid grid-cols-2 gap-2">
                  <div><Label>IMAP-server</Label>
                    <Input value={String(config.imap_host || "")}
                           onChange={(e) => setConfig({ ...config, imap_host: e.target.value })} /></div>
                  <div><Label>Poort</Label>
                    <Input type="number" value={String(config.imap_port ?? 993)}
                           onChange={(e) => setConfig({ ...config, imap_port: Number(e.target.value) })} /></div>
                  <div><Label>Gebruiker</Label>
                    <Input value={String(config.imap_user || "")}
                           onChange={(e) => setConfig({ ...config, imap_user: e.target.value })}
                           placeholder="leeg = zelfde als SMTP" /></div>
                  <div><Label>Map</Label>
                    <Input value={String(config.imap_folder || "INBOX")}
                           onChange={(e) => setConfig({ ...config, imap_folder: e.target.value })} /></div>
                </div>
              </div>
            </>
          )}

          <div>
            <Label>Waarover melden</Label>
            <div className="flex flex-wrap gap-1">
              {gebeurtenissen.map((g) => {
                const aan = events.includes(g.key);
                return (
                  <button key={g.key} title={g.label}
                          className={`rounded px-2 py-0.5 text-[11px] ${aan ? "bg-primary text-primary-foreground" : "bg-secondary"}`}
                          onClick={() => setEvents(aan ? events.filter((x) => x !== g.key)
                                                       : [...events, g.key])}>
                    {g.label}
                  </button>
                );
              })}
            </div>
            {events.length === 0 && (
              <p className="mt-1 text-[11px] text-muted-foreground">Niets aangevinkt = alles.</p>
            )}
          </div>

          <Toggle checked={antwoord} onChange={setAntwoord}
                  label="Antwoorden gaan terug de sessie in" />

          <div className="flex flex-wrap items-center gap-2">
            <Button className="text-xs" disabled={bezig} onClick={() => bewaar()}>Opslaan</Button>
            <Button variant="secondary" className="text-xs" disabled={bezig}
                    onClick={() => test(false)}>
              <RefreshCw size={12} /> Verbinding testen
            </Button>
            <Button variant="secondary" className="text-xs" disabled={bezig}
                    onClick={() => test(true)}>
              <Send size={12} /> Testbericht sturen
            </Button>
            <Button variant="ghost" className="ml-auto text-xs text-destructive" disabled={bezig}
                    onClick={async () => {
                      const ja = await bevestig.vraag({
                        titel: `Kanaal '${kanaal.name}' verwijderen?`,
                        tekst: "Er gaan daarna geen meldingen meer via dit kanaal.",
                        bevestig: "Verwijderen",
                      });
                      if (ja) await notifyApi.remove(kanaal.id).then(onGewijzigd);
                    }}>
              <Trash2 size={12} />
            </Button>
          </div>
          {melding && <p className="text-xs text-muted-foreground">{melding}</p>}
        </div>
      )}
    </div>
  );
}
