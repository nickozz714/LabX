/**
 * pages/SkillsPage.tsx
 *
 * Combines MCP-server management, the tool catalog, and the Skill Wizard —
 * the fix for issue 3: the wizard lets you pick tools (grouped by MCP
 * server, host- or lab-located), shows each tool's input schema as a guide,
 * and collects a free-text instruction PER tool.
 */
import { useEffect, useState } from "react";
import { mcpServerApi, skillApi, toolApi, type CatalogEntry } from "@/lib/skills";
import type { MCPServerDto, SkillDto, SkillToolLink, ToolDto } from "@/lib/types";
import { Badge, Button, Card, EmptyState, Input, Label, Modal, TextArea, Toggle } from "@/components/ui";
import { AzureProfilePicker } from "@/components/AzureProfilePicker";
import { ApiError } from "@/lib/api";

type Section = "skills" | "tools" | "mcp";

export function SkillsPage() {
  const [section, setSection] = useState<Section>("skills");
  return (
    <div className="p-6">
      <div className="mb-4 flex gap-1 border-b border-border text-sm">
        {(["skills", "tools", "mcp"] as Section[]).map((s) => (
          <button
            key={s}
            onClick={() => setSection(s)}
            className={`px-3 py-1.5 ${section === s ? "border-b-2 border-primary font-medium" : "text-muted-foreground"}`}
          >
            {{ skills: "Skills", tools: "Tools", mcp: "MCP-servers" }[s]}
          </button>
        ))}
      </div>
      {section === "skills" && <SkillsSection />}
      {section === "tools" && <ToolsSection />}
      {section === "mcp" && <McpSection />}
    </div>
  );
}

// ── MCP servers ────────────────────────────────────────────────────────────

