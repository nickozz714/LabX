/**
 * pages/BoardsPage.tsx — overzicht van agent boards.
 *
 * Een board is een kanban-bord dat aan een lab gekoppeld kan worden; die
 * koppeling is wat het een *agent* board maakt (de agent werkt in dat lab als
 * hij een ticket oppakt). Optioneel hangt er een externe bron onder: Azure
 * DevOps of Jira.
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { boardApi } from "@/lib/boards";
import { labsApi } from "@/lib/labs";
import type { BoardDto, Lab, ProviderSpec } from "@/lib/types";
import { Badge, Button, Card, EmptyState, Input, Label, Modal, Select } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { KanbanSquare, RefreshCw, Boxes, AlertTriangle } from "lucide-react";

export function BoardsPage() {
  const [boards, setBoards] = useState<BoardDto[]>([]);
  const [creating, setCreating] = useState(false);
  const [syncing, setSyncing] = useState<number | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const navigate = useNavigate();

  function refresh() {
    boardApi.list().then(setBoards);
  }
  useEffect(refresh, []);

  async function sync(board: BoardDto) {
    setSyncing(board.id);
    setNotice(null);
    try {
      const stats = await boardApi.sync(board.id);
      setNotice(
        `${board.name}: ${stats.created_local} nieuw, ${stats.updated_local} bijgewerkt, ` +
          `${stats.pushed + stats.created_external} teruggeschreven` +
          (stats.errors.length ? ` — ${stats.errors.length} fout(en)` : ""),
      );
      refresh();
    } catch (err) {
      setNotice(err instanceof ApiError ? `${board.name}: ${err.message}` : "Synchronisatie mislukt");
    } finally {
      setSyncing(null);
    }
  }

  return (
    <div className="p-6">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-bold">Boards</h1>
        <Button onClick={() => setCreating(true)}>+ Nieuw board</Button>
      </div>

      {notice && (
        <Card className="mb-3 p-3 text-sm">
          {notice}
          <button className="ml-2 text-xs text-muted-foreground" onClick={() => setNotice(null)}>
            sluiten
          </button>
        </Card>
      )}

      {boards.length === 0 ? (
        <EmptyState>
          Nog geen boards. Een board is een kanban-bord met tickets die de agent kan oppakken —
          koppel het aan een lab, en optioneel aan Azure DevOps of Jira.
        </EmptyState>
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          {boards.map((b) => (
            <Card key={b.id} className="p-4">
              <div className="flex items-start justify-between gap-2">
                <button
                  className="flex items-center gap-2 text-left font-semibold hover:underline"
                  onClick={() => navigate(`/boards/${b.id}`)}
                >
                  <KanbanSquare size={16} /> {b.name}
                </button>
                <Badge tone={b.provider === "local" ? "neutral" : "violet"}>
                  {b.provider === "local" ? "LabX" : b.provider === "jira" ? "Jira" : "Azure DevOps"}
                </Badge>
              </div>
              {b.description && <p className="mt-1 text-xs text-muted-foreground">{b.description}</p>}

              <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <span className="inline-flex items-center gap-1">
                  <Boxes size={12} />
                  {b.lab_id ? (
                    <>
                      {b.lab_name || b.lab_id.slice(0, 8)}
                      <Badge tone={b.lab_status === "running" ? "green" : "yellow"}>{b.lab_status}</Badge>
                    </>
                  ) : (
                    "geen lab gekoppeld"
                  )}
                </span>
                <span>· {b.ticket_total ?? 0} ticket(s)</span>
                {b.provider !== "local" && (
                  <span>· {b.sync_direction === "two_way" ? "two-way sync" : "alleen lezen"}</span>
                )}
              </div>

              {b.last_sync_error && (
                <p className="mt-2 flex items-start gap-1 text-xs text-destructive">
                  <AlertTriangle size={12} className="mt-0.5 shrink-0" />
                  {b.last_sync_error}
                </p>
              )}

              <div className="mt-3 flex gap-2">
                <Button variant="secondary" className="text-xs" onClick={() => navigate(`/boards/${b.id}`)}>
                  Openen
                </Button>
                {b.provider !== "local" && (
                  <Button
                    variant="secondary"
                    className="text-xs"
                    disabled={syncing === b.id}
                    onClick={() => sync(b)}
                  >
                    <RefreshCw size={12} className={syncing === b.id ? "animate-spin" : ""} />
                    {syncing === b.id ? "Bezig…" : "Sync"}
                  </Button>
                )}
              </div>
            </Card>
          ))}
        </div>
      )}

      {creating && (
        <CreateBoardModal
          onClose={() => setCreating(false)}
          onCreated={(board) => {
            setCreating(false);
            refresh();
            navigate(`/boards/${board.id}`);
          }}
        />
      )}
    </div>
  );
}

function CreateBoardModal({ onClose, onCreated }: { onClose: () => void; onCreated: (b: BoardDto) => void }) {
  const [name, setName] = useState("");
  const [keyPrefix, setKeyPrefix] = useState("");
  const [description, setDescription] = useState("");
  const [labId, setLabId] = useState("");
  const [provider, setProvider] = useState<string>("local");
  const [labs, setLabs] = useState<Lab[]>([]);
  const [providers, setProviders] = useState<ProviderSpec[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    labsApi.list().then(setLabs);
    boardApi.providers().then(setProviders);
  }, []);

  const spec = providers.find((p) => p.key === provider);

  async function submit() {
    try {
      const board = await boardApi.create({
        name,
        key_prefix: keyPrefix.trim() || undefined,
        description: description.trim() || undefined,
        lab_id: labId || undefined,
        provider,
      });
      onCreated(board);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Aanmaken mislukt");
    }
  }

  return (
    <Modal open onClose={onClose} title="Nieuw board">
      <div className="space-y-3">
        <div className="grid grid-cols-3 gap-3">
          <div className="col-span-2">
            <Label>Naam</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Platformwerk" />
          </div>
          <div>
            <Label>Ticket-prefix</Label>
            <Input
              value={keyPrefix}
              onChange={(e) => setKeyPrefix(e.target.value.toUpperCase())}
              placeholder="LAB"
              maxLength={16}
            />
          </div>
        </div>
        <div>
          <Label>Omschrijving</Label>
          <Input value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>
        <div>
          <Label>Gekoppeld lab</Label>
          <Select value={labId} onChange={(e) => setLabId(e.target.value)}>
            <option value="">Geen lab (agent kan nog niets oppakken)</option>
            {labs.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name} — {l.status}
              </option>
            ))}
          </Select>
          <p className="mt-1 text-xs text-muted-foreground">
            De agent werkt ín dit lab wanneer hij een ticket van dit board oppakt.
          </p>
        </div>
        <div>
          <Label>Bron</Label>
          <Select value={provider} onChange={(e) => setProvider(e.target.value)}>
            {providers.map((p) => (
              <option key={p.key} value={p.key}>
                {p.name}
              </option>
            ))}
          </Select>
          {spec && <p className="mt-1 text-xs text-muted-foreground">{spec.description}</p>}
          {provider !== "local" && (
            <p className="mt-1 text-xs text-muted-foreground">
              De verbindingsgegevens vul je na het aanmaken in bij de board-instellingen.
            </p>
          )}
        </div>
        {error && <p className="text-sm text-destructive">{error}</p>}
        <Button className="w-full" onClick={submit} disabled={!name.trim()}>
          Aanmaken
        </Button>
      </div>
    </Modal>
  );
}
