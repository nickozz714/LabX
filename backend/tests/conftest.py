"""Gedeelde fixtures.

`guard_db` is er omdat de data-guard sinds de scheiding classifier/guard zijn
regels uit de DATABASE haalt. Dat is precies de bedoeling — je kunt ze zelf
aanpassen — maar het betekent dat een test die de guard aanroept een database
met regels nodig heeft. Een SQLite-in-geheugen met de meegeleverde regels erin
is dan eerlijker dan de guard zijn regels afnemen: je test wat er in productie
ook draait.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture()
def guard_db(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from db.database import Base
    import models.guard_audit  # noqa: F401  (registreert de tabel)
    import models.guard_rule  # noqa: F401
    from services.lab.classifier import seed_standaardregels

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        models.guard_rule.GuardRule.__table__,
        models.guard_audit.GuardAudit.__table__,
    ])
    Sessie = sessionmaker(bind=engine)
    db = Sessie()
    seed_standaardregels(db)

    # De guard versleutelt wat hij in de audit zet. In een test hoeft dat geen
    # echte sleutel te zijn, maar hij mag er ook niet op omvallen.
    monkeypatch.setenv("LABX_FERNET_KEY",
                       "dGVzdC1zbGV1dGVsLXZvb3ItZGUtdW5pdC10ZXN0cy0wMDA9")
    try:
        yield db
    finally:
        db.close()