function McpSection() {
  const [view, setView] = useState<"installed" | "library">("installed");
  const [servers, setServers] = useState<MCPServerDto[]>([]);
  const [catalog, setCatalog] = useState<CatalogEntry[]>([]);
  const [open, setOpen] = useState(false);
  const [installing, setInstalling] = useState<string | null>(null);
  const [editingAuth, setEditingAuth] = useState<MCPServerDto | null>(null);
  const [editingConnection, setEditingConnection] = useState<MCPServerDto | null>(null);

  function refresh() {
    mcpServerApi.list().then(setServers);
    mcpServerApi.catalog().then(setCatalog);
  }
  useEffect(refresh, []);

  async function install(key: string) {
    setInstalling(key);
    try {
      await mcpServerApi.installFromCatalog(key);
      refresh();
    } finally {
      setInstalling(null);
    }
  }

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <div className="flex gap-1 text-sm">
          <button
            onClick={() => setView("installed")}
            className={`rounded-md px-2 py-1 ${view === "installed" ? "bg-secondary font-medium" : "text-muted-foreground"}`}
          >
            Geïnstalleerd
          </button>
          <button
            onClick={() => setView("library")}
            className={`rounded-md px-2 py-1 ${view === "library" ? "bg-secondary font-medium" : "text-muted-foreground"}`}
          >
            Bibliotheek
          </button>
        </div>
        {view === "installed" && <Button onClick={() => setOpen(true)}>+ Server</Button>}
      </div>

      {view === "library" && (
        <div>
          <p className="mb-3 text-xs text-muted-foreground">
            Een kleine, ingebouwde standaardbibliotheek van bekende officiële MCP-referentieservers —
            geen live externe marktplaats (dat is een aparte vertrouwenskeuze), maar direct met één
            klik te installeren via <code>npx</code> (Node zit al in het LabX-image).
          </p>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {catalog.map((c) => (
              <Card key={c.key} className="p-3">
                <div className="mb-1 flex items-center justify-between">
                  <span className="font-medium text-sm">{c.name}</span>
                  <Badge tone={c.suggested_location === "lab" ? "violet" : "neutral"}>
                    {c.suggested_location === "lab" ? "in lab" : "extern"}
                  </Badge>
                </div>
                <p className="mb-2 text-xs text-muted-foreground">{c.description}</p>
                <code className="mb-2 block truncate text-xs text-muted-foreground">{c.kind === "http" ? c.base_url : c.package}</code>
                <Button
                  variant={c.installed ? "secondary" : "primary"}
                  disabled={c.installed || installing === c.key}
                  onClick={() => install(c.key)}
                >
                  {c.installed ? "Geïnstalleerd" : installing === c.key ? "Bezig…" : "Installeren"}
                </Button>
              </Card>
            ))}
          </div>
        </div>
      )}

      {view === "installed" && (
        servers.length === 0 ? (
          <EmptyState>Nog geen MCP-servers. Voeg er een toe — extern (host) of in een lab — of installeer er een uit de Bibliotheek.</EmptyState>
        ) : (
          <div className="space-y-2">
            {servers.map((s) => (
              <Card key={s.id} className="p-3">
                <div className="flex items-center justify-between">
                  <div>
                    <div className="font-medium text-sm">
                      {s.name} <Badge tone={s.location === "lab" ? "violet" : "neutral"}>{s.location === "lab" ? "in lab" : "extern"}</Badge>{" "}
                      {s.location === "host" && s.always_allowed && <Badge tone="green">altijd toegestaan</Badge>}{" "}
                      <span className="text-xs text-muted-foreground">{s.server_type}</span>
                    </div>
                    <div className="text-xs text-muted-foreground">{s.base_url || s.stdio_command}</div>
                    {s.last_sync_status && (
                      <div className="text-xs text-muted-foreground">
                        laatst gesynct: {s.last_synced_at ? new Date(s.last_synced_at).toLocaleString() : "-"} ({s.last_sync_status})
                      </div>
                    )}
                  </div>
                  <div className="flex gap-2">
                    <Button variant="secondary" onClick={() => setEditingConnection(s)}>
                      Verbinding bewerken
                    </Button>
                    {s.location === "host" && s.server_type !== "stdio" && (
                      <Button variant="secondary" onClick={() => setEditingAuth(s)}>
                        {s.has_auth ? "Auth wijzigen" : "Auth instellen"}
                      </Button>
                    )}
                    {s.location === "host" && (
                      <Button variant="secondary" onClick={() => mcpServerApi.sync(s.id).then(refresh)}>
                        Sync tools
                      </Button>
                    )}
                    <Button variant="danger" onClick={() => mcpServerApi.remove(s.id).then(refresh)}>
                      Verwijderen
                    </Button>
                  </div>
                </div>
                {s.location === "host" && (
                  <div className="mt-2 flex items-center gap-2 border-t border-border pt-2 text-xs">
                    <span className="font-semibold text-muted-foreground">Bruikbaar in:</span>
                    <select
                      value={s.usage_scope}
                      onChange={(e) => mcpServerApi.update(s.id, { usage_scope: e.target.value }).then(refresh)}
                      className="rounded-md border border-input bg-background px-2 py-1"
                    >
                      <option value="session">Claude-sessie (altijd, elke chat — eigen capaciteit zoals Nectar)</option>
                      <option value="both">Sessie én lab (via de Toegang-lijst van het lab)</option>
                      <option value="lab">Alleen lab (uitsluitend als het lab het expliciet toestaat)</option>
                    </select>
                  </div>
                )}
              </Card>
            ))}
          </div>
        )
      )}
      {open && <CreateMcpServerModal onClose={() => setOpen(false)} onCreated={() => { setOpen(false); refresh(); }} />}
      {editingAuth && (
        <EditAuthModal server={editingAuth} onClose={() => setEditingAuth(null)} onSaved={() => { setEditingAuth(null); refresh(); }} />
      )}
      {editingConnection && (
        <EditConnectionModal server={editingConnection} onClose={() => setEditingConnection(null)} onSaved={() => { setEditingConnection(null); refresh(); }} />
      )}
    </div>
  );
}

type AuthType = "none" | "bearer" | "header";

