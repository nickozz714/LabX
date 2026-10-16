"""
db/database.py — SQLite engine + session factory. LabX is single-tenant/single
-node, so SQLite (with WAL for concurrent reads during a chat stream) is
sufficient; no ORM abstraction over multiple DB backends like ND3X.
"""
from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(
    f"sqlite:///{settings.DB_PATH}",
    connect_args={"check_same_thread": False},
    future=True,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
    cur = dbapi_connection.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    # WAL laat veel lezers toe maar ÉÉN schrijver tegelijk. De standaard van
    # vijf seconden was te krap zodra er meer tegelijk gebeurt: twee chats die
    # hun voortgang bijwerken, een bordsync, de modelpool en de guard-audit.
    # Een schrijver die 30 seconden wacht is onzichtbaar; eentje die opgeeft
    # kost een gebruiker zijn antwoord (16-10-2026: "database is locked"
    # midden in een chat, waarna de run als "verdwenen" eindigde).
    cur.execute("PRAGMA busy_timeout=30000")
    # Duurzaamheid hoeft hier niet per transactie naar de schijf: WAL met
    # NORMAL is de aanbevolen combinatie en scheelt een fsync per commit --
    # precies waar die schrijvers op stonden te wachten.
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
