/**
 * pages/LabsPage.tsx
 *
 * Ported in spirit from ND3X's LabsTile.tsx: list + create dialog + detail
 * panel (settings, guard toggles with live model status, file browser,
 * one-shot exec, interactive terminal, guard-audit). The Docker diagnostic
 * banner is the direct fix for issue 1 ("geen Docker aanwezig").
 */
import { useEffect, useState } from "react";
import { dockerStatus, labsApi } from "@/lib/labs";
import type { DockerStatus, GuardModelStatus, ImagePreset, Lab } from "@/lib/types";
import { Badge, Button, Card, EmptyState, Input, Label, Modal, Toggle } from "@/components/ui";
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
              <div className="mt-2 flex gap-1 text-xs text-muted-foreground">
                {lab.data_guard && <Badge tone="violet">data-guard</Badge>}
                {lab.llm_guard && <Badge tone="violet">llm-guard</Badge>}
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
  }, []);

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
      await labsApi.create({
        name, environment, cpu_limit: cpu, mem_limit_mb: mem, ttl_hours: ttl,
        allow_network: allowNetwork, data_guard: dataGuard, llm_guard: llmGuard,
        repos, ports: portList.length ? portList : undefined,
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
          </select>
          <p className="mt-1 text-xs text-muted-foreground">{presets.find((p) => p.key === environment)?.description}</p>
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
            <Label>TTL (uur)</Label>
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
          </div>
        </details>
        {error && <p className="text-sm text-destructive">{error}</p>}
        <Button className="w-full" disabled={busy || !name.trim()} onClick={submit}>
          {busy ? "Aanmaken…" : "Aanmaken"}
        </Button>
      </div>
    </Modal>
  );
}

function LabDetailModal({ lab, onClose, onChanged }: { lab: Lab; onClose: () => void; onChanged: () => void }) {
  const [tab, setTab] = useState<"settings" | "toegang" | "git" | "files" | "exec" | "terminal" | "audit">("settings");
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
        {(["settings", "toegang", "git", "files", "exec", "terminal", "audit"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-3 py-1.5 ${tab === t ? "border-b-2 border-primary font-medium" : "text-muted-foreground"}`}
          >
            {{ settings: "Instellingen", toegang: "Toegang", git: "Git", files: "Bestanden",
               exec: "Commando", terminal: "Terminal", audit: "Guard-audit" }[t]}
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
          <div className="grid grid-cols-2 gap-3 text-sm text-muted-foreground">
            <div>CPU: {lab.cpu_limit}</div>
            <div>RAM: {lab.mem_limit_mb} MB</div>
            <div>TTL: {lab.ttl_hours}u (verloopt {lab.expires_at ? new Date(lab.expires_at).toLocaleString() : "-"})</div>
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

      {tab === "toegang" && <LabAllowlist lab={lab} onSaved={() => onChanged()} />}
      {tab === "git" && <PublishPanel lab={lab} />}
      {tab === "files" && <FileBrowser lab={lab} />}
      {tab === "exec" && <ExecPanel lab={lab} />}
      {tab === "terminal" && <LabTerminal labId={lab.id} token={getToken() || ""} />}
      {tab === "audit" && <GuardAuditPanel lab={lab} />}
    </Modal>
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