function AuthFields({
  authType, setAuthType, token, setToken, headerName, setHeaderName, headerValue, setHeaderValue,
}: {
  authType: AuthType; setAuthType: (v: AuthType) => void;
  token: string; setToken: (v: string) => void;
  headerName: string; setHeaderName: (v: string) => void;
  headerValue: string; setHeaderValue: (v: string) => void;
}) {
  return (
    <div className="space-y-2">
      <Label>Authenticatie (versleuteld opgeslagen, nooit teruggegeven)</Label>
      <select value={authType} onChange={(e) => setAuthType(e.target.value as AuthType)} className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm">
        <option value="none">Geen</option>
        <option value="bearer">Bearer-token (Authorization header)</option>
        <option value="header">Aangepaste header</option>
      </select>
      {authType === "bearer" && (
        <Input type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="token" />
      )}
      {authType === "header" && (
        <div className="grid grid-cols-2 gap-2">
          <Input value={headerName} onChange={(e) => setHeaderName(e.target.value)} placeholder="X-Api-Key" />
          <Input type="password" value={headerValue} onChange={(e) => setHeaderValue(e.target.value)} placeholder="waarde" />
        </div>
      )}
    </div>
  );
}

function buildAuthPayload(authType: AuthType, token: string, headerName: string, headerValue: string) {
  if (authType === "bearer" && token.trim()) return { type: "bearer", token: token.trim() };
  if (authType === "header" && headerName.trim() && headerValue.trim()) {
    return { type: "header", header: headerName.trim(), value: headerValue.trim() };
  }
  if (authType === "none") return { type: "none" };
  return undefined;
}

function CreateMcpServerModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [location, setLocation] = useState<"host" | "lab">("host");
  const [serverType, setServerType] = useState<"http" | "sse" | "stdio">("http");
  const [baseUrl, setBaseUrl] = useState("");
  const [stdioCommand, setStdioCommand] = useState("");
  const [authType, setAuthType] = useState<AuthType>("none");
  const [token, setToken] = useState("");
  const [headerName, setHeaderName] = useState("");
  const [headerValue, setHeaderValue] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    try {
      await mcpServerApi.create({
        name, slug, location, server_type: serverType,
        base_url: serverType !== "stdio" ? baseUrl : undefined,
        stdio_command: serverType === "stdio" ? stdioCommand : undefined,
        auth: buildAuthPayload(authType, token, headerName, headerValue),
      });
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Aanmaken mislukt");
    }
  }

  return (
    <Modal open onClose={onClose} title="MCP-server toevoegen">
      <div className="space-y-3">
        <div>
          <Label>Naam</Label>
          <Input value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <Label>Slug</Label>
          <Input value={slug} onChange={(e) => setSlug(e.target.value.toLowerCase())} placeholder="nectar" />
        </div>
        <div>
          <Label>Locatie</Label>
          <div className="flex gap-4 text-sm">
            <label className="flex items-center gap-1">
              <input type="radio" checked={location === "host"} onChange={() => setLocation("host")} /> Extern (op de LabX-server)
            </label>
            <label className="flex items-center gap-1">
              <input type="radio" checked={location === "lab"} onChange={() => setLocation("lab")} /> In een lab
            </label>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {location === "host"
              ? "Draait naast LabX; tools worden host-side aangeroepen (niet geguard — geen labdata)."
              : "Stdio-proces gestart met `docker exec -i` in het gekoppelde lab; output loopt door de data-guard."}
          </p>
        </div>
        <div>
          <Label>Transport</Label>
          <select value={serverType} onChange={(e) => setServerType(e.target.value as any)} className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm">
            <option value="http">http (streamable-http)</option>
            <option value="sse">sse</option>
            <option value="stdio">stdio</option>
          </select>
        </div>
        {serverType === "stdio" ? (
          <div>
            <Label>Stdio-commando</Label>
            <Input value={stdioCommand} onChange={(e) => setStdioCommand(e.target.value)} placeholder="python -m my_mcp_server" />
          </div>
        ) : (
          <div>
            <Label>Base URL</Label>
            <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="http://mijn-server:8642/mcp" />
          </div>
        )}
        {serverType !== "stdio" && (
          <AuthFields
            authType={authType} setAuthType={setAuthType}
            token={token} setToken={setToken}
            headerName={headerName} setHeaderName={setHeaderName}
            headerValue={headerValue} setHeaderValue={setHeaderValue}
          />
        )}
        {error && <p className="text-sm text-destructive">{error}</p>}
        <Button className="w-full" onClick={submit} disabled={!name || !slug}>
          Toevoegen
        </Button>
      </div>
    </Modal>
  );
}

