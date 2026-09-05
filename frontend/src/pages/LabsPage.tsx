/**
 * pages/LabsPage.tsx
 *
 * Ported in spirit from ND3X's LabsTile.tsx: list + create dialog + detail
 * panel (settings, guard toggles with live model status, file browser,
 * one-shot exec, interactive terminal, guard-audit). The Docker diagnostic
 * banner is the direct fix for issue 1 ("geen Docker aanwezig").
 */
import { useEffect, useRef, useState } from "react";
import { dockerStatus, labsApi } from "@/lib/labs";
import type { DockerStatus, GuardModelStatus, ImagePreset, Lab, LabExtra } from "@/lib/types";
import { Badge, Button, Card, EmptyState, Input, Label, Modal, TextArea, Toggle } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { LabTerminal } from "@/components/LabTerminal";
import { LabAllowlist } from "@/components/LabAllowlist";
import { AzureProfilePicker } from "@/components/AzureProfilePicker";
import { getToken } from "@/lib/api";

function statusTone(status: Lab["status"]) {
  return { creating: "yellow", running: "green", stopped: "neutral", error: "red", expired: "neutral" }[status] as any;
}

export function LabsPage() {
  const [labs, setLabs] = useState<Lab[]>([]);
  const [docker, setDocker] = useState<DockerStatus | null>(null);
  const [selected, setSelected] = useState<Lab | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [loading, setLoading] = useState(true);

  async function refresh() {
    const [l, d] = await Promise.all([labsApi.list(), dockerStatus()]);
    setLabs(l);
    setDocker(d);
    if (selected) {
      const fresh = l.find((x) => x.id === selected.id);
      setSelected(fresh || null);
    }
    setLoading(false);
  }

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 6000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="p-6">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-bold">Labs</h1>
        <Button onClick={() => setCreateOpen(true)}>+ Nieuw lab</Button>
      </div>

      {docker && !docker.daemon_up && (
        <Card className="mb-4 border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
          <strong>Docker niet beschikbaar.</strong> {docker.hint}
          <div className="mt-1 text-xs opacity-80">
            in_container={String(docker.in_container)} · socket_mounted={String(docker.socket_mounted)} · docker_host={docker.docker_host}
          </div>
        </Card>
      )}

      {loading ? (
        <p className="text-sm text-muted-foreground">Laden…</p>
      ) : labs.length === 0 ? (
        <EmptyState>Nog geen labs. Maak er een aan om te beginnen.</EmptyState>
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
          {labs.map((lab) => (
            <Card key={lab.id} className="cursor-pointer p-4 hover:border-primary/40" onClick={() => setSelected(lab)}>
              <div className="mb-1 flex items-center justify-between">
                <span className="font-semibold">{lab.name}</span>
                <Badge tone={statusTone(lab.status)}>{lab.status}</Badge>
              </div>
              <div className="text-xs text-muted-foreground">{lab.image}</div>
              <div className="mt-2 flex flex-wrap gap-1 text-xs text-muted-foreground">
                {lab.data_guard && <Badge tone="violet">data-guard</Badge>}
                {lab.llm_guard && <Badge tone="violet">llm-guard</Badge>}
                {/* Het lab draait al terwijl de pakketten nog binnenkomen —
                    zonder dit zou je op "running" afgaan en je afvragen waarom
                    Playwright er nog niet is. */}
                {(lab.provision_status === "pending" || lab.provision_status === "running") && (
                  <Badge tone="yellow">inrichten…</Badge>
                )}
                {lab.provision_status === "error" && <Badge tone="red">inrichten mislukt</Badge>}
                {(lab.extras || []).length > 0 && lab.provision_status === "ok" && (
                  <Badge tone="neutral">{lab.extras.length} extra&apos;s</Badge>
                )}
              </div>
            </Card>
          ))}
        </div>
      )}

      {createOpen && (
        <CreateLabModal
          onClose={() => setCreateOpen(false)}
          onCreated={() => {
            setCreateOpen(false);
            refresh();
          }}
        />
      )}

      {selected && <LabDetailModal lab={selected} onClose={() => setSelected(null)} onChanged={refresh} />}
    </div>
  );
}

function CreateLabModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState("");
  const [presets, setPresets] = useState<ImagePreset[]>([]);
  const [environment, setEnvironment] = useState("python");
  // "__custom__" = niet uit de lijst maar een zelf ingetypt image.
  const [customImage, setCustomImage] = useState("");
  const [imageQuery, setImageQuery] = useState("");
  const [imageHits, setImageHits] = useState<{ name: string; description: string }[]>([]);
  const [searching, setSearching] = useState(false);
  const [catalog, setCatalog] = useState<LabExtra[]>([]);
  const [extras, setExtras] = useState<string[]>([]);
  const [setupScript, setSetupScript] = useState("");
  const [cpu, setCpu] = useState(1);
  const [mem, setMem] = useState(2048);
  const [ttl, setTtl] = useState(24);
  const [allowNetwork, setAllowNetwork] = useState(true);
  const [dataGuard, setDataGuard] = useState(true);
  const [llmGuard, setLlmGuard] = useState(true);
  const [repoUrl, setRepoUrl] = useState("");
  const [repoToken, setRepoToken] = useState("");
  const [ports, setPorts] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    labsApi.images().then((r) => setPresets(r.presets));
    labsApi.extras().then((rows) => {
      const available = rows.filter((e) => e.is_enabled);
      setCatalog(available);
      setExtras(available.filter((e) => e.default_on).map((e) => e.key));
    });
  }, []);

  async function searchImages() {
    if (imageQuery.trim().length < 2) return;
    setSearching(true);
    try {
      const r = await labsApi.searchImages(imageQuery.trim());
      setImageHits((r.results || []).map((x: any) => ({ name: x.name, description: x.description })));
    } finally {
      setSearching(false);
    }
  }

  function toggleExtra(key: string) {
    setExtras((cur) => (cur.includes(key) ? cur.filter((k) => k !== key) : [...cur, key]));
  }

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const repos = repoUrl.trim()
        ? [{ url: repoUrl.trim(), token: repoToken.trim() || undefined }]
        : undefined;
      const portList = ports
        .split(",")
        .map((p) => p.trim())
        .filter(Boolean)
        .map(Number)
        .filter((n) => Number.isFinite(n));
      const useCustom = environment === "__custom__";
      await labsApi.create({
        name,
        // Eén van de twee: een preset-sleutel, of een zelf opgegeven image.
        environment: useCustom ? undefined : environment,
        image: useCustom ? customImage.trim() : undefined,
        cpu_limit: cpu, mem_limit_mb: mem, ttl_hours: ttl,
        allow_network: allowNetwork, data_guard: dataGuard, llm_guard: llmGuard,
        repos, ports: portList.length ? portList : undefined,
        extras, setup_script: setupScript.trim() || undefined,
      });
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Aanmaken mislukt");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open onClose={onClose} title="Nieuw lab">
      <div className="space-y-3">
        <div>
          <Label>Naam</Label>
          <Input value={name} onChange={(e) => setName(e.target.value)} autoFocus />
        </div>
        <div>
          <Label>Omgeving</Label>
          <select
            value={environment}
            onChange={(e) => setEnvironment(e.target.value)}
            className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm"
          >
            {presets.map((p) => (
              <option key={p.key} value={p.key}>
                {p.label}
              </option>
            ))}
            <option value="__custom__">Eigen image…</option>
          </select>
          {environment === "__custom__" ? (
            <div className="mt-2 space-y-2">
              <Input
                value={customImage}
                onChange={(e) => setCustomImage(e.target.value)}
                placeholder="mcr.microsoft.com/playwright:v1.55.0-noble"
              />
              <div className="flex gap-2">
                <Input
                  value={imageQuery}
                  onChange={(e) => setImageQuery(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && searchImages()}
                  placeholder="Zoek op Docker Hub…"
                />
                <Button variant="secondary" onClick={searchImages} disabled={searching}>
                  {searching ? "Zoeken…" : "Zoek"}
                </Button>
              </div>
              {imageHits.length > 0 && (
                <div className="max-h-40 overflow-y-auto rounded-md border border-border">
                  {imageHits.map((hit) => (
                    <button
                      key={hit.name}
                      onClick={() => setCustomImage(hit.name)}
                      className="block w-full px-2 py-1 text-left text-xs hover:bg-secondary"
                    >
                      <span className="font-medium">{hit.name}</span>
                      {hit.description && <span className="text-muted-foreground"> — {hit.description}</span>}
                    </button>
                  ))}
                </div>
              )}
              <p className="text-xs text-muted-foreground">
                Elk Debian/Ubuntu-gebaseerd image werkt; het basisgereedschap wordt er alsnog in gezet.
              </p>
            </div>
          ) : (
            <p className="mt-1 text-xs text-muted-foreground">{presets.find((p) => p.key === environment)?.description}</p>
          )}
        </div>
        <div>
          <Label>Erbij installeren</Label>
          <p className="mb-1 text-xs text-muted-foreground">
            Wordt na het aanmaken op de achtergrond geïnstalleerd — het lab is meteen bruikbaar en de
            voortgang staat op het tabblad Inrichting. Beheer de lijst bij Instellingen &gt; Lab-extra's.
          </p>
          <div className="space-y-1 rounded-md border border-border p-2">
            {catalog.length === 0 && <p className="text-xs text-muted-foreground">Geen pakketten in de catalogus.</p>}
            {catalog.map((e) => (
              <label key={e.key} className="flex cursor-pointer items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={extras.includes(e.key)}
                  onChange={() => toggleExtra(e.key)}
                />
                <span>
                  {e.label}
                  {e.description && <span className="block text-xs text-muted-foreground">{e.description}</span>}
                </span>
              </label>
            ))}
          </div>
        </div>
        <div className="grid grid-cols-3 gap-2">
          <div>
            <Label>CPU</Label>
            <Input type="number" step="0.5" value={cpu} onChange={(e) => setCpu(Number(e.target.value))} />
          </div>
          <div>
            <Label>RAM (MB)</Label>
            <Input type="number" step="256" value={mem} onChange={(e) => setMem(Number(e.target.value))} />
          </div>
          <div>
            <Label>Stopt na (uur zonder gebruik)</Label>
            <Input type="number" value={ttl} onChange={(e) => setTtl(Number(e.target.value))} />
          </div>
        </div>
        <div className="space-y-2 rounded-md border border-border p-3">
          <Toggle checked={allowNetwork} onChange={setAllowNetwork} label="Netwerktoegang" />
          <Toggle checked={dataGuard} onChange={setDataGuard} label="Data-egress-guard (regels)" />
          <Toggle checked={llmGuard} onChange={setLlmGuard} label="Lokaal model als extra check (spoor B)" />
        </div>
        <details className="rounded-md border border-border p-3 text-sm">
          <summary className="cursor-pointer font-medium text-muted-foreground">Repo clonen &amp; poorten (optioneel)</summary>
          <div className="mt-2 space-y-2">
            <div>
              <Label>Repo-URL</Label>
              <Input value={repoUrl} onChange={(e) => setRepoUrl(e.target.value)} placeholder="https://github.com/org/repo.git" />
            </div>
            {repoUrl.trim() && (
              <div>
                <Label>Token (optioneel, voor privérepo's)</Label>
                <Input type="password" value={repoToken} onChange={(e) => setRepoToken(e.target.value)} />
              </div>
            )}
            <div>
              <Label>Poorten om te publiceren (komma-gescheiden)</Label>
              <Input value={ports} onChange={(e) => setPorts(e.target.value)} placeholder="8000, 8501" />
            </div>
            <div>
              <Label>Eigen setup-script (draait na de pakketten, als root)</Label>
              <TextArea
                rows={4}
                className="font-mono text-xs"
                value={setupScript}
                onChange={(e) => setSetupScript(e.target.value)}
                placeholder={"pip install -q pandas pyarrow"}
              />
              <p className="mt-1 text-xs text-muted-foreground">
                Draait bij elk inrichten opnieuw (ook bij een herstart), dus schrijf het zo dat het
                een tweede keer geen kwaad kan.
              </p>
            </div>
          </div>
        </details>
        {error && <p className="text-sm text-destructive">{error}</p>}
        <Button
          className="w-full"
          disabled={busy || !name.trim() || (environment === "__custom__" && !customImage.trim())}
          onClick={submit}
        >
          {busy ? "Aanmaken…" : "Aanmaken"}
        </Button>
      </div>
    </Modal>
  );
}

