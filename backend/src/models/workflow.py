# models/workflow.py
#
# Een workflow begon als een markdown-bestand met stappen: bij het uitvoeren
# gingen ALLE stappen in één prompt naar de agent, en daarna was het aan het
# model. Dat werkte, maar het betekende ook dat er tijdens het draaien geen
# stappen bestonden — en dus niets te vertakken, te herhalen of te loggen viel.
#
# Nu is een workflow een GRAAF van activiteiten (`nodes_json` + `edges_json`)
# die LabX zelf uitvoert: één activiteit per keer, met een eigen invoer,
# uitvoer en status. Daar komen `als`-vertakkingen, herhalingen en een echt
# runverslag uit voort; het zijn alle drie dezelfde afhankelijkheid.
#
# `markdown` en `steps_json` blijven bestaan als im- en export: een bestaande
# workflow wordt ingelezen als een rechte keten van agent-activiteiten, zodat
# niemand iets opnieuw hoeft te tekenen.
from __future__ import annotations

from sqlalchemy import Boolean, Float, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base


class Workflow(Base):
    __tablename__ = "workflows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")
    steps_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # De graaf. Leeg bij een workflow van vóór deze versie; die wordt bij het
    # eerste gebruik uit `steps_json` afgeleid (zie services/workflows/graph.py).
    nodes_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    edges_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Wat je bij het starten meegeeft, en wat elke activiteit met
    # `{{ invoer.<naam> }}` kan gebruiken. Zie services/workflows/parameters.py.
    parameters_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (Index("idx_workflows_enabled", "is_enabled"),)


class WorkflowRun(Base):
    """Eén uitvoering van een workflow — handmatig of uit een schedule.

    Ook een geplande run komt hier terecht. Daarvóór landde die in
    `schedule_runs`, en had hetzelfde ding twee geschiedenissen op twee
    schermen: je zag óf dat de cron had gedraaid, óf wat de workflow had
    gedaan, nooit allebei.
    """

    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workflow_id: Mapped[int] = mapped_column(Integer, nullable=False)
    lab_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # In welke werker (container) deze run draaide.
    worker_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trigger_type: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")  # manual|cron
    # Welke schedule hem startte, als het er een was.
    trigger_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # pending | running | completed | failed | cancelled
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    # De sessie waarin de activiteiten draaien. Standaard delen ze er één: de
    # agent onthoudt dan wat hij in de vorige stap deed. Per activiteit is een
    # verse sessie aan te zetten (zie graph.py).
    thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cli_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Waarden waarmee deze run begon; te gebruiken in expressies als invoer.x
    input_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Optelsom over de activiteiten heen: tokens, kosten, aantal stappen.
    totals_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    finished_at: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (Index("idx_workflow_runs_workflow", "workflow_id"),)


class WorkflowRunStep(Base):
    """Wat er in één activiteit gebeurde.

    Dit is de laag die er niet was. Een run bewaarde alleen de eindtekst, en
    de redenatie stond ergens in een chattranscript — dus bij een run die
    gisteren half misging viel niet na te gaan wát het model precies kreeg,
    wat het besloot en waarom het die kant op ging.

    Hier staat per activiteit: de invoer die het model kreeg, zijn antwoord,
    de tool-aanroepen ertussenin, het gestructureerde resultaat, hoe lang het
    duurde, wat het kostte, en welke verbinding er daarna genomen is.
    """

    __tablename__ = "workflow_run_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    naam: Mapped[str] = mapped_column(String(255), nullable=False)
    soort: Mapped[str] = mapped_column(String(16), nullable=False, default="agent")
    # Volgnummer binnen de run (1, 2, 3 ...) — de volgorde waarin het gebeurde,
    # die bij vertakkingen en herhalingen niet uit de graaf af te leiden is.
    volgnummer: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Bij een herhaling: de hoeveelste ronde, en waarover.
    iteratie: Mapped[int | None] = mapped_column(Integer, nullable=True)
    item: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    invoer: Mapped[str | None] = mapped_column(Text, nullable=True)
    uitvoer: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Het gestructureerde antwoord, als de activiteit een JSON-schema had.
    resultaat_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # De tool-aanroepen en tussendoor-gedachten, in dezelfde vorm als de chat.
    stappen_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Welke verbinding er na deze activiteit genomen is (succes/fout/ja/nee).
    tak: Mapped[str | None] = mapped_column(String(16), nullable=True)

    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    duur_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    finished_at: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (Index("idx_workflow_run_steps_run", "run_id", "volgnummer"),)