/**
 * Twee soorten inloggegevens, want het zijn twee soorten aanroepen. De
 * toolslijst ophalen doet LabX zelf — geen lab, geen gebruiker in de lus — en
 * dat is precies het geval waarvoor je een dienst-account wilt gebruiken. Een
 * echte aanroep hoort juist te draaien met de identiteit van het lab of de
 * sessie waarin hij plaatsvindt. Laat het tweede blok leeg en de sync gebruikt
 * gewoon de eerste, zoals het altijd was.
 */
function EditAuthModal({ server, onClose, onSaved }: { server: MCPServerDto; onClose: () => void; onSaved: () => void }) {
  const [authType, setAuthType] = useState<AuthType>(server.has_auth ? "bearer" : "none");
  const [token, setToken] = useState("");
  const [headerName, setHeaderName] = useState("");
  const [headerValue, setHeaderValue] = useState("");
  const [separate, setSeparate] = useState(server.has_sync_auth);
  const [syncType, setSyncType] = useState<AuthType>(server.has_sync_auth ? "bearer" : "none");
  const [syncToken, setSyncToken] = useState("");
  const [syncHeaderName, setSyncHeaderName] = useState("");
  const [syncHeaderValue, setSyncHeaderValue] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    try {
      await mcpServerApi.update(server.id, {
        auth: buildAuthPayload(authType, token, headerName, headerValue),
        // Het vinkje uit betekent expliciet "geen aparte" — dus wissen, niet
        // overslaan, anders blijft een oude sync-credential stilletjes staan.
        sync_auth: separate
          ? buildAuthPayload(syncType, syncToken, syncHeaderName, syncHeaderValue)
          : { type: "none" },
      });
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Opslaan mislukt");
    }
  }

  return (
    <Modal open onClose={onClose} title={`Authenticatie — ${server.name}`}>
      <div className="space-y-4">
        <div>
          <p className="mb-1 text-xs font-semibold text-muted-foreground">Voor aanroepen (lab/sessie)</p>
          {server.has_auth && <p className="mb-1 text-xs text-muted-foreground">Er is al een auth geconfigureerd. Vul hieronder in om te vervangen.</p>}
          <AuthFields
            authType={authType} setAuthType={setAuthType}
            token={token} setToken={setToken}
            headerName={headerName} setHeaderName={setHeaderName}
            headerValue={headerValue} setHeaderValue={setHeaderValue}
          />
        </div>

        <div className="rounded-md border border-border p-3">
          <label className="flex items-center gap-2 text-xs font-semibold text-muted-foreground">
            <input type="checkbox" checked={separate} onChange={(e) => setSeparate(e.target.checked)} />
            Aparte inloggegevens voor het synchroniseren
          </label>
          <p className="mt-1 text-xs text-muted-foreground">
            Alleen gebruikt bij "Sync tools" — het ophalen van de toolslijst. Uit = daarvoor gelden
            dezelfde gegevens als hierboven.
          </p>
          {separate && (
            <div className="mt-2">
              {server.has_sync_auth && <p className="mb-1 text-xs text-muted-foreground">Er staat al een sync-auth ingesteld. Vul in om te vervangen.</p>}
              <AuthFields
                authType={syncType} setAuthType={setSyncType}
                token={syncToken} setToken={setSyncToken}
                headerName={syncHeaderName} setHeaderName={setSyncHeaderName}
                headerValue={syncHeaderValue} setHeaderValue={setSyncHeaderValue}
              />
            </div>
          )}
        </div>

        {error && <p className="text-sm text-destructive">{error}</p>}
        <Button className="w-full" onClick={submit}>Opslaan</Button>
      </div>
    </Modal>
  );
}

