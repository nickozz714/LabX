# models/voice.py — de spraaklaag: sessies, de tijdlijn, en wachtende acties.
#
# Drie tabellen, en de scheiding ertussen is met opzet.
#
# - `voice_sessions` is één gesprek: wanneer begonnen, met welk brein, wat het
#   tot nu toe gekost heeft.
# - `voice_events` is de TIJDLIJN. Eén tabel voor wat jij zei, wat de
#   assistent zei, welke tool dat werd en wat eruit kwam — bewust niet drie
#   lijsten naast elkaar, want de volgorde ertussen is juist de informatie.
#   Dit is tegelijk het auditspoor: elke schrijfactie is hier terug te voeren
#   op de bevestigingszin én op het antwoord dat jij gaf.
# - `voice_pending_actions` is een schrijfactie die op bevestiging wacht. Die
#   staat in de DATABASE en niet in het geheugen van het model, want dat is
#   het hele punt: de server houdt vast wat hij gaat uitvoeren, stelt daar de
#   bevestigingszin uit samen, en voert na een "ja" exact díé parameters uit.
#   Zou het model de zin zelf verzinnen, dan kan het iets anders voorlezen dan
#   het uitvoert.
from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base

# Welk brein de sessie aanstuurt. Twee smaken, dezelfde acties eronder:
# "realtime" = het spraakmodel van OpenAI doet intentie én stem;
# "pipeline" = transcriberen, Claude als brein via de bestaande CLI, en
# lokaal voorlezen.
BREINEN = ("realtime", "pipeline")

# De soorten regels op de tijdlijn.
GEBEURTENISSEN = (
    "gebruiker",      # wat jij zei (transcriptie)
    "assistent",      # wat de assistent zei
    "actie",          # een tool werd aangeroepen, met resultaat
    "bevestiging",    # de zin die de server opstelde
    "antwoord",       # jouw ja/nee daarop
    "fout",
    "systeem",        # sessie gestart/gestopt, stand gewijzigd
)


class VoiceSession(Base):
    __tablename__ = "voice_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    brein: Mapped[str] = mapped_column(String(16), nullable=False, default="pipeline")
    # Hoe er geluisterd wordt: "ptt" (push-to-talk) of "open". Staat per
    # sessie en niet alleen in de instellingen, want je mag hem tijdens een
    # gesprek omzetten en achteraf moet te zien zijn wat gold.
    microfoon: Mapped[str] = mapped_column(String(16), nullable=False, default="ptt")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="actief")

    # Lopend totaal, zodat het dagplafond en de teller in het scherm uit
    # dezelfde bron komen.
    kosten_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    beurten: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    started_at: Mapped[str] = mapped_column(String(64), nullable=False)
    ended_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    einde_reden: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (Index("idx_voice_sessions_start", "started_at"),)


class VoiceEvent(Base):
    __tablename__ = "voice_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("voice_sessions.id", ondelete="CASCADE"), nullable=False)
    ts: Mapped[str] = mapped_column(String(64), nullable=False)
    soort: Mapped[str] = mapped_column(String(16), nullable=False)
    tekst: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Alleen bij soort="actie": welke tool, met welke parameters, en wat eruit
    # kwam. Compact gehouden — de tijdlijn is om terug te lezen, niet om een
    # volledige API-respons in te bewaren.
    tool: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parameters: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    resultaat: Mapped[str | None] = mapped_column(Text, nullable=True)

    kosten_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (Index("idx_voice_events_sessie", "session_id", "ts"),)


class VoicePendingAction(Base):
    """Een schrijfactie die op bevestiging wacht.

    De `zin` is door de SERVER opgesteld uit `parameters` — de velden die hij
    daadwerkelijk gaat uitvoeren. Het model leest hem alleen voor. Daardoor
    kan er geen licht zitten tussen waar je ja tegen zegt en wat er gebeurt.
    """

    __tablename__ = "voice_pending_actions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("voice_sessions.id", ondelete="CASCADE"), nullable=False)

    tool: Mapped[str] = mapped_column(String(64), nullable=False)
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    zin: Mapped[str] = mapped_column(Text, nullable=False)

    # "wacht" → "bevestigd" | "geweigerd" | "verlopen"
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="wacht")
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    # Twintig seconden na aanmaken. Daarna is "ja" een gok naar wat er ook
    # alweer gevraagd werd, dus dan vervalt hij en vraagt de assistent opnieuw.
    expires_at: Mapped[str] = mapped_column(String(64), nullable=False)
    resolved_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resultaat: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("idx_voice_pending_sessie", "session_id", "status"),)


class VoiceConcept(Base):
    """Een opdracht die over meerdere beurten wordt opgebouwd.

    Dit staat met opzet in de DATABASE en niet in het geheugen van het model.
    Een model dat zelf een concept onthoudt, vult op den duur velden in die je
    nooit gezegd hebt -- en bij een planning of een workflow merk je dat pas
    als het ding draait. De server weet wat er gevraagd is en wat er ingevuld
    staat; het model stelt alleen de vraag en geeft het antwoord door.
    """

    __tablename__ = "voice_concepten"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("voice_sessions.id", ondelete="CASCADE"),
        nullable=False, index=True)
    # "planning" of "workflow"
    soort: Mapped[str] = mapped_column(String(32), nullable=False)
    # Wat er tot nu toe is ingevuld.
    velden: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # bezig | afgerond | afgebroken
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="bezig")
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)
