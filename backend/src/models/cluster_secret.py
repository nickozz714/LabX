# models/cluster_secret.py
#
# De kluis van één cluster.
#
# Er was al een kluis per lab (lab_secret.py) en een die overal geldt
# (secret.py). Wat ontbrak zat er precies tussenin, en dat is waar een cluster
# woont: een geheim dat bij deze APPLICATIE hoort en bij elk branch-lab ervan,
# maar niet daarbuiten.
#
# **Waarom dit niet met de gedeelde kluis kon.** Daar is de naam globaal uniek
# -- bewust, want "een naam die twee dingen kan betekenen is geen geheim maar
# een gok". Maar een cluster verwijst naar geheimen met de naam van een
# OMGEVINGSVARIABELE: JWT_SECRET, DB_PASS, API_KEY. Die namen zijn juist niet
# uniek; elke applicatie heeft ze. Twee clusters zouden dus om dezelfde
# `JWT_SECRET` vechten, en wie het laatst opslaat wint -- zonder dat iemand
# het merkt, want de waarde is nergens te zien.
#
# Hier is de naam uniek BINNEN een cluster, en dat is precies de bedoeling.
# Wordt een naam hier niet gevonden, dan telt de gedeelde kluis nog steeds:
# zo kan een toegangstoken één keer bestaan terwijl JWT_SECRET per cluster
# verschilt.
from __future__ import annotations

from sqlalchemy import Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base


class ClusterSecret(Base):
    """Eén geheim dat alleen in dit cluster en zijn branch-labs geldt."""

    __tablename__ = "cluster_secrets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cluster_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    #: Meestal de naam van de omgevingsvariabele waar hij in moet komen.
    naam: Mapped[str] = mapped_column(String(120), nullable=False)
    #: Fernet-versleuteld. Komt NOOIT terug uit de API: die zegt alleen DAT er
    #: een waarde is en hoe lang hij is -- genoeg om te zien of je de juiste
    #: hebt geplakt, te weinig om hem te gebruiken.
    waarde_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    omschrijving: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)
    last_used_at: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint("cluster_id", "naam", name="uq_cluster_secret"),
    )
