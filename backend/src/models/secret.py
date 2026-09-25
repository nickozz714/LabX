# models/secret.py
#
# De kluis die NIET aan één lab hangt.
#
# Er waren al geheimen per lab (models/lab_secret.py): die bestaan omdat een
# token in een shell-commando tekst is, en tekst reist door het audit-spoor,
# de prompt en de uitvoer. Dat probleem is niet beperkt tot een shell in een
# lab. Een webhook-URL in een skill, een API-sleutel in een tool-aanroep, een
# wachtwoord in een MCP-configuratie — overal waar iets getypt kan worden,
# staat nu de waarde zelf, en daarmee staat hij ook in de context van het
# model.
#
# Een geheim uit deze tabel wordt overal met `{{secret:naam}}` aangehaald. De
# waarde komt er pas in op het laatste moment, op weg naar BUITEN (een tool,
# een commando, een HTTP-aanroep) — nooit op weg naar het model. Die regel is
# de hele reden dat dit veilig is, en staat daarom ook in de resolver zelf.
from __future__ import annotations

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base


class Secret(Base):
    """Eén geheim dat overal in LabX te gebruiken is."""

    __tablename__ = "secrets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Waarmee je hem aanhaalt: {{secret:teams-webhook}}. Uniek, want een naam
    # die twee dingen kan betekenen is geen geheim maar een gok.
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Fernet-versleuteld (utils/crypto). Komt NOOIT terug uit de API: die geeft
    # alleen prijs DAT er een waarde is, en hoe lang hij is.
    value_encrypted: Mapped[str] = mapped_column(Text, nullable=False)

    # Waar hij gebruikt mag worden. Leeg = overal. Anders een lijst lab-id's;
    # een geheim van één klant hoort niet in het lab van een andere te werken.
    lab_ids: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)
    last_used_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Hoe vaak hij is ingevuld. Geen audit, wel het antwoord op "wordt dit ding
    # eigenlijk nog gebruikt" voordat je hem weggooit.
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (Index("idx_secrets_name", "name"),)
