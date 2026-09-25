/**
 * components/workflow/Eigenschappen.tsx — het paneel rechts van het doek.
 *
 * Alles wat een activiteit anders maakt dan een doosje met tekst staat hier:
 * de opdracht, de rol, of hij met een schone sessie begint, of hij herhaalt,
 * en of hij mag mislukken. Bewust één paneel voor alle soorten, met per soort
 * alleen de velden die ertoe doen — een `als` heeft geen opdracht nodig en een
 * shell geen rol.
 *
 * Een voorwaarde is hier drie velden (links, operator, rechts) en geen vrije
 * tekst. Dat is niet alleen veiliger, het is ook in te vullen zonder te weten
 * hoe je een expressie schrijft.
 */
import type { Verwijzing, WorkflowConditie, WorkflowNode } from "@/lib/types";
import { Button, Input, Label, Select, TextArea, Toggle } from "@/components/ui";
import { SchemaBouwer } from "@/components/workflow/SchemaBouwer";
import { VerwijzingKiezer } from "@/components/workflow/VerwijzingKiezer";

const OPERATOREN = ["==", "!=", ">", ">=", "<", "<=", "bevat", "bevat_niet",
                    "is_leeg", "is_niet_leeg"];

function Conditie({ waarde, onChange, titel, hint, opties }: {
  waarde: WorkflowConditie | undefined;
  onChange: (c: WorkflowConditie) => void;
  titel: string;
  hint?: string;
  opties: Verwijzing[];
}) {
  const c = waarde || { links: "", operator: "==", rechts: "" };
  const zonderRechts = ["is_leeg", "is_niet_leeg"].includes(c.operator);
  return (
    <div className="space-y-1">
      <Label>{titel}</Label>
      <VerwijzingKiezer waarde={c.links} opties={opties}
                        onChange={(pad) => onChange({ ...c, links: pad })} />
      <div className="flex gap-1">
        <Select value={c.operator} className="w-32"
                onChange={(e) => onChange({ ...c, operator: e.target.value })}>
          {OPERATOREN.map((o) => <option key={o} value={o}>{o}</option>)}
        </Select>
        {!zonderRechts && (
          <Input value={String(c.rechts ?? "")} placeholder="0"
                 onChange={(e) => onChange({ ...c, rechts: e.target.value })} />
        )}
      </div>
      {hint && <p className="text-[11px] text-muted-foreground">{hint}</p>}
    </div>
  );
}