function EditConnectionModal({ server, onClose, onSaved }: { server: MCPServerDto; onClose: () => void; onSaved: () => void }) {
  const [baseUrl, setBaseUrl] = useState(server.base_url || "");
  const [stdioCommand, setStdioCommand] = useState(server.stdio_command || "");
  const [azureProfileId, setAzureProfileId] = useState<number | null>(server.azure_profile_id);
  const [syncProfileId, setSyncProfileId] = useState<number | null>(server.sync_azure_profile_id);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    try {
      await mcpServerApi.update(server.id, {
        base_url: server.server_type !== "stdio" ? baseUrl : undefined,
        stdio_command: server.server_type === "stdio" ? stdioCommand : undefined,
        azure_profile_id: azureProfileId,
        sync_azure_profile_id: syncProfileId,
      });
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Opslaan mislukt");
    }
  }

  return (
    <Modal open onClose={onClose} title={`Verbinding bewerken — ${server.name}`}>
      <div className="space-y-3">
        {server.server_type === "stdio" ? (
          <div>
            <Label>Stdio-commando</Label>
            <Input value={stdioCommand} onChange={(e) => setStdioCommand(e.target.value)} />
            <p className="mt-1 text-xs text-muted-foreground">
              Bv. een catalogus-entry die nog een argument nodig heeft (organisatienaam, projectpad, …).
            </p>
          </div>
        ) : (
          <div>
            <Label>Base URL</Label>
            <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
          </div>
        )}
        {server.location === "host" && (
          <div>
            <AzureProfilePicker
              value={azureProfileId}
              onChange={setAzureProfileId}
              label="Standaard Azure-profiel (voor Microsoft-servers zoals Azure MCP/Fabric — mint of ververst een echte token/az-sessie i.p.v. een statisch token; wordt overruled door het Azure-profiel van het actieve lab als een chat aan een lab hangt)"
            />
            <p className="mt-1 text-xs text-muted-foreground">
              Alleen relevant voor servers die met Azure/Microsoft-identiteit werken (Azure MCP Server,
              Fabric MCP, Fabric RTI, Dev Box, Azure DevOps). Gebruikt als er geen lab-specifiek profiel is.
            </p>
            <div className="mt-3 border-t border-border pt-3">
              <AzureProfilePicker
                value={syncProfileId}
                onChange={setSyncProfileId}
                label="Azure-profiel voor het synchroniseren (leeg = hetzelfde als hierboven)"
              />
              <p className="mt-1 text-xs text-muted-foreground">
                De toolslijst ophalen doet LabX zelf, zonder lab en zonder gebruiker. Zet hier een
                dienst-identiteit neer als je niet wilt dat die sync op een persoonlijke of
                lab-gebonden login leunt — en andersom: zo lekt een brede sync-identiteit niet door
                naar aanroepen die met de rechten van het lab horen te draaien.
              </p>
            </div>
          </div>
        )}
        {error && <p className="text-sm text-destructive">{error}</p>}
        <Button className="w-full" onClick={submit}>Opslaan</Button>
      </div>
    </Modal>
  );
}

// ── Tools ──────────────────────────────────────────────────────────────────

