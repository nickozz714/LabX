/**
 * components/FirstRunWizard.tsx — shown once (per browser) when either
 * Docker isn't reachable yet or no Claude Code oauth token is configured,
 * since neither Labs nor Chat can do anything without both. Reuses the
 * existing GET /api/system/docker (dockerStatus()) and the same
 * settingsApi.update({ oauth_token }) write-only field the Settings page's
 * "Claude Code CLI" card already uses — this is a friendlier front door to
 * the same two facts, not a new mechanism.
 */
import { useEffect, useState } from "react";
import { dockerStatus } from "@/lib/labs";
import { settingsApi } from "@/lib/settings";
import type { AppSettingsDto, DockerStatus } from "@/lib/types";
import { Badge, Button, Card, Input } from "@/components/ui";

export function FirstRunWizard({ onDone }: { onDone: () => void }) {
  const [docker, setDocker] = useState<DockerStatus | null>(null);
  const [settings, setSettings] = useState<AppSettingsDto | null>(null);
  const [token, setToken] = useState("");
  const [saving, setSaving] = useState(false);
  const [checking, setChecking] = useState(false);

  function refresh() {
    setChecking(true);
    Promise.all([dockerStatus(), settingsApi.get()])
      .then(([d, s]) => {
        setDocker(d);
        setSettings(s);
      })
      .finally(() => setChecking(false));
  }
  useEffect(refresh, []);

  if (!docker || !settings) {
    return <div className="flex h-screen items-center justify-center text-sm text-muted-foreground">Laden…</div>;
  }

  const dockerOk = docker.daemon_up;
  const tokenOk = settings.oauth_token_configured;

  async function saveToken() {
    setSaving(true);
    try {
      const updated = await settingsApi.update({ oauth_token: token });
      setSettings(updated);
      setToken("");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex h-screen items-center justify-center bg-background p-6">
      <Card className="w-full max-w-lg space-y-5 p-6">
        <div>
          <h1 className="text-lg font-bold">Welkom bij LabX</h1>
          <p className="text-sm text-muted-foreground">Twee dingen moeten kloppen voordat labs en chat werken.</p>
        </div>

        <div className="space-y-2 rounded-md border border-border p-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold">1. Docker</h2>
            <Badge tone={dockerOk ? "green" : "red"}>{dockerOk ? "beschikbaar" : "niet beschikbaar"}</Badge>
          </div>
          {dockerOk ? (
            <p className="text-xs text-muted-foreground">Docker is gevonden en de daemon draait.</p>
          ) : (
            <>
              <p className="text-xs text-muted-foreground">
                LabX start elk lab als een eigen Docker-container. Installeer Docker Desktop, zorg dat het
                draait, en klik dan op "Opnieuw controleren" hieronder.
                {docker.hint && <> {docker.hint}</>}
              </p>
              <a
                href="https://www.docker.com/products/docker-desktop/"
                target="_blank"
                rel="noreferrer"
                className="inline-block text-xs text-primary hover:underline"
              >
                Docker Desktop downloaden →
              </a>
            </>
          )}
        </div>

        <div className="space-y-2 rounded-md border border-border p-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold">2. Claude Code-toegang</h2>
            <Badge tone={tokenOk ? "green" : "red"}>{tokenOk ? "ingesteld" : "niet ingesteld"}</Badge>
          </div>
          <p className="text-xs text-muted-foreground">
            De chat in LabX draait via de Claude Code CLI, met je bestaande Claude-abonnement — geen los
            API-key nodig. Draai op een machine waar je al bent ingelogd het commando{" "}
            <code>claude setup-token</code>, en plak de uitkomst hieronder.
          </p>
          {tokenOk ? (
            <p className="text-xs text-muted-foreground">Er is al een token ingesteld.</p>
          ) : (
            <div className="flex gap-2">
              <Input
                type="password"
                placeholder="claude setup-token output"
                value={token}
                onChange={(e) => setToken(e.target.value)}
              />
              <Button onClick={saveToken} disabled={!token.trim() || saving}>
                {saving ? "…" : "Opslaan"}
              </Button>
            </div>
          )}
        </div>

        <div className="flex items-center justify-between pt-1">
          <Button variant="ghost" onClick={onDone}>
            Later instellen
          </Button>
          <div className="flex gap-2">
            <Button variant="secondary" onClick={refresh} disabled={checking}>
              {checking ? "…" : "Opnieuw controleren"}
            </Button>
            {dockerOk && tokenOk && <Button onClick={onDone}>Aan de slag</Button>}
          </div>
        </div>
      </Card>
    </div>
  );
}
