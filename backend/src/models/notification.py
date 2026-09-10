# models/notification.py
#
# Meldingen naar buiten, en antwoorden weer naar binnen.
#
# Het ontwerp draait om één beperking: LabX staat op een prive server zonder
# domein en zonder open poort. Een provider kan hier dus niets AFLEVEREN — de
# gebruikelijke webhook bestaat niet. Alle tweerichtingsverkeer werkt daarom
# door te HALEN: Telegram met getUpdates, mail met IMAP. Dat is geen
# tussenoplossing maar hier de betere: geen tunnel, geen open poort, en niets
# dat stukgaat zodra het IP verandert.
#
# Twee tabellen:
#
# - `notification_channels` — waar naartoe, en waarvoor. Een kanaal is een
#   mailadres of een Telegram-chat plus de gebeurtenissen waar het op reageert.
# - `notification_log` — wat er verstuurd is, en waar het over ging. Die
#   tweede helft is het belangrijkst: zonder te weten bij WELK gesprek een
#   melding hoorde, kan een antwoord er niet naartoe terug. De `external_id`
#   (Telegram-message_id, of de Message-ID van de mail) is de sleutel die een
#   binnenkomend antwoord aan de juiste sessie koppelt.
from __future__ import annotations

from sqlalchemy import Boolean, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base

# email    — SMTP eruit, IMAP erin. Kost niets en hangt van geen enkele
#            externe dienst af behalve je eigen mailbox.
# telegram — een bot-token en een chat-id. Gratis, werkt op de telefoon als
#            een gewone chat, en getUpdates haalt antwoorden op zonder dat er
#            iets van buiten naar binnen hoeft.
CHANNEL_KINDS = ("email", "telegram")

# Waar een melding over kan gaan. Bewust grof: vier soorten die je in de
# praktijk anders behandelt, niet vijftien vinkjes die niemand instelt.
#
# run_klaar      — een agent-run is goed afgelopen (met de samenvatting)
# run_mislukt    — een agent-run is stukgelopen
# aandacht_nodig — er wacht iets op JOU: ticket geblokkeerd, planning
#                  gepauzeerd, of werk dat niet is afgemaakt. Dit is de
#                  gebeurtenis waar terugpraten het meest oplevert.
# planning_klaar — een hele reeks tickets is afgewerkt
# storing        — er is iets structureel mis: lab omgevallen, sync-fout,
#                  budget overschreden
EVENTS = ("run_klaar", "run_mislukt", "aandacht_nodig", "planning_klaar", "storing")


class NotificationChannel(Base):
    __tablename__ = "notification_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="email")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Niet-geheime instellingen: server, poort, afzender, ontvanger, chat-id,
    # en voor de inbox de laatst verwerkte positie (Telegram-offset,
    # IMAP-UID). Die laatste hoort hier en niet in een apart tabelletje: hij is
    # per kanaal en heeft verder geen leven.
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Wachtwoord / bot-token, Fernet-versleuteld (utils/crypto). Komt nooit
    # terug in een API-antwoord — de router geeft alleen `has_secret` prijs.
    secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Op welke gebeurtenissen dit kanaal afgaat. Leeg = alles.
    events: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Mag een antwoord op een melding via dit kanaal terug de sessie in? Uit
    # zetten maakt er een pure meldingskanaal van (bv. een gedeeld mailadres
    # waar meer mensen op zitten).
    allow_reply: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_sent_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_poll_at: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)


class NotificationLog(Base):
    __tablename__ = "notification_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    event: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    # sent | failed
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="sent")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Waar dit over ging: {thread_id, ticket_id, board_id, run_id, plan_id,
    # lab_id}. Dit is wat een ANTWOORD zijn weg terug wijst — zonder deze
    # context is een binnengekomen "ja, doe maar" een bericht zonder adres.
    context: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # De id die het kanaal aan dit bericht gaf: het message_id van Telegram of
    # de Message-ID van de mail. Een antwoord verwijst daarnaar (reply_to /
    # In-Reply-To) en zo vinden we deze regel terug.
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        Index("idx_notif_log_channel", "channel_id"),
        Index("idx_notif_log_external", "external_id"),
    )
