# models/lab.py
#
# Ported from ND3X-public/src/models/playground.py, minus org_id/project_id
# (LabX is single-tenant). A Lab is an isolated Docker workspace: the agent
# works INSIDE the container, /workspace sits on a named volume so stop/start
# never loses work. The TTL-reaper cleans up expired labs.
from __future__ import annotations

from sqlalchemy import Boolean, Float, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base


class Lab(Base):
    __tablename__ = "labs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # creating | running | stopped | error | expired
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="creating")
    image: Mapped[str] = mapped_column(String(255), nullable=False)
    container_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    volume_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # docker-network alias the sibling container gets, so the backend can reach
    # a published lab port over DNS on the labx-labs bridge (see docker_runtime.py).
    network_alias: Mapped[str | None] = mapped_column(String(255), nullable=True)

    cpu_limit: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    mem_limit_mb: Mapped[int] = mapped_column(Integer, nullable=False, default=2048)
    allow_network: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Zoveel uur zonder gebruik blijft een lab staan; daarna stopt de reaper
    # hem (werker 1 incluis — die is de ondergrens van de autoscaler, geen
    # uitzondering op de TTL).
    ttl_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=14)
    expires_at: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Repos cloned into /workspace at creation:
    # [{name, url, source: registry|url, authenticated}] — tokens are NEVER stored.
    repos: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Container ports published on localhost (for APIs/MCP servers isolated in
    # the lab): [8000, ...].
    ports: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Data-egress-guard: container output heading to the LLM is screened
    # (services/lab/data_guard.py). Default ON.
    data_guard: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Local-model second opinion (data_guard_llm.py) on top of the rules —
    # catches the subtle aggregation tail. Per lab, default ON.
    llm_guard: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Per-lab ALLOWLIST: when a chat binds to this lab, the gateway strips the
    # CLI down to builtins + the container shell tool. These lists name
    # specific EXTERNAL MCP servers (slugs), tools (names) and skills (names)
    # to allow anyway. Empty = default (fully stripped).
    allowed_mcp: Mapped[list] = mapped_column(JSON, nullable=True, default=list)
    allowed_tools: Mapped[list] = mapped_column(JSON, nullable=True, default=list)
    allowed_skills: Mapped[list] = mapped_column(JSON, nullable=True, default=list)
    # The Azure identity a chat bound to this lab uses when it calls a HOST
    # MCP server that authenticates via Azure (Azure MCP Server, Fabric MCP,
    # ...) — takes priority over that server's own azure_profile_id, since a
    # chat is bound to one lab and "which Azure identity" is naturally a
    # per-lab choice. See services/azure/azure_mcp_auth.py.
    azure_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Welke resources in dit lab te claimen zijn (sleutels uit `claim_resources`).
    # NULL = de meegeleverde standaard, en dat is met opzet geen lege lijst: een
    # bestaand lab hoort de browser te kunnen reserveren zonder dat iemand eerst
    # een vinkje zet. Zie services/lab/resources.py.
    claim_resources: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # Hoeveel containers dit lab NU heeft (zie models/lab_worker.py). Dit is
    # een afgeleide: de autoscaler beweegt hem tussen min en max.
    worker_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # De ondergrens: zoveel werkers blijven altijd staan. Eén is genoeg — die
    # eerste draagt de identiteit van het lab (terminal, bestanden, poorten) en
    # gaat pas uit als het hele lab door zijn TTL heen valt.
    min_workers: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Het plafond. De autoscaler mag hier tot aan gaan als er werk staat te
    # wachten, en de agent mag er zelf om vragen — maar nooit erboven. Dit is
    # de knop van de mens: een agent die het druk heeft mag niet ongelimiteerd
    # containers op deze machine zetten. max = min betekent: niet schalen.
    max_workers: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Hoeveel sessies er TEGELIJK in één werker mogen draaien.
    #
    # Een lab is een sandbox-pc, en op een pc kun je ook twee keer Claude
    # draaien. Of dat handig is hangt af van het werk: twee sessies delen de
    # bestanden, de processen en de az-sessie van die container. Soms is dat
    # precies wat je wilt (twee onderzoeken naast elkaar in dezelfde omgeving),
    # soms precies niet (twee runs in dezelfde git-repo). Die afweging hoort
    # bij de gebruiker; LabX legt er alleen een plafond omheen.
    #
    # 1 = zoals het altijd was: één sessie per container, en werk wacht of
    # krijgt een eigen werker.
    sessies_per_werker: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Mag een chatbeurt bij een vol lab in een bezette werker landen?
    #
    # Een chat bezet net zo goed een container als een ticket, dus normaal
    # claimt hij er een. Is alles bezet en staat het plafond op slot, dan is er
    # een keuze te maken die niet voor iedereen hetzelfde uitpakt:
    #
    #   True  — de beurt gaat door in werker 1, naast het werk dat er al zit.
    #           Jij drukte op verzenden; je krijgt antwoord. Prijs: twee runs
    #           in dezelfde bestanden, processen en az-sessie.
    #   False — de beurt wordt geweigerd tot er een werker vrij is. Het lab
    #           blijft schoon; jij wacht.
    #
    # Standaard True, want dat is hoe het altijd werkte en het weigeren van een
    # mens is de ingrijpendere van de twee.
    chat_deelt_werker: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # ── inrichting bovenop het basis-image (zie models/lab_extra.py) ─────────
    # Keys uit `lab_extras` die in dit lab geïnstalleerd worden (Playwright +
    # Chromium, Node, ...). Zonder dit kon alleen een code-change bepalen wat
    # er in een lab zat.
    extras: Mapped[list] = mapped_column(JSON, nullable=True, default=list)
    # Vrij shell-script dat NA de extra's draait — voor het ene pakket waar
    # geen catalogus-entry voor is. Draait bij elk inrichten opnieuw, dus
    # schrijf het idempotent.
    setup_script: Mapped[str | None] = mapped_column(Text, nullable=True)
    # pending | running | ok | error | skipped — het inrichten loopt op de
    # achtergrond (een browser binnenhalen duurt minuten), dus de UI moet
    # kunnen zien hoe het ervoor staat.
    provision_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Per stap: {key, label, status, exit_code, output} van de laatste ronde.
    provision_log: Mapped[list] = mapped_column(JSON, nullable=True, default=list)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)
    last_used_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Het model waarmee in dit lab gewerkt wordt. Leeg = de standaard uit de
    # instellingen. Per lab en niet alleen per chat, omdat het werk aan een lab
    # hangt en niet aan een gesprek: een board-agent die tickets van dit lab
    # oppakt hoort hetzelfde model te gebruiken als de chat ernaast.
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Wat dit lab over zijn eigen wereld weet; zie services/lab/profielen.py.
    # Een lab dat in Fabric werkt produceert de hele dag technische uitvoer, en
    # een guard die dat niet weet blokkeert precies het werk waarvoor het lab
    # bestaat. Verschuift wat als NORMAAL geldt, niet wat er beschermd wordt.
    security_profile: Mapped[str] = mapped_column(
        String(32), nullable=False, default="generiek")

    __table_args__ = (
        Index("idx_labs_status", "status"),
        Index("idx_labs_expires", "expires_at"),
    )
