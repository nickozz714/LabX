/**
 * pages/AzureProfilesPage.tsx — ported in spirit from ND3X's
 * AzureProfilesTile.tsx: create (bundle/SP/bearer), capture-host, verify,
 * delete, sync to host or to a lab.
 *
 * Een opgeslagen az-sessie veroudert, en juist een profiel dat hier ligt te
 * wachten veroudert het snelst: een refresh token verloopt op stilte, niet op
 * gebruik. Daarom twee dingen naast elkaar: "Vernieuwen" wisselt het refresh
 * token in voor een vers paar (het profiel blijft hetzelfde), en "Opnieuw
 * authenticeren" vervangt de bestanden — opnieuw van de host, of gekozen met de
 * bestandskiezer.
 */
import { useEffect, useState } from "react";
import { azureProfilesApi } from "@/lib/azureProfiles";
import { labsApi } from "@/lib/labs";
import type { AzureProfileDto, Lab } from "@/lib/types";
import { Badge, Button, Card, EmptyState, Input, Label, Modal, TextArea } from "@/components/ui";
import { AzureBundlePicker, bundleComplete } from "@/components/AzureBundlePicker";
import { ApiError } from "@/lib/api";

export function AzureProfilesPage() {
  const [profiles, setProfiles] = useState<AzureProfileDto[]>([]);
  const [labs, setLabs] = useState<Lab[]>([]);
  const [creating, setCreating] = useState(false);
  const [reauth, setReauth] = useState<AzureProfileDto | null>(null);
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

  async function refreshTokens(p: AzureProfileDto) {
    setBusyId(p.id);
    setMessage(null);
    try {
      const r = await azureProfilesApi.refresh(p.id);
      setMessage(`${p.name}: ${r.detail}`);
      refresh();
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : "Vernieuwen mislukt");
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
                {p.kind !== "bearer" && (
                  <Button
                    variant="secondary"
                    disabled={busyId === p.id}
                    onClick={() => refreshTokens(p)}
                    title="Wisselt het refresh token in voor een vers paar, zodat de sessie niet verloopt"
                  >
                    {busyId === p.id ? "Bezig…" : "Vernieuwen"}
                  </Button>
                )}
                {p.kind === "msal_bundle" && (
                  <Button variant="secondary" disabled={busyId === p.id} onClick={() => setReauth(p)}>
                    Opnieuw authenticeren…
                  </Button>
                )}
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
      {reauth && (
        <ReauthProfileModal
          profile={reauth}
          onClose={() => setReauth(null)}
          onDone={(msg) => {
            setReauth(null);
            setMessage(msg);
            refresh();
          }}
        />
      )}
    </div>
  );
}

function CreateProfileModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState<"msal_bundle" | "service_principal" | "bearer">("msal_bundle");
  const [files, setFiles] = useState<Record<string, string>>({});
  const [tenantId, setTenantId] = useState("");
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    try {
      const payload: Record<string, any> = { name, kind };
      if (kind === "msal_bundle") payload.files = files;
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
            <option value="msal_bundle">Bundel (bestanden uit ~/.azure)</option>
            <option value="service_principal">Service principal</option>
            <option value="bearer">Bearer-token</option>
          </select>
        </div>
        {kind === "msal_bundle" && (
          <div>
            <Label>Bestanden uit ~/.azure</Label>
            <AzureBundlePicker files={files} onChange={setFiles} />
          </div>
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
        <Button
          className="w-full"
          onClick={submit}
          disabled={!name || (kind === "msal_bundle" && !bundleComplete(files))}
        >
          Aanmaken
        </Button>
      </div>
    </Modal>
  );
}

/**
 * Opnieuw authenticeren: dezelfde profielnaam houden, alleen de az-sessie
 * eronder vervangen. Twee wegen, want er zijn twee situaties: `az login` is
 * hier op de LabX-host gedraaid (dan haalt de knop de bestanden zelf op), of op
 * je eigen machine (dan kies je ze met de bestandskiezer).
 */
function ReauthProfileModal({
  profile, onClose, onDone,
}: {
  profile: AzureProfileDto;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const [files, setFiles] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function fromHost() {
    setBusy(true);
    setError(null);
    try {
      await azureProfilesApi.recaptureHost(profile.id);
      onDone(`${profile.name}: az-sessie opnieuw van de host gehaald.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Ophalen van de host mislukt");
    } finally {
      setBusy(false);
    }
  }

  async function fromFiles() {
    setBusy(true);
    setError(null);
    try {
      await azureProfilesApi.update(profile.id, { files });
      onDone(`${profile.name}: bestanden vervangen.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Opslaan mislukt");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open onClose={onClose} title={`Opnieuw authenticeren — ${profile.name}`}>
      <div className="space-y-4">
        <div className="rounded-md border border-border p-3">
          <Label>Van de LabX-host</Label>
          <p className="mb-2 text-xs text-muted-foreground">
            Draai daar eerst <code>az login</code>; deze knop leest de verse bestanden uit
            ~/.azure en zet ze onder dit profiel.
          </p>
          <Button variant="secondary" className="text-xs" disabled={busy} onClick={fromHost}>
            {busy ? "Bezig…" : "Opnieuw ophalen van host"}
          </Button>
        </div>

        <div className="rounded-md border border-border p-3">
          <Label>Van deze computer</Label>
          <AzureBundlePicker files={files} onChange={setFiles} />
          <Button
            className="mt-2 text-xs"
            disabled={busy || !bundleComplete(files)}
            onClick={fromFiles}
          >
            {busy ? "Opslaan…" : "Bestanden opslaan"}
          </Button>
        </div>

        {error && <p className="text-sm text-destructive">{error}</p>}
        <p className="text-xs text-muted-foreground">
          Naam, omschrijving en alles wat naar dit profiel verwijst blijven staan — alleen de
          opgeslagen sessie wordt vervangen.
        </p>
      </div>
    </Modal>
  );
}