function LabDetailModal({ lab, onClose, onChanged }: { lab: Lab; onClose: () => void; onChanged: () => void }) {
  const [tab, setTab] = useState<"settings" | "inrichting" | "browser" | "toegang" | "git" | "files" | "exec" | "terminal" | "audit">("settings");
  const [guardStatus, setGuardStatus] = useState<GuardModelStatus | null>(null);

  useEffect(() => {
    if (!lab.llm_guard) return;
    let cancelled = false;
    const poll = () => labsApi.guardModelStatus().then((s) => !cancelled && setGuardStatus(s)).catch(() => {});
    poll();
    const t = setInterval(poll, 4000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [lab.llm_guard]);

  async function toggle(field: "data_guard" | "llm_guard", value: boolean) {
    await labsApi.update(lab.id, { [field]: value });
    onChanged();
  }

  return (
    <Modal open onClose={onClose} title={lab.name} wide>
      <div className="mb-3 flex items-center gap-2 text-sm">
        <Badge tone={statusTone(lab.status)}>{lab.status}</Badge>
        <span className="text-muted-foreground">{lab.image}</span>
        {lab.status === "running" ? (
          <Button variant="secondary" onClick={() => labsApi.stop(lab.id).then(onChanged)}>Stop</Button>
        ) : (
          <Button variant="secondary" onClick={() => labsApi.start(lab.id).then(onChanged)}>Start</Button>
        )}
        <Button variant="danger" onClick={() => labsApi.remove(lab.id).then(() => { onChanged(); onClose(); })}>
          Verwijderen
        </Button>
      </div>

      <div className="mb-3 flex flex-wrap gap-1 border-b border-border text-sm">
        {(["settings", "inrichting", "browser", "toegang", "git", "files", "exec", "terminal", "audit"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-3 py-1.5 ${tab === t ? "border-b-2 border-primary font-medium" : "text-muted-foreground"}`}
          >
            {{ settings: "Instellingen", inrichting: "Inrichting", browser: "Browser", toegang: "Toegang",
               git: "Git", files: "Bestanden", exec: "Commando", terminal: "Terminal",
               audit: "Guard-audit" }[t]}
          </button>
        ))}
      </div>

      {tab === "settings" && (
        <div className="space-y-3">
          <Toggle checked={lab.data_guard} onChange={(v) => toggle("data_guard", v)} label="Data-egress-guard (regels)" />
          <Toggle checked={lab.llm_guard} onChange={(v) => toggle("llm_guard", v)} label="Lokaal model als extra check (spoor B)" />
          {lab.llm_guard && guardStatus && (
            <div className="text-xs text-muted-foreground">
              Model {guardStatus.model}: <Badge tone={guardStatus.state === "ready" ? "green" : "yellow"}>{guardStatus.state}</Badge>
              {guardStatus.hint && <span className="ml-2">{guardStatus.hint}</span>}
              {guardStatus.state !== "ready" && (
                <Button variant="ghost" className="ml-2" onClick={() => labsApi.guardModelEnsure().then(setGuardStatus)}>
                  Nu ophalen
                </Button>
              )}
            </div>
          )}
          {lab.status === "expired" && (
            <p className="mb-3 rounded-md border border-border bg-secondary/40 p-2 text-xs text-muted-foreground">
              Dit lab is gestopt omdat het {lab.ttl_hours} uur niet gebruikt is. Er is niets weg:
              /workspace staat op een eigen volume en de container bestaat nog. Starten zet hem
              weer aan en de teller begint opnieuw.
            </p>
          )}
          <div className="grid grid-cols-2 gap-3 text-sm text-muted-foreground">
            <div>CPU: {lab.cpu_limit}</div>
            <div>RAM: {lab.mem_limit_mb} MB</div>
            <div>
              Stopt na {lab.ttl_hours}u zonder gebruik
              {lab.expires_at && lab.status !== "expired" && (
                <> — nu: {new Date(lab.expires_at).toLocaleString()}</>
              )}
            </div>
            <div>Netwerk-alias: {lab.network_alias}</div>
          </div>
          <div className="border-t border-border pt-3">
            <AzureProfilePicker
              value={lab.azure_profile_id}
              onChange={(id) => labsApi.update(lab.id, { azure_profile_id: id }).then(onChanged)}
              label="Azure-profiel voor dit lab (identiteit voor Microsoft MCP-servers zoals Azure MCP/Fabric bij een aan dit lab gekoppelde chat — overruled zo'n server z'n eigen standaardprofiel)"
            />
          </div>
        </div>
      )}

      {tab === "inrichting" && <ProvisioningPanel lab={lab} onChanged={onChanged} />}
      {tab === "browser" && <BrowserPanel lab={lab} />}
      {tab === "toegang" && <LabAllowlist lab={lab} onSaved={() => onChanged()} />}
      {tab === "git" && <PublishPanel lab={lab} />}
      {tab === "files" && <FileBrowser lab={lab} />}
      {tab === "exec" && <ExecPanel lab={lab} />}
      {tab === "terminal" && <LabTerminal labId={lab.id} token={getToken() || ""} />}
      {tab === "audit" && <GuardAuditPanel lab={lab} />}
    </Modal>
  );
}

function provisionTone(status: Lab["provision_status"]) {
  return { ok: "green", error: "red", running: "yellow", pending: "yellow", skipped: "neutral" }[
    status || "skipped"
  ] as any;
}

/**
 * Tabblad "Inrichting": wat er in dit lab geïnstalleerd staat, wat dat deed, en
 * de knop om het opnieuw te proberen. Het inrichten draait op de achtergrond
 * (een browser binnenhalen duurt minuten), dus dit scherm polt zolang het bezig
 * is — zonder dat zou je alleen "pending" zien en moeten raden.
 */
function ProvisioningPanel({ lab, onChanged }: { lab: Lab; onChanged: () => void }) {
  const [catalog, setCatalog] = useState<LabExtra[]>([]);
  const [extras, setExtras] = useState<string[]>(lab.extras || []);
  const [script, setScript] = useState(lab.setup_script || "");
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [image, setImage] = useState(lab.image);
  const [confirmRebuild, setConfirmRebuild] = useState(false);
  const running = lab.provision_status === "running" || lab.provision_status === "pending";
  const rebuilding = lab.status === "creating";

  useEffect(() => {
    labsApi.extras().then((rows) => setCatalog(rows.filter((e) => e.is_enabled)));
  }, []);

  useEffect(() => {
    setExtras(lab.extras || []);
    setScript(lab.setup_script || "");
    setImage(lab.image);
  }, [lab.extras, lab.setup_script, lab.image]);

  // De verversfunctie via een ref: hij is elke render een nieuwe functie, en
  // als hij in de deps stond zou het interval bij elke render opnieuw beginnen
  // en dus mogelijk nooit aflopen.
  const refreshRef = useRef(onChanged);
  refreshRef.current = onChanged;
  useEffect(() => {
    if (!running && !rebuilding) return;
    const t = setInterval(() => refreshRef.current(), 3000);
    return () => clearInterval(t);
  }, [running, rebuilding]);

  const dirty =
    JSON.stringify([...extras].sort()) !== JSON.stringify([...(lab.extras || [])].sort()) ||
    script !== (lab.setup_script || "");

  async function save() {
    setBusy(true);
    setError(null);
    try {
      // Opslaan installeert meteen wat erbij komt (de backend start het
      // inrichten zelf zodra deze twee velden veranderen).
      await labsApi.update(lab.id, { extras, setup_script: script });
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Opslaan mislukt");
    } finally {
      setBusy(false);
    }
  }

  async function rebuild() {
    setBusy(true);
    setError(null);
    try {
      await labsApi.rebuild(lab.id, image.trim() || undefined);
      setConfirmRebuild(false);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Opnieuw opbouwen mislukt");
    } finally {
      setBusy(false);
    }
  }

  async function reprovision(force: boolean) {
    setBusy(true);
    setError(null);
    try {
      await labsApi.provision(lab.id, force);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Inrichten starten mislukt");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-muted-foreground">Status:</span>
        <Badge tone={provisionTone(lab.provision_status)}>{lab.provision_status || "onbekend"}</Badge>
        {running && <span className="text-xs text-muted-foreground">Bezig — dit scherm ververst zichzelf.</span>}
        <div className="ml-auto flex gap-2">
          <Button variant="secondary" disabled={busy || running || lab.status !== "running"}
                  onClick={() => reprovision(false)}>
            Opnieuw inrichten
          </Button>
          <Button variant="ghost" disabled={busy || running || lab.status !== "running"}
                  onClick={() => reprovision(true)}>
            Alles opnieuw installeren
          </Button>
        </div>
      </div>

      {!lab.allow_network && (
        <p className="rounded-md border border-border bg-secondary/40 p-2 text-xs text-muted-foreground">
          Dit lab heeft geen netwerktoegang, dus er valt niets te installeren.
        </p>
      )}

      <div className="rounded-md border border-border p-3">
        <Label>Image</Label>
        <div className="mt-1 flex gap-2">
          <Input value={image} onChange={(e) => setImage(e.target.value)} />
          <Button variant="secondary" disabled={busy || rebuilding} onClick={() => setConfirmRebuild(true)}>
            {rebuilding ? "Bezig…" : "Opnieuw opbouwen"}
          </Button>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          Een container houdt het image waarmee hij is gemaakt, dus wijzigen of bijwerken betekent:
          opnieuw opbouwen. Laat het veld staan om dit image naar zijn nieuwste versie te halen.
        </p>
        {confirmRebuild && (
          <div className="mt-2 space-y-2 rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs">
            <p>
              <strong>/workspace blijft</strong> (eigen volume) en de aangevinkte pakketten worden
              opnieuw geïnstalleerd. Weg is alles wat verder in de container stond — handmatig
              geïnstalleerde tools buiten /workspace, systeeminstellingen, de az-sessie (die wordt
              opnieuw gesynct). Duurt enkele minuten.
            </p>
            <div className="flex gap-2">
              <Button variant="danger" disabled={busy} onClick={rebuild}>
                Ja, opnieuw opbouwen op {image.trim() || lab.image}
              </Button>
              <Button variant="ghost" onClick={() => setConfirmRebuild(false)}>
                Annuleren
              </Button>
            </div>
          </div>
        )}
      </div>

      <div>
        <Label>Geïnstalleerd in dit lab</Label>
        <div className="mt-1 space-y-1 rounded-md border border-border p-2">
          {catalog.length === 0 && <p className="text-xs text-muted-foreground">Geen pakketten in de catalogus.</p>}
          {catalog.map((e) => (
            <label key={e.key} className="flex cursor-pointer items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-1"
                checked={extras.includes(e.key)}
                onChange={() =>
                  setExtras((cur) => (cur.includes(e.key) ? cur.filter((k) => k !== e.key) : [...cur, e.key]))
                }
              />
              <span>
                {e.label}
                {e.description && <span className="block text-xs text-muted-foreground">{e.description}</span>}
              </span>
            </label>
          ))}
        </div>
      </div>

      <div>
        <Label>Eigen setup-script</Label>
        <TextArea rows={4} className="font-mono text-xs" value={script}
                  onChange={(ev) => setScript(ev.target.value)} />
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}
      <Button disabled={busy || !dirty} onClick={save}>
        {busy ? "Bezig…" : "Opslaan en installeren"}
      </Button>

      {(lab.provision_log || []).length > 0 && (
        <div>
          <Label>Laatste ronde</Label>
          <div className="mt-1 divide-y divide-border rounded-md border border-border text-sm">
            {(lab.provision_log || []).map((step) => (
              <div key={step.key}>
                <button
                  className="flex w-full items-center gap-2 px-2 py-1.5 text-left hover:bg-secondary"
                  onClick={() => setOpen(open === step.key ? null : step.key)}
                >
                  <Badge tone={step.status === "ok" ? "green" : step.status === "error" ? "red" : "neutral"}>
                    {step.status}
                  </Badge>
                  <span>{step.label}</span>
                  {step.exit_code != null && step.status === "error" && (
                    <span className="text-xs text-muted-foreground">exit {step.exit_code}</span>
                  )}
                </button>
                {open === step.key && step.output && (
                  <pre className="max-h-64 overflow-auto bg-secondary/40 px-2 py-1 text-xs whitespace-pre-wrap">
                    {step.output}
                  </pre>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * Tabblad "Browser": dezelfde browser die de agent bestuurt, maar dan zichtbaar
 * — zodat je er zelf in kunt inloggen waar de agent niet langs komt (een
 * Microsoft-login met tweestapsverificatie bijvoorbeeld). Wat jij hier doet,
 * doe je in zíjn sessie: hij werkt daarna gewoon verder achter die login.
 *
 * De VNC-poort van het lab wordt niet op de host gepubliceerd; dit gaat door
 * een proxy in LabX, dus achter dezelfde login als de rest.
 */
function BrowserPanel({ lab }: { lab: Lab }) {
  const heeftPakket = (lab.extras || []).includes("browser-vnc");
  const url = `/api/labs/${lab.id}/browser?token=${encodeURIComponent(getToken() || "")}`;

  if (!heeftPakket) {
    return (
      <div className="space-y-2 text-sm">
        <p className="text-muted-foreground">
          Dit lab heeft het pakket <strong>Zelf inloggen in de browser van het lab</strong> niet aan
          staan. Vink het aan bij <strong>Inrichting</strong>; daarna draait de browser van de agent
          zichtbaar en kun je hier meekijken en zelf inloggen.
        </p>
      </div>
    );
  }
  if (lab.status !== "running") {
    return <p className="text-sm text-muted-foreground">Start het lab om de browser te zien.</p>;
  }
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <span>
          Je kijkt naar de browser van de agent. Log hier in en hij werkt verder in die sessie;
          het profiel staat op /workspace en blijft dus bewaard.
        </span>
        <a className="ml-auto whitespace-nowrap underline" href={url} target="_blank" rel="noreferrer">
          In een nieuw tabblad
        </a>
      </div>
      <iframe
        title="Browser van het lab"
        src={url}
        className="h-[70vh] w-full rounded-md border border-border bg-black"
      />
      <p className="text-xs text-muted-foreground">
        Nog geen venster te zien? De browser start pas zodra de agent hem gebruikt — laat hem
        bijvoorbeeld naar de pagina navigeren waar de login op komt.
      </p>
    </div>
  );
}

function PublishPanel({ lab }: { lab: Lab }) {
  const [repo, setRepo] = useState("");
  const [branch, setBranch] = useState("");
  const [message, setMessage] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  async function publish() {
    setBusy(true);
    setResult(null);
    try {
      const r: any = await labsApi.publish(lab.id, {
        repo, branch: branch || undefined, message: message || undefined, token: token || undefined,
      });
      setResult(r.output || "Gepubliceerd.");
    } catch (err) {
      setResult(err instanceof ApiError ? err.message : "Publiceren mislukt");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        Commit + push van een repo in <code>/workspace</code> via een kortlevende helper-container —
        het token komt nooit in de lab-container zelf terecht. Voor Azure-identiteiten in dit lab:
        gebruik de Azure-profielen-pagina (sync → lab).
      </p>
      <div>
        <Label>Repo-map (naam onder /workspace)</Label>
        <Input value={repo} onChange={(e) => setRepo(e.target.value)} placeholder="repo" />
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div>
          <Label>Branch (optioneel)</Label>
          <Input value={branch} onChange={(e) => setBranch(e.target.value)} placeholder="main" />
        </div>
        <div>
          <Label>Token (optioneel)</Label>
          <Input type="password" value={token} onChange={(e) => setToken(e.target.value)} />
        </div>
      </div>
      <div>
        <Label>Commit-bericht (optioneel — leeg = geen commit, alleen push)</Label>
        <Input value={message} onChange={(e) => setMessage(e.target.value)} />
      </div>
      <Button onClick={publish} disabled={busy || !repo.trim()}>
        {busy ? "Bezig…" : "Publiceren"}
      </Button>
      {result && <pre className="max-h-40 overflow-auto rounded bg-secondary p-2 text-xs whitespace-pre-wrap">{result}</pre>}
    </div>
  );
}

function FileBrowser({ lab }: { lab: Lab }) {
  const [path, setPath] = useState("/workspace");
  const [entries, setEntries] = useState<{ name: string; is_dir: boolean }[]>([]);
  const [content, setContent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load(p: string) {
    setError(null);
    try {
      const r = await labsApi.files(lab.id, p);
      setEntries(r.entries);
      setPath(r.path);
      setContent(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Laden mislukt");
    }
  }

  useEffect(() => {
    load("/workspace");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lab.id]);

  return (
    <div>
      <div className="mb-2 text-xs text-muted-foreground">{path}</div>
      {error && <p className="mb-2 text-sm text-destructive">{error}</p>}
      <ul className="mb-3 max-h-48 divide-y divide-border overflow-y-auto rounded border border-border text-sm">
        {path !== "/workspace" && (
          <li className="cursor-pointer px-3 py-1.5 hover:bg-secondary" onClick={() => load(path.split("/").slice(0, -1).join("/") || "/workspace")}>
            ..
          </li>
        )}
        {entries.map((e) => (
          <li
            key={e.name}
            className="cursor-pointer px-3 py-1.5 hover:bg-secondary"
            onClick={() => (e.is_dir ? load(`${path}/${e.name}`) : labsApi.readFile(lab.id, `${path}/${e.name}`).then((r) => setContent(r.content)))}
          >
            {e.is_dir ? "📁" : "📄"} {e.name}
          </li>
        ))}
      </ul>
      {content !== null && <pre className="max-h-64 overflow-auto rounded bg-secondary p-3 text-xs whitespace-pre-wrap">{content}</pre>}
    </div>
  );
}

function ExecPanel({ lab }: { lab: Lab }) {
  const [command, setCommand] = useState("");
  const [result, setResult] = useState<{ exit_code: number; output: string; guarded?: boolean; guard_reason?: string } | null>(null);
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true);
    try {
      const r = await labsApi.exec(lab.id, command);
      setResult(r);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="mb-2 flex gap-2">
        <Input value={command} onChange={(e) => setCommand(e.target.value)} placeholder="echo hallo" onKeyDown={(e) => e.key === "Enter" && run()} />
        <Button onClick={run} disabled={busy || !command.trim()}>
          {busy ? "…" : "Run"}
        </Button>
      </div>
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

function GuardAuditPanel({ lab }: { lab: Lab }) {
  const [items, setItems] = useState<any[]>([]);

  useEffect(() => {
    labsApi.guardAudit(lab.id).then((r) => setItems(r.items));
  }, [lab.id]);

  return (
    <div>
      <a href={`/api/labs/${lab.id}/guard-audit?format=csv`} className="mb-2 inline-block text-xs text-primary hover:underline">
        Download CSV
      </a>
      <div className="max-h-72 overflow-auto rounded border border-border text-xs">
        <table className="w-full">
          <thead className="sticky top-0 bg-secondary">
            <tr>
              <th className="p-2 text-left">Tijd</th>
              <th className="p-2 text-left">Blocked</th>
              <th className="p-2 text-left">Reden</th>
              <th className="p-2 text-left">Commando</th>
            </tr>
          </thead>
          <tbody>
            {items.map((it, i) => (
              <tr key={i} className="border-t border-border">
                <td className="p-2">{new Date(it.ts).toLocaleTimeString()}</td>
                <td className="p-2">{it.data?.blocked ? <Badge tone="red">ja</Badge> : <Badge tone="green">nee</Badge>}</td>
                <td className="p-2">{it.data?.guard_reason || "-"}</td>
                <td className="p-2 font-mono">{(it.data?.command || "").slice(0, 60)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
