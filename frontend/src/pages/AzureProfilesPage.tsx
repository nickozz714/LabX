/**
 * pages/AzureProfilesPage.tsx — ported in spirit from ND3X's
 * AzureProfilesTile.tsx: create (bundle/SP/bearer), capture-host, verify,
 * delete, sync to host or to a lab.
 */
import { useEffect, useState } from "react";
import { azureProfilesApi } from "@/lib/azureProfiles";
import { labsApi } from "@/lib/labs";
import type { AzureProfileDto, Lab } from "@/lib/types";
import { Badge, Button, Card, EmptyState, Input, Label, Modal, TextArea } from "@/components/ui";
import { ApiError } from "@/lib/api";

export function AzureProfilesPage() {
  const [profiles, setProfiles] = useState<AzureProfileDto[]>([]);
  const [labs, setLabs] = useState<Lab[]>([]);
  const [creating, setCreating] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  function refresh() {
    azureProfilesApi.list().then(setProfiles);
  }
  useEffect(() => {
    refresh();
    labsApi.list().then(setLabs);
  }, []);

  async function verify(p: AzureProfileDto) {
    setBusyId(p.id);
    try {
      const r = await azureProfilesApi.verify(p.id);
      setMessage(`Identiteit: ${JSON.stringify(r.identity)}`);
      refresh();
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : "Verify mislukt");
    } finally {
      setBusyId(null);
    }
  }

  async function syncToLab(p: AzureProfileDto, labId: string) {
    if (!labId) return;
    setBusyId(p.id);
    try {
      const r = await azureProfilesApi.sync(p.id, { target: "lab", lab_id: labId });
      setMessage(r.ok ? "Gesynct naar lab." : `Sync mislukt: ${JSON.stringify(r.detail)}`);
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : "Sync mislukt");
    } finally {
      setBusyId(null);
    }
  }

  async function syncToHost(p: AzureProfileDto) {
    setBusyId(p.id);
    try {
      const r = await azureProfilesApi.sync(p.id, { target: "host" });
      setMessage(r.ok ? "Gesynct naar de LabX-host." : `Sync mislukt: ${JSON.stringify(r.detail)}`);
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : "Sync mislukt");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="p-6">
      <div className="mb-4 flex justify-between">
        <h1 className="text-xl font-bold">Azure-profielen</h1>
        <div className="flex gap-2">
          <Button
            variant="secondary"
            onClick={async () => {
              try {
                await azureProfilesApi.captureHost("Host az-login");
                refresh();
              } catch (err) {
                setMessage(err instanceof ApiError ? err.message : "Capture mislukt");
              }
            }}
          >
            Vanaf host
          </Button>
          <Button onClick={() => setCreating(true)}>+ Nieuw profiel</Button>
        </div>
      </div>
      {message && <p className="mb-3 text-sm text-muted-foreground">{message}</p>}
      {profiles.length === 0 ? (
        <EmptyState>Nog geen Azure-profielen.</EmptyState>
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {profiles.map((p) => (
            <Card key={p.id} className="p-4">
              <div className="mb-1 flex items-center justify-between">
                <span className="font-medium">{p.name}</span>
                <Badge tone="violet">{p.kind}</Badge>
              </div>
              {p.identity && <pre className="mb-2 max-h-24 overflow-auto rounded bg-secondary p-2 text-xs">{JSON.stringify(p.identity, null, 2)}</pre>}
              <div className="flex flex-wrap gap-2 text-xs">
                <Button variant="secondary" disabled={busyId === p.id} onClick={() => verify(p)}>
                  Verifieer
                </Button>
                <Button variant="secondary" disabled={busyId === p.id} onClick={() => syncToHost(p)}>
                  Sync → host
                </Button>
                <select
                  disabled={busyId === p.id}
                  onChange={(e) => syncToLab(p, e.target.value)}
                  defaultValue=""
                  className="rounded border border-input bg-background px-2 py-1"
                >
                  <option value="" disabled>
                    Sync → lab…
                  </option>
                  {labs.map((l) => (
                    <option key={l.id} value={l.id}>
                      {l.name}
                    </option>
                  ))}
                </select>
                <Button variant="danger" onClick={() => azureProfilesApi.remove(p.id).then(refresh)}>
                  Verwijderen
                </Button>
              </div>
            </Card>
          ))}
        </div>
      )}
      {creating && <CreateProfileModal onClose={() => setCreating(false)} onCreated={() => { setCreating(false); refresh(); }} />}
    </div>
  );
}

function CreateProfileModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState<"msal_bundle" | "service_principal" | "bearer">("msal_bundle");
  const [msalCache, setMsalCache] = useState("");
  const [azureProfileJson, setAzureProfileJson] = useState("");
  const [tenantId, setTenantId] = useState("");
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    try {
      const payload: Record<string, any> = { name, kind };
      if (kind === "msal_bundle") payload.files = { "msal_token_cache.json": msalCache, "azureProfile.json": azureProfileJson };
      if (kind === "service_principal") Object.assign(payload, { tenant_id: tenantId, client_id: clientId, client_secret: clientSecret });
      if (kind === "bearer") payload.token = token;
      await azureProfilesApi.create(payload);
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Aanmaken mislukt");
    }
  }

  return (
    <Modal open onClose={onClose} title="Nieuw Azure-profiel">
      <div className="space-y-3">
        <div>
          <Label>Naam</Label>
          <Input value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <Label>Soort</Label>
          <select value={kind} onChange={(e) => setKind(e.target.value as any)} className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm">
            <option value="msal_bundle">Bundel (~/.azure, geplakt)</option>
            <option value="service_principal">Service principal</option>
            <option value="bearer">Bearer-token</option>
          </select>
        </div>
        {kind === "msal_bundle" && (
          <>
            <div>
              <Label>msal_token_cache.json</Label>
              <TextArea rows={3} value={msalCache} onChange={(e) => setMsalCache(e.target.value)} className="font-mono text-xs" />
            </div>
            <div>
              <Label>azureProfile.json</Label>
              <TextArea rows={3} value={azureProfileJson} onChange={(e) => setAzureProfileJson(e.target.value)} className="font-mono text-xs" />
            </div>
          </>
        )}
        {kind === "service_principal" && (
          <>
            <Input placeholder="tenant_id" value={tenantId} onChange={(e) => setTenantId(e.target.value)} />
            <Input placeholder="client_id" value={clientId} onChange={(e) => setClientId(e.target.value)} />
            <Input placeholder="client_secret" type="password" value={clientSecret} onChange={(e) => setClientSecret(e.target.value)} />
          </>
        )}
        {kind === "bearer" && <TextArea rows={3} placeholder="access token" value={token} onChange={(e) => setToken(e.target.value)} className="font-mono text-xs" />}
        {error && <p className="text-sm text-destructive">{error}</p>}
        <Button className="w-full" onClick={submit} disabled={!name}>
          Aanmaken
        </Button>
      </div>
    </Modal>
  );
}