function ToolsSection() {
  const [tools, setTools] = useState<ToolDto[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  function refresh() {
    toolApi.list().then(setTools);
  }
  useEffect(refresh, []);

  const grouped = tools.reduce<Record<string, { serverId: number | null; tools: ToolDto[] }>>((acc, t) => {
    const key = t.mcp_server ? t.mcp_server.name : "(overig)";
    (acc[key] ||= { serverId: t.mcp_server_id, tools: [] }).tools.push(t);
    return acc;
  }, {});

  function toggleSelect(id: number, on: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (on) next.add(id); else next.delete(id);
      return next;
    });
  }

  async function bulk(payload: { is_enabled?: boolean; provenance?: "control" | "data" }) {
    if (selected.size === 0) return;
    await toolApi.bulk({ tool_ids: [...selected], ...payload });
    setSelected(new Set());
    refresh();
  }

  return (
    <div>
      <h2 className="mb-1 text-sm font-semibold text-muted-foreground">Toolcatalogus</h2>
      <p className="mb-3 text-xs text-muted-foreground">
        <strong>Guard-classificatie</strong> geldt alleen voor tools die IN een lab draaien en bepaalt hoe streng de
        data-egress-guard hun output controleert: <em>"kan klantdata bevatten"</em> = output wordt streng
        gescreend op vertrouwelijke inhoud; <em>"alleen technische metadata"</em> = lichtere controle
        (voor tools die alleen namen/schema's/statussen teruggeven).
      </p>

      {selected.size > 0 && (
        <div className="sticky top-0 z-10 mb-3 flex items-center gap-2 rounded-md border border-primary/40 bg-card p-2 text-xs shadow-sm">
          <span className="font-semibold">{selected.size} geselecteerd</span>
          <Button variant="secondary" className="px-2 py-1 text-xs" onClick={() => bulk({ is_enabled: true })}>Zet aan</Button>
          <Button variant="secondary" className="px-2 py-1 text-xs" onClick={() => bulk({ is_enabled: false })}>Zet uit</Button>
          <Button variant="secondary" className="px-2 py-1 text-xs" onClick={() => bulk({ provenance: "data" })}>Guard: klantdata</Button>
          <Button variant="secondary" className="px-2 py-1 text-xs" onClick={() => bulk({ provenance: "control" })}>Guard: metadata</Button>
          <Button variant="ghost" className="ml-auto px-2 py-1 text-xs" onClick={() => setSelected(new Set())}>Deselecteer</Button>
        </div>
      )}

      {tools.length === 0 ? (
        <EmptyState>Nog geen tools. Voeg een MCP-server toe en sync 'm.</EmptyState>
      ) : (
        <div className="space-y-4">
          {Object.entries(grouped).map(([serverName, group]) => {
            const allSelected = group.tools.every((t) => selected.has(t.id));
            return (
              <div key={serverName}>
                <div className="mb-1 flex items-center gap-2">
                  <label className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground">
                    <input
                      type="checkbox"
                      checked={allSelected}
                      onChange={(e) => group.tools.forEach((t) => toggleSelect(t.id, e.target.checked))}
                    />
                    {serverName} ({group.tools.length})
                  </label>
                  {group.serverId != null && (
                    <>
                      <button className="text-xs text-primary hover:underline"
                              onClick={() => toolApi.bulk({ mcp_server_id: group.serverId!, is_enabled: true }).then(refresh)}>
                        alles aan
                      </button>
                      <button className="text-xs text-primary hover:underline"
                              onClick={() => toolApi.bulk({ mcp_server_id: group.serverId!, is_enabled: false }).then(refresh)}>
                        alles uit
                      </button>
                    </>
                  )}
                </div>
                <div className="space-y-2">
                  {group.tools.map((t) => (
                    <Card key={t.id} className={`p-3 ${t.is_enabled ? "" : "opacity-60"}`}>
                      <div className="flex items-center gap-2">
                        <input
                          type="checkbox"
                          checked={selected.has(t.id)}
                          onChange={(e) => toggleSelect(t.id, e.target.checked)}
                        />
                        <div className="flex-1 font-medium text-sm">
                          {t.name} {t.mcp_server && <Badge tone={t.mcp_server.location === "lab" ? "violet" : "neutral"}>{t.mcp_server.location === "lab" ? "in lab" : "extern"}</Badge>}
                        </div>
                        <Toggle checked={t.is_enabled} onChange={(v) => toolApi.update(t.id, { is_enabled: v }).then(refresh)} />
                        {t.mcp_server?.location === "lab" && (
                          <select
                            value={(t.annotations?.provenance as string) || "data"}
                            onChange={(e) => toolApi.update(t.id, { annotations: { provenance: e.target.value } }).then(refresh)}
                            className="rounded border border-input bg-background px-2 py-0.5 text-xs"
                            title="Hoe streng controleert de data-guard de output van deze in-lab tool?"
                          >
                            <option value="data">Guard: kan klantdata bevatten (streng)</option>
                            <option value="control">Guard: alleen technische metadata</option>
                          </select>
                        )}
                      </div>
                      <div className="mt-1 text-xs text-muted-foreground">{t.description}</div>
                      <details className="mt-1 text-xs">
                        <summary className="cursor-pointer text-muted-foreground">Input-schema</summary>
                        <pre className="mt-1 overflow-auto rounded bg-secondary p-2">{JSON.stringify(t.argument, null, 2)}</pre>
                      </details>
                    </Card>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── Skills (the wizard) ─────────────────────────────────────────────────────

function SkillsSection() {
  const [skills, setSkills] = useState<SkillDto[]>([]);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [editing, setEditing] = useState<SkillDto | null>(null);

  function refresh() {
    skillApi.list().then(setSkills);
  }
  useEffect(refresh, []);

  return (
    <div>
      <div className="mb-3 flex justify-between">
        <h2 className="text-sm font-semibold text-muted-foreground">Skills</h2>
        <Button onClick={() => setWizardOpen(true)}>+ Nieuwe skill</Button>
      </div>
      {skills.length === 0 ? (
        <EmptyState>Nog geen skills. De wizard laat je tools kiezen en per tool instructies + het input-schema zien.</EmptyState>
      ) : (
        <div className="space-y-2">
          {skills.map((s) => (
            <Card key={s.id} className="p-3">
              <div className="flex items-center justify-between">
                <span
                  className="flex-1 cursor-pointer font-medium text-sm"
                  onClick={async () => setEditing(await skillApi.get(s.id))}
                >
                  {s.display_name || s.name}
                </span>
                <button
                  onClick={() => skillApi.update(s.id, { is_enabled: !s.is_enabled }).then(refresh)}
                  title="Aan/uit"
                >
                  <Badge tone={s.is_enabled ? "green" : "neutral"}>{s.is_enabled ? "aan" : "uit"}</Badge>
                </button>
              </div>
              <div
                className="cursor-pointer text-xs text-muted-foreground"
                onClick={async () => setEditing(await skillApi.get(s.id))}
              >
                {s.description}
              </div>
              <div className="mt-1 flex items-center justify-between">
                <span className="text-xs text-muted-foreground">{s.tools.length} gekoppelde tool(s)</span>
                <Button
                  variant="danger"
                  className="px-2 py-0.5 text-xs"
                  onClick={() => {
                    if (confirm(`Skill "${s.display_name || s.name}" verwijderen?`)) skillApi.remove(s.id).then(refresh);
                  }}
                >
                  Verwijderen
                </Button>
              </div>
            </Card>
          ))}
        </div>
      )}
      {wizardOpen && (
        <SkillWizard
          onClose={() => setWizardOpen(false)}
          onSaved={() => {
            setWizardOpen(false);
            refresh();
          }}
        />
      )}
      {editing && (
        <SkillWizard
          existing={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            refresh();
          }}
        />
      )}
    </div>
  );
}

interface WizardToolChoice {
  tool: ToolDto;
  selected: boolean;
  instructions: string;
}

function SkillWizard({ existing, onClose, onSaved }: { existing?: SkillDto; onClose: () => void; onSaved: () => void }) {
  const [step, setStep] = useState(0);
  const [name, setName] = useState(existing?.name || "");
  const [description, setDescription] = useState(existing?.description || "");
  const [instructions, setInstructions] = useState(existing?.instructions || "");
  const [isEnabled, setIsEnabled] = useState(existing?.is_enabled ?? true);
  const [allTools, setAllTools] = useState<ToolDto[]>([]);
  const [choices, setChoices] = useState<WizardToolChoice[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    toolApi.list().then((tools) => {
      setAllTools(tools);
      const existingLinks = new Map((existing?.tools || []).map((l: SkillToolLink) => [l.tool_id, l]));
      setChoices(
        tools.map((t) => ({
          tool: t,
          selected: existingLinks.has(t.id),
          instructions: existingLinks.get(t.id)?.instructions || "",
        })),
      );
    });
  }, [existing]);

  const grouped = allTools.reduce<Record<string, ToolDto[]>>((acc, t) => {
    const key = t.mcp_server ? t.mcp_server.name : "(overig)";
    (acc[key] ||= []).push(t);
    return acc;
  }, {});

  function setChoice(toolId: number, patch: Partial<WizardToolChoice>) {
    setChoices((prev) => prev.map((c) => (c.tool.id === toolId ? { ...c, ...patch } : c)));
  }

  async function save() {
    try {
      let skill = existing;
      const payload = { name, description, instructions, is_enabled: isEnabled };
      if (existing) {
        skill = await skillApi.update(existing.id, payload);
      } else {
        skill = await skillApi.create(payload);
      }
      const tools = choices.filter((c) => c.selected).map((c) => ({ tool_id: c.tool.id, instructions: c.instructions }));
      await skillApi.setTools(skill!.id, tools);
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Opslaan mislukt");
    }
  }

  const steps = ["Doel", "Tools kiezen", "Instructies per tool", "Overzicht"];

  return (
    <Modal open onClose={onClose} title={existing ? "Skill bewerken" : "Nieuwe skill"} wide>
      <div className="mb-3 flex gap-2 text-xs text-muted-foreground">
        {steps.map((s, i) => (
          <span key={s} className={i === step ? "font-semibold text-primary" : ""}>
            {i + 1}. {s}
          </span>
        ))}
      </div>

      {step === 0 && (
        <div className="space-y-3">
          <div>
            <Label>Naam</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div>
            <Label>Beschrijving (voor de tool-catalogus die de agent ziet)</Label>
            <TextArea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
          </div>
          <div>
            <Label>Algemene instructies (how-to, altijd meegegeven als de skill aan staat)</Label>
            <TextArea rows={3} value={instructions} onChange={(e) => setInstructions(e.target.value)} />
          </div>
          <Toggle checked={isEnabled} onChange={setIsEnabled} label="Skill ingeschakeld" />
        </div>
      )}

      {step === 1 && (
        <div className="max-h-96 space-y-4 overflow-y-auto">
          <p className="text-xs text-muted-foreground">
            Let op: dit is optioneel. Een tool werkt ook zonder skill zodra hij is toegestaan op een
            lab (Labs → lab openen → tab "Toegang"). Een skill voegt alleen how-to-instructies toe.
          </p>
          {Object.entries(grouped).map(([serverName, tools]) => (
            <div key={serverName}>
              <div className="mb-1 text-xs font-semibold text-muted-foreground">{serverName}</div>
              <div className="space-y-1">
                {tools.map((t) => {
                  const choice = choices.find((c) => c.tool.id === t.id);
                  return (
                    <label key={t.id} className="flex items-start gap-2 rounded p-1.5 hover:bg-secondary">
                      <input type="checkbox" className="mt-0.5" checked={choice?.selected || false} onChange={(e) => setChoice(t.id, { selected: e.target.checked })} />
                      <div>
                        <div className="text-sm">{t.name}</div>
                        <div className="text-xs text-muted-foreground">{t.description}</div>
                      </div>
                    </label>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}

      {step === 2 && (
        <div className="max-h-96 space-y-4 overflow-y-auto">
          {choices.filter((c) => c.selected).length === 0 && <p className="text-sm text-muted-foreground">Geen tools gekozen — ga terug naar stap 2.</p>}
          {choices
            .filter((c) => c.selected)
            .map((c) => (
              <Card key={c.tool.id} className="p-3">
                <div className="mb-1 text-sm font-medium">{c.tool.name}</div>
                <details className="mb-2 text-xs">
                  <summary className="cursor-pointer text-muted-foreground">Input-schema (leidraad voor de instructie)</summary>
                  <pre className="mt-1 overflow-auto rounded bg-secondary p-2">{JSON.stringify(c.tool.argument, null, 2)}</pre>
                </details>
                <Label>Instructie voor de agent bij het gebruik van deze tool</Label>
                <TextArea rows={2} value={c.instructions} onChange={(e) => setChoice(c.tool.id, { instructions: e.target.value })} placeholder="bv. 'Roep dit altijd eerst aan met limit=5.'" />
              </Card>
            ))}
        </div>
      )}

      {step === 3 && (
        <div className="space-y-2 text-sm">
          <div>
            <strong>{name}</strong> — {description}
          </div>
          <div className="text-muted-foreground">{instructions}</div>
          <div className="mt-2">
            {choices.filter((c) => c.selected).length} tool(s) gekoppeld met instructies.
          </div>
        </div>
      )}

      {error && <p className="mt-2 text-sm text-destructive">{error}</p>}

      <div className="mt-4 flex justify-between">
        <Button variant="secondary" disabled={step === 0} onClick={() => setStep(step - 1)}>
          Terug
        </Button>
        {step < steps.length - 1 ? (
          <Button onClick={() => setStep(step + 1)} disabled={step === 0 && !name.trim()}>
            Volgende
          </Button>
        ) : (
          <Button onClick={save}>Opslaan</Button>
        )}
      </div>
    </Modal>
  );
}
