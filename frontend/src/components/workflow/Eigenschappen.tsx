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
import { TekstMetVerwijzingen } from "@/components/workflow/TekstMetVerwijzingen";

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

export function Eigenschappen({ node, onChange, onDelete, verwijzingen = [], lijsten = [],
                                groepen = [] }: {
  node: WorkflowNode | null;
  onChange: (n: WorkflowNode) => void;
  onDelete: (id: string) => void;
  /** De lussen en bubbels op het doek, om een activiteit in te kunnen zetten
   *  zonder te slepen. */
  groepen?: WorkflowNode[];
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
  // Zit deze activiteit in een lus? Dat weten we aan de verwijzingen die de
  // server teruggeeft: `item` bestaat alleen binnen een lus.
  const inLus = verwijzingen.some((v) => v.pad === "item");

  return (
    <div className="space-y-3 p-4">
      <div>
        <Label>Naam</Label>
        <Input value={node.naam} onChange={(e) => zet({ naam: e.target.value })} />
        <p className="mt-1 text-[11px] text-muted-foreground">
          Verwijzen doe je met <code>stap.{node.sleutel}.uitvoer</code>
        </p>
      </div>

      {node.type !== "parallel" && node.type !== "voorelk" && groepen.length > 0 && (
        <div>
          <Label>Zit in</Label>
          <Select
            value={node.groep || ""}
            onChange={(e) => {
              const id = e.target.value || undefined;
              const doel = groepen.find((g) => g.id === id);
              // Een nieuwe plek erbij: binnen een groep is de positie relatief
              // aan die groep, daarbuiten aan het doek. Zonder dit zou hij op
              // een onzichtbare plek belanden.
              const positie = doel
                ? { x: 24, y: 56 }
                : { x: (node.positie?.x ?? 0) + ((groepen.find(
                      (g) => g.id === node.groep)?.positie?.x) ?? 0) + 40,
                    y: (node.positie?.y ?? 0) + ((groepen.find(
                      (g) => g.id === node.groep)?.positie?.y) ?? 0) + 40 };
              zet({ groep: id, positie });
            }}
          >
            <option value="">De hoofdstroom</option>
            {groepen.map((g) => (
              <option key={g.id} value={g.id}>
                {g.type === "voorelk" ? "↻ lus" : "⇉ bubbel"} — {g.naam}
              </option>
            ))}
          </Select>
          <p className="mt-1 text-[11px] text-muted-foreground">
            In een lus draait deze activiteit één keer per element; in een bubbel tegelijk
            met de andere. Slepen op het doek doet hetzelfde.
          </p>
        </div>
      )}

      {node.type === "agent" && (
        <>
          <TekstMetVerwijzingen
            label="Opdracht" waarde={node.prompt || ""} opties={verwijzingen} rijen={7}
            onChange={(tekst) => zet({ prompt: tekst })}
            placeholder={inLus
              ? "Werk dit element af: {{ item }}"
              : "Wat moet de agent doen? Kies hierboven wat je uit een eerdere stap nodig hebt."}
            hint={inLus
              ? "Je zit in een lus: {{ item }} is het element van deze ronde, {{ iteratie }} de hoeveelste."
              : undefined} />

          <TekstMetVerwijzingen
            label="Rol (optioneel)" waarde={node.rol || ""} opties={verwijzingen} rijen={2}
            onChange={(tekst) => zet({ rol: tekst })}
            placeholder="Je bent reviewer. Wees streng en wijzig niets."
            hint="Gaat vóór de opdracht mee. Zinvol bij een beoordelaar of een stap met een andere houding dan de rest." />
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
          <TekstMetVerwijzingen
            label="Commando" waarde={node.commando || ""} opties={verwijzingen} rijen={4} mono
            onChange={(tekst) => zet({ commando: tekst })}
            placeholder="echo {{ item }}" />
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

      {inLus && node.type !== "als" && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-2 text-[11px] leading-relaxed">
          Deze activiteit zit in een lus en draait dus al één keer per element. Gebruik
          <code className="mx-1">{"{{ item }}"}</code>om verder te werken met het element van
          deze ronde.
        </div>
      )}

      {!inLus && node.type !== "parallel" && node.type !== "voorelk" && node.type !== "als" && (
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