export function Eigenschappen({ node, onChange, onDelete, verwijzingen = [], lijsten = [] }: {
  node: WorkflowNode | null;
  onChange: (n: WorkflowNode) => void;
  onDelete: (id: string) => void;
  /** Waar deze activiteit naar kan verwijzen — uit de schema's van de andere
   *  activiteiten. Vult de keuzelijsten, zodat niemand een pad hoeft te raden. */
  verwijzingen?: Verwijzing[];
  /** De lijsten waar een lus langs kan lopen. */
  lijsten?: Verwijzing[];
}) {
  if (!node) {
    return (
      <div className="space-y-2 p-4 text-xs text-muted-foreground">
        <p className="font-medium text-foreground">Niets geselecteerd</p>
        <p>
          Klik een activiteit aan om hem in te stellen. Sleep activiteiten in een <strong>lus</strong>
          om ze per element van een lijst te laten draaien, of in een <strong>bubbel</strong> om ze
          tegelijk te doen. Verbind ze door van het
          <span className="mx-1 rounded bg-green-500/20 px-1">groene</span>punt te slepen
          (bij succes) of van het<span className="mx-1 rounded bg-red-500/20 px-1">rode</span>
          (bij fout). Een <strong>bubbel</strong> voert alles wat erin ligt tegelijk uit —
          sleep activiteiten erin of eruit.
        </p>
      </div>
    );
  }
  const zet = (velden: Partial<WorkflowNode>) => onChange({ ...node, ...velden });

  return (
    <div className="space-y-3 p-4">
      <div>
        <Label>Naam</Label>
        <Input value={node.naam} onChange={(e) => zet({ naam: e.target.value })} />
        <p className="mt-1 text-[11px] text-muted-foreground">
          Verwijzen doe je met <code>stap.{node.sleutel}.uitvoer</code>
        </p>
      </div>

      {node.type === "agent" && (
        <>
          <div>
            <Label>Opdracht</Label>
            <TextArea rows={7} value={node.prompt || ""}
                      onChange={(e) => zet({ prompt: e.target.value })}
                      placeholder={"Laad de tabel {{ item }} naar silver.\n\n"
                                   + "Verwijs naar eerdere activiteiten met {{ stap.x.uitvoer }}"} />
          </div>
          <div>
            <Label>Rol (optioneel)</Label>
            <TextArea rows={2} value={node.rol || ""}
                      onChange={(e) => zet({ rol: e.target.value })}
                      placeholder="Je bent reviewer. Wees streng en wijzig niets." />
            <p className="mt-1 text-[11px] text-muted-foreground">
              Gaat vóór de opdracht mee. Zinvol bij een beoordelaar of een stap met een
              andere houding dan de rest.
            </p>
          </div>
          <Toggle checked={Boolean(node.verse_sessie)}
                  onChange={(v) => zet({ verse_sessie: v })}
                  label="Schone sessie (vergeet wat eerder in deze run gebeurde)" />
          <div>
            <Label>Uitvoer van deze stap (optioneel)</Label>
            <SchemaBouwer waarde={node.json_schema}
                          onChange={(json) => zet({ json_schema: json })} />
          </div>
        </>
      )}

      {node.type === "shell" && (
        <>
          <div>
            <Label>Commando</Label>
            <TextArea rows={4} className="font-mono text-xs" value={node.commando || ""}
                      onChange={(e) => zet({ commando: e.target.value })} />
          </div>
          <div>
            <Label>Time-out (seconden)</Label>
            <Input type="number" value={node.timeout ?? 120}
                   onChange={(e) => zet({ timeout: Number(e.target.value) })} />
          </div>
        </>
      )}

      {node.type === "als" && (
        <Conditie titel="Voorwaarde" waarde={node.conditie} opties={verwijzingen}
                  onChange={(c) => zet({ conditie: c })}
                  hint="Klopt hij, dan gaat de run verder langs 'ja' — anders langs 'nee'." />
      )}

      {node.type === "wacht" && (
        <div>
          <Label>Wachten (seconden)</Label>
          <Input type="number" value={node.seconden ?? 30}
                 onChange={(e) => zet({ seconden: Number(e.target.value) })} />
        </div>
      )}

      {node.type === "voorelk" && (
        <>
          <div>
            <Label>Loop langs deze lijst</Label>
            <VerwijzingKiezer waarde={node.bron || ""} opties={lijsten}
                              onChange={(pad) => zet({ bron: pad })}
                              placeholder="stap.analyse.json.incidentGroups" />
            <p className="mt-1 text-[11px] text-muted-foreground">
              Alles wat je in deze lus sleept draait één keer per element. Binnen de lus
              gebruik je <code>{"{{ item }}"}</code> (en zijn velden, zoals
              <code className="ml-1">{"{{ item.title }}"}</code>) en
              <code className="ml-1">{"{{ iteratie }}"}</code>. Een <code>als</code> mag erin:
              die beslist dan per element.
            </p>
          </div>
          <div>
            <Label>Hoogstens zoveel elementen</Label>
            <Input type="number" value={node.max_items ?? 50}
                   onChange={(e) => zet({ max_items: Number(e.target.value) })} />
            <p className="mt-1 text-[11px] text-muted-foreground">
              Een lijst die onverwacht duizend lang is, is duizend agent-beurten.
            </p>
          </div>
          <div>
            <Label>Als een ronde mislukt</Label>
            <Select value={node.fout_gedrag || "stop"}
                    onChange={(e) => zet({ fout_gedrag: e.target.value })}>
              <option value="stop">Stoppen (volg de fout-verbinding)</option>
              <option value="doorgaan">Doorgaan met de volgende elementen</option>
            </Select>
          </div>
        </>
      )}

      {node.type === "parallel" && (
        <>
          <div>
            <Label>Hoeveel tegelijk</Label>
            <Input type="number" value={node.max_gelijktijdig ?? 4}
                   onChange={(e) => zet({ max_gelijktijdig: Number(e.target.value) })} />
            <p className="mt-1 text-[11px] text-muted-foreground">
              Elke tak krijgt een eigen sessie, en waar mogelijk een eigen werker. Zijn er
              minder werkers dan takken, dan draaien ze naast elkaar in dezelfde container —
              zelfde afweging als twee keer Claude op één pc.
            </p>
          </div>
          <div>
            <Label>Als één tak mislukt</Label>
            <Select value={node.fout_gedrag || "stop"}
                    onChange={(e) => zet({ fout_gedrag: e.target.value })}>
              <option value="stop">De bubbel mislukt (volg de fout-verbinding)</option>
              <option value="doorgaan">Doorgaan; de rest telt gewoon</option>
            </Select>
          </div>
        </>
      )}

      {node.type !== "parallel" && node.type !== "voorelk" && node.type !== "als" && (
        <div className="space-y-2 rounded-md border border-border p-2">
          <div className="text-xs font-semibold">Herhalen</div>
          <div>
            <Label>Voor elk element van</Label>
            <VerwijzingKiezer waarde={node.herhaal_over || ""} opties={lijsten}
                              onChange={(pad) => zet({ herhaal_over: pad })}
                              placeholder="stap.lijst.json.tabellen" />
            <p className="mt-1 text-[11px] text-muted-foreground">
              Herhaalt DEZE ene activiteit per element. Moeten er meerdere stappen per
              element gebeuren (of een <code>als</code> ertussen), gebruik dan een
              <strong> lus</strong> uit de balk links. Lege lijst = overslaan, en dat is
              geen fout.
            </p>
          </div>
          <Conditie titel="Of: herhalen tot" waarde={node.herhaal_tot} opties={verwijzingen}
                    onChange={(c) => zet({ herhaal_tot: c })} />
          <div>
            <Label>Hoogstens zoveel rondes</Label>
            <Input type="number" value={node.herhaal_max ?? 25}
                   onChange={(e) => zet({ herhaal_max: Number(e.target.value) })} />
          </div>
        </div>
      )}

      {node.type !== "parallel" && (
        <Toggle checked={Boolean(node.mag_falen)} onChange={(v) => zet({ mag_falen: v })}
                label="Mag mislukken zonder de run te stoppen" />
      )}

      <Button variant="danger" className="w-full" onClick={() => onDelete(node.id)}>
        Activiteit verwijderen
      </Button>
    </div>
  );
}
