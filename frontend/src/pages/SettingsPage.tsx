/**
 * pages/SettingsPage.tsx — Claude Code CLI config + guard/lab defaults. The
 * oauth token is write-only (same pattern as an Azure profile secret).
 */
import { useEffect, useState } from "react";
import { settingsApi } from "@/lib/settings";
import { labsApi } from "@/lib/labs";
import { useAuth } from "@/contexts/AuthContext";
import { api, ApiError } from "@/lib/api";
import type { AppSettingsDto, GuardModelStatus } from "@/lib/types";
import { Badge, Button, Card, Input, Label, TextArea, Toggle } from "@/components/ui";

export function SettingsPage() {
  const [settings, setSettings] = useState<AppSettingsDto | null>(null);
  const [oauthToken, setOauthToken] = useState("");
  const [extraArgsText, setExtraArgsText] = useState("");
  const [saved, setSaved] = useState(false);
  const [guardStatus, setGuardStatus] = useState<GuardModelStatus | null>(null);

  useEffect(() => {
    settingsApi.get().then((s) => {
      setSettings(s);
      setExtraArgsText((s.extra_args || []).join(" "));
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    const poll = () => labsApi.guardModelStatus().then((s) => !cancelled && setGuardStatus(s)).catch(() => {});
    poll();
    const t = setInterval(poll, 4000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, []);

  if (!settings) return <div className="p-6 text-sm text-muted-foreground">Laden…</div>;

  async function save(patch: Partial<AppSettingsDto> & { oauth_token?: string }) {
    const updated = await settingsApi.update({ ...settings, ...patch });
    setSettings(updated);
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  }

  return (
    <div className="mx-auto max-w-2xl p-6 space-y-6">
      <h1 className="text-xl font-bold">Instellingen</h1>

      <Card className="p-4 space-y-3">
        <h2 className="text-sm font-semibold">Claude Code CLI — globaal (infrastructuur)</h2>
        <p className="text-xs text-muted-foreground">
          Geldt voor élke chat en elk lab — dit is geen per-gesprek instelling. Het standaard
          model en de standaard reasoning effort voor NIEUWE chats stel je in op de Chat-pagina
          zelf (dropdown of <code>/model</code>/<code>/effort</code> + de pin-knop) — niet hier.
        </p>
        <div>
          <Label>CLI-pad</Label>
          <Input value={settings.cli_path} onChange={(e) => setSettings({ ...settings, cli_path: e.target.value })} onBlur={() => save({ cli_path: settings.cli_path })} />
        </div>
        <div>
          <Label>OAuth-token ({settings.oauth_token_configured ? "ingesteld" : "niet ingesteld"})</Label>
          <div className="flex gap-2">
            <Input type="password" placeholder="claude setup-token output" value={oauthToken} onChange={(e) => setOauthToken(e.target.value)} />
            <Button variant="secondary" onClick={() => save({ oauth_token: oauthToken }).then(() => setOauthToken(""))}>
              Opslaan
            </Button>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label>Max turns</Label>
            <Input type="number" value={settings.max_turns} onChange={(e) => setSettings({ ...settings, max_turns: Number(e.target.value) })} onBlur={() => save({ max_turns: settings.max_turns })} />
          </div>
          <div>
            <Label>Timeout (seconden, leeg = 7200s standaard)</Label>
            <Input
              type="number"
              value={settings.timeout_seconds ?? ""}
              onChange={(e) => setSettings({ ...settings, timeout_seconds: e.target.value ? Number(e.target.value) : null })}
              onBlur={() => save({ timeout_seconds: settings.timeout_seconds })}
            />
          </div>
        </div>
        <div>
          <Label>Extra CLI-argumenten (spatie-gescheiden)</Label>
          <Input
            value={extraArgsText}
            onChange={(e) => setExtraArgsText(e.target.value)}
            onBlur={() => save({ extra_args: extraArgsText.split(/\s+/).filter(Boolean) })}
            placeholder="--verbose"
          />
        </div>
        <Toggle checked={settings.enable_tool_search} onChange={(v) => save({ enable_tool_search: v })} label="Tool-search aan (issue 2: schema's on demand i.p.v. altijd in context)" />

        <details className="rounded-md border border-border p-3 text-sm">
          <summary className="cursor-pointer font-medium text-muted-foreground">Geavanceerd (Claude Code CLI-features)</summary>
          <div className="mt-2 space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>Fallback-model(len) (komma-gescheiden, optioneel)</Label>
                <Input
                  value={settings.fallback_model || ""}
                  onChange={(e) => setSettings({ ...settings, fallback_model: e.target.value })}
                  onBlur={() => save({ fallback_model: settings.fallback_model || null })}
                  placeholder="sonnet,haiku"
                />
              </div>
              <div>
                <Label>Standaard subagent (optioneel — "--agent")</Label>
                <Input
                  value={settings.default_agent || ""}
                  onChange={(e) => setSettings({ ...settings, default_agent: e.target.value })}
                  onBlur={() => save({ default_agent: settings.default_agent || null })}
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>Max budget per run (USD, optioneel)</Label>
                <Input
                  type="number" step="0.5"
                  value={settings.max_budget_usd ?? ""}
                  onChange={(e) => setSettings({ ...settings, max_budget_usd: e.target.value ? Number(e.target.value) : null })}
                  onBlur={() => save({ max_budget_usd: settings.max_budget_usd })}
                />
              </div>
              <div>
                <Label>Autocompact (leeg, "auto", of een tokenaantal)</Label>
                <Input
                  value={settings.autocompact || ""}
                  onChange={(e) => setSettings({ ...settings, autocompact: e.target.value })}
                  onBlur={() => save({ autocompact: settings.autocompact || null })}
                  placeholder="auto of bv. 200000"
                />
              </div>
            </div>
            <div>
              <Label>Custom agents (JSON, optioneel — "--agents")</Label>
              <TextArea
                rows={3}
                value={settings.custom_agents_json || ""}
                onChange={(e) => setSettings({ ...settings, custom_agents_json: e.target.value })}
                onBlur={() => save({ custom_agents_json: settings.custom_agents_json || null })}
                placeholder='{"reviewer": {"description": "Reviews code", "prompt": "You are a code reviewer"}}'
              />
            </div>
          </div>
        </details>
        <button
          onClick={() => {
            localStorage.removeItem("labx_wizard_dismissed");
            window.location.reload();
          }}
          className="text-xs text-primary hover:underline"
        >
          Open de eerste-keer-wizard opnieuw (Docker-check + Claude-token)
        </button>
      </Card>

      <Card className="p-4 space-y-3">
        <h2 className="text-sm font-semibold">Data-guard standaarden</h2>
        <Toggle checked={settings.data_guard_default} onChange={(v) => save({ data_guard_default: v })} label="Data-egress-guard standaard aan voor nieuwe labs" />
        <Toggle checked={settings.llm_guard_default} onChange={(v) => save({ llm_guard_default: v })} label="Lokaal guard-model (spoor B) standaard aan" />
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label>Ollama-URL (leeg = auto: host.docker.internal in een container)</Label>
            <Input value={settings.guard_llm_url} onChange={(e) => setSettings({ ...settings, guard_llm_url: e.target.value })} onBlur={() => save({ guard_llm_url: settings.guard_llm_url })} placeholder="http://ollama:11434" />
          </div>
          <div>
            <Label>Guard-model</Label>
            <Input value={settings.guard_llm_model} onChange={(e) => setSettings({ ...settings, guard_llm_model: e.target.value })} onBlur={() => save({ guard_llm_model: settings.guard_llm_model })} />
          </div>
        </div>
        {guardStatus && (
          <div className="rounded-md border border-border p-3 text-xs">
            <div className="flex items-center gap-2">
              <span className="font-medium">Live status:</span>
              <Badge tone={guardStatus.state === "ready" ? "green" : guardStatus.state === "disabled" ? "neutral" : "yellow"}>
                {guardStatus.state}
              </Badge>
              <span className="text-muted-foreground">{guardStatus.model} @ {guardStatus.url}</span>
              {guardStatus.state !== "ready" && (
                <Button variant="ghost" className="ml-auto px-2 py-0.5" onClick={() => labsApi.guardModelEnsure().then(setGuardStatus)}>
                  Nu ophalen
                </Button>
              )}
            </div>
            {guardStatus.hint && <p className="mt-1 text-muted-foreground">{guardStatus.hint}</p>}
            {guardStatus.state === "unreachable" && (
              <p className="mt-1 text-muted-foreground">
                Ollama zelf start niet mee met LabX — start het op de host (<code>ollama serve</code>,
                of de Ollama-app) of gebruik de meegeleverde sidecar:{" "}
                <code>docker compose --profile with-ollama up -d</code> en zet de URL hierboven op{" "}
                <code>http://ollama:11434</code>.
              </p>
            )}
          </div>
        )}
      </Card>

      <Card className="p-4 space-y-3">
        <h2 className="text-sm font-semibold">Automatische hooks (elke chat-beurt)</h2>
        <p className="text-xs text-muted-foreground">
          Roep vóór elke chat-beurt automatisch één of meer tools aan en geef de resultaten mee
          aan de agent — de agent hoeft er dan niet zelf aan te denken. Werkt met elke tool die
          een <code>query</code>-argument accepteert (bv. <code>hive_recall</code>). Elke
          uitgevoerde hook is zichtbaar als ⚙️-stap in de chat.
        </p>
        {(settings.auto_hooks || []).map((h, i) => (
          <div key={i} className="space-y-2 rounded-md border border-border p-3">
            <div className="flex items-center gap-2">
              <Toggle
                checked={h.enabled}
                onChange={(v) => {
                  const hooks = [...settings.auto_hooks];
                  hooks[i] = { ...h, enabled: v };
                  save({ auto_hooks: hooks });
                }}
                label={h.enabled ? "actief" : "uit"}
              />
              <Button
                variant="danger"
                className="ml-auto px-2 py-0.5 text-xs"
                onClick={() => save({ auto_hooks: settings.auto_hooks.filter((_, j) => j !== i) })}
              >
                Verwijderen
              </Button>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <Label>Tool-naam</Label>
                <Input
                  value={h.tool_name}
                  onChange={(e) => {
                    const hooks = [...settings.auto_hooks];
                    hooks[i] = { ...h, tool_name: e.target.value };
                    setSettings({ ...settings, auto_hooks: hooks });
                  }}
                  onBlur={() => save({ auto_hooks: settings.auto_hooks })}
                  placeholder="hive_recall"
                />
              </div>
              <div>
                <Label>Query (leeg = laatste gebruikersbericht)</Label>
                <Input
                  value={h.query_template || ""}
                  onChange={(e) => {
                    const hooks = [...settings.auto_hooks];
                    hooks[i] = { ...h, query_template: e.target.value || null };
                    setSettings({ ...settings, auto_hooks: hooks });
                  }}
                  onBlur={() => save({ auto_hooks: settings.auto_hooks })}
                  placeholder="(optioneel) vaste zoekopdracht"
                />
              </div>
            </div>
            <div>
              <Label>Instructie voor de agent (optioneel, na het resultaat)</Label>
              <TextArea
                rows={2}
                value={h.instruction || ""}
                onChange={(e) => {
                  const hooks = [...settings.auto_hooks];
                  hooks[i] = { ...h, instruction: e.target.value || null };
                  setSettings({ ...settings, auto_hooks: hooks });
                }}
                onBlur={() => save({ auto_hooks: settings.auto_hooks })}
                placeholder="Gebruik deze context alleen als die relevant is voor de vraag."
              />
            </div>
          </div>
        ))}
        <Button
          variant="secondary"
          onClick={() =>
            save({
              auto_hooks: [...(settings.auto_hooks || []),
                { tool_name: "", query_template: null, instruction: null, enabled: true }],
            })
          }
        >
          + Hook toevoegen
        </Button>
      </Card>

      <Card className="p-4 space-y-3">
        <h2 className="text-sm font-semibold">Lab-standaarden</h2>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label>Standaard image</Label>
            <Input value={settings.default_image} onChange={(e) => setSettings({ ...settings, default_image: e.target.value })} onBlur={() => save({ default_image: settings.default_image })} />
          </div>
          <div>
            <Label>Standaard TTL (uur)</Label>
            <Input type="number" value={settings.default_ttl_hours} onChange={(e) => setSettings({ ...settings, default_ttl_hours: Number(e.target.value) })} onBlur={() => save({ default_ttl_hours: settings.default_ttl_hours })} />
          </div>
        </div>
      </Card>

      <AccountCard />

      {saved && <p className="text-sm text-success">Opgeslagen.</p>}
    </div>
  );
}

function AccountCard() {
  const { adoptSession } = useAuth();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newUsername, setNewUsername] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function submit() {
    setMsg(null);
    setErr(null);
    try {
      const res = await api.put<{ access_token: string; username: string }>("/auth/credentials", {
        current_password: currentPassword,
        username: newUsername,
        password: newPassword,
      });
      adoptSession(res.access_token, res.username);
      setMsg("Account bijgewerkt — je sessie is automatisch vernieuwd.");
      setCurrentPassword("");
      setNewUsername("");
      setNewPassword("");
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Bijwerken mislukt");
    }
  }

  return (
    <Card className="p-4 space-y-3">
      <h2 className="text-sm font-semibold">Account</h2>
      <p className="text-xs text-muted-foreground">
        Wijzig de gebruikersnaam en/of het wachtwoord van het beheerdersaccount. Je huidige
        wachtwoord is verplicht ter bevestiging.
      </p>
      <div className="grid grid-cols-3 gap-2">
        <div>
          <Label>Huidig wachtwoord</Label>
          <Input type="password" value={currentPassword} onChange={(e) => setCurrentPassword(e.target.value)} />
        </div>
        <div>
          <Label>Nieuwe gebruikersnaam</Label>
          <Input value={newUsername} onChange={(e) => setNewUsername(e.target.value)} placeholder="admin" />
        </div>
        <div>
          <Label>Nieuw wachtwoord (min. 8 tekens)</Label>
          <Input type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} />
        </div>
      </div>
      {err && <p className="text-sm text-destructive">{err}</p>}
      {msg && <p className="text-sm text-success">{msg}</p>}
      <Button variant="secondary" onClick={submit} disabled={!currentPassword || !newUsername || newPassword.length < 8}>
        Account bijwerken
      </Button>
    </Card>
  );
}
