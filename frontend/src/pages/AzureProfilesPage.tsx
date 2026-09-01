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
 *
 * Allebei eindigen ze met DOORZETTEN, en niet als losse knop erna. Een verse
 * sessie die alleen hier in de kluis staat verandert namelijk niets: de host en
 * elk draaiend lab houden hun oude kopie, en dat merk je pas als de agent
 * halverwege zijn werk op een verlopen token stuit. Vernieuwen en opnieuw
 * authenticeren zetten daarom zelf door naar alles wat het profiel gebruikt, met
 * per doel een regel of het gelukt is. De losse knoppen (verifiëren, naar de
 * host, naar één lab) blijven bestaan voor als je juist wél één ding wilt doen.
 */
import { useEffect, useState } from "react";
import { azureProfilesApi } from "@/lib/azureProfiles";
import { labsApi } from "@/lib/labs";
import type { AzureProfileDto, Lab } from "@/lib/types";
import { Badge, Button, Card, EmptyState, Input, Label, Modal, TextArea } from "@/components/ui";
import { AzureBundlePicker, bundleComplete } from "@/components/AzureBundlePicker";
import type { ApplyStep } from "@/lib/azureProfiles";
import { ApiError } from "@/lib/api";

export function AzureProfilesPage() {
  const [profiles, setProfiles] = useState<AzureProfileDto[]>([]);
  const [labs, setLabs] = useState<Lab[]>([]);
  const [creating, setCreating] = useState(false);
  const [reauth, setReauth] = useState<AzureProfileDto | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [steps, setSteps] = useState<ApplyStep[] | null>(null);

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
      report(p.name, `identiteit ${JSON.stringify(r.identity)}`);
      refresh();
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : "Verify mislukt");
    } finally {
      setBusyId(null);
    }
  }

  function report(name: string, text: string, list?: ApplyStep[] | null) {
    setMessage(`${name}: ${text}`);
    setSteps(list || null);
  }

  async function refreshTokens(p: AzureProfileDto) {
    setBusyId(p.id);
    report(p.name, "bezig…");
    try {
      const r = await azureProfilesApi.refresh(p.id);
      report(p.name, r.detail, r.apply?.steps);
      refresh();
    } catch (err) {
      report(p.name, err instanceof ApiError ? err.message : "Vernieuwen mislukt");
    } finally {
      setBusyId(null);
    }
  }

  async function applyEverywhere(p: AzureProfileDto) {
    setBusyId(p.id);
    report(p.name, "doorzetten…");
    try {
      const r = await azureProfilesApi.apply(p.id);
      report(p.name, r.ok ? "overal bijgewerkt." : "niet overal gelukt — zie hieronder.", r.steps);
      refresh();
    } catch (err) {
      report(p.name, err instanceof ApiError ? err.message : "Doorzetten mislukt");
    } finally {
      setBusyId(null);
    }
  }

  async function syncToLab(p: AzureProfileDto, labId: string) {
    if (!labId) return;
    setBusyId(p.id);
    try {
      const r = await azureProfilesApi.sync(p.id, { target: "lab", lab_id: labId });
      report(p.name, r.ok ? "gesynct naar lab." : `sync mislukt: ${JSON.stringify(r.detail)}`);
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
      report(p.name, r.ok ? "gesynct naar de LabX-host." : `sync mislukt: ${JSON.stringify(r.detail)}`);
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
      {message && <p className="mb-1 text-sm text-muted-foreground">{message}</p>}
      {steps && (
        <ul className="mb-3 space-y-0.5 text-xs">
          {steps.map((st, i) => (
            <li key={i} className="flex gap-2">
              <span className={st.ok ? "text-success" : "text-destructive"}>{st.ok ? "✓" : "✗"}</span>
              <span className="font-medium">{st.target}</span>
              <span className="text-muted-foreground">{st.detail}</span>
            </li>
          ))}
        </ul>
      )}
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
              <div className="flex flex-wrap items-center gap-2 text-xs">
                {p.kind !== "bearer" && (
                  <Button
                    disabled={busyId === p.id}
                    onClick={() => applyEverywhere(p)}
                    title="Verifieert de sessie en zet hem door naar de host en elk lab dat dit profiel gebruikt"
                  >
                    {busyId === p.id ? "Bezig…" : "Overal toepassen"}
                  </Button>
                )}
                {p.kind === "msal_bundle" && (
                  <Button variant="secondary" disabled={busyId === p.id} onClick={() => setReauth(p)}>
                    Opnieuw authenticeren…
                  </Button>
                )}
                {p.kind !== "bearer" && (
                  <Button
                    variant="secondary"
                    disabled={busyId === p.id}
                    onClick={() => refreshTokens(p)}
                    title="Wisselt het refresh token in voor een vers paar en zet dat meteen door"
                  >
                    Vernieuwen
                  </Button>
                )}
                {/* Losse stappen, voor als je juist één ding wilt doen. */}
                <details className="text-xs">
                  <summary className="cursor-pointer select-none text-muted-foreground">Losse stappen</summary>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <Button variant="secondary" disabled={busyId === p.id} onClick={() => verify(p)}>
                      Verifieer
                    </Button>
                    <Button variant="secondary" disabled={busyId === p.id} onClick={() => syncToHost(p)}>
                      Sync → host
                    </Button>
                    <select
                      disabled={busyId === p.id}
                      onChange={(e) => syncToLab(p, e.target.value)}
                      // Gestuurd op "" en niet defaultValue: anders blijft het
                      // gekozen lab staan en levert hetzelfde lab nog eens
                      // kiezen geen change-event op — de knop deed dan niets.
                      value=""
                      className="rounded border border-input bg-background px-2 py-1"
                    >
                      <option value="">Sync → lab…</option>
                      {labs.map((l) => (
                        <option key={l.id} value={l.id}>
                          {l.name} ({l.status})
                        </option>
                      ))}
                    </select>
                  </div>
                </details>
                <span className="flex-1" />
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
          onDone={(msg, list) => {
            setReauth(null);
            report(reauth.name, msg, list);
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
  onDone: (message: string, steps?: ApplyStep[] | null) => void;
}) {
  const [files, setFiles] = useState<Record<string, string>>({});
  const [spread, setSpread] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /** Opnieuw authenticeren is pas af als de host en de labs het weten. */
  async function finish(what: string) {
    if (!spread) {
      onDone(`${what} — nog niet doorgezet.`);
      return;
    }
    const applied = await azureProfilesApi.apply(profile.id);
    onDone(`${what} ${applied.ok ? "en overal doorgezet." : "— niet overal gelukt:"}`, applied.steps);
  }

  async function fromHost() {
    setBusy(true);
    setError(null);
    try {
      await azureProfilesApi.recaptureHost(profile.id);
      await finish("az-sessie opnieuw van de host gehaald");
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
      await finish("bestanden vervangen");
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
        <label className="flex items-start gap-2 text-xs text-muted-foreground">
          <input type="checkbox" checked={spread} onChange={(e) => setSpread(e.target.checked)} className="mt-0.5" />
          <span>
            Meteen doorzetten naar de host en de labs die dit profiel gebruiken. Zonder dit staat de
            verse sessie alleen hier, en werken de labs door met hun oude kopie.
          </span>
        </label>
        <p className="text-xs text-muted-foreground">
          Naam, omschrijving en alles wat naar dit profiel verwijst blijven staan — alleen de
          opgeslagen sessie wordt vervangen.
        </p>
      </div>
    </Modal>
  );
}
