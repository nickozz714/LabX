"""
services/lab/verkenner_catalogus.py

De dienstencatalogus: wat een omgevingssleutel tot een container maakt.

Dit is het stuk dat de verkenner algemeen maakt. Een repo zegt
`REDIS_URL=redis://localhost:6379` en bedoelt "ik heb een Redis nodig" — maar
nergens in die repo staat welk image dat is of welke poort erbij hoort. Dat
weten is niet de taak van de repo; het is de taak van LabX, en het staat
daarom hier, in een tabel die je kunt uitbreiden zonder code te schrijven.

**Twee tabellen, en de tweede is de belangrijkste.**

- `DIENSTEN` — sleutels die een container opleveren. Dingen met een emulator
  of een officieel image: een database, een broker, een opslagemulator.
- `EXTERNE_DIENSTEN` — sleutels die naar een ECHTE dienst wijzen waar geen
  emulator van bestaat. Application Insights, Langfuse, Locize. Die kun je
  niet nabouwen; je hebt er een geheim voor nodig, en dat moet vóór het eerste
  branch-lab in de kluis staan.

Wat in geen van beide tabellen voorkomt maar wel naar een geheim riekt
(`*_KEY`, `*_SECRET`, `*_TOKEN`, `*_PASSWORD`, `*_CONNECTION_STRING`) landt
op de tweede lijst als onbekend geheim. Liever een vraag te veel dan een
branch-lab dat veertig minuten bouwt en dan omvalt op een ontbrekende sleutel.

**Een emulatorsleutel is geen geheim.** De Cosmos-emulator heeft een vaste,
publiek gedocumenteerde sleutel. Draait de emulator in het cluster, dan hoeft
`COSMOS_KEY` dus niet uit de kluis te komen — die levert de catalogus zelf.
Dat scheelt de gebruiker een geheim dat nergens geheim aan is.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

#: De vaste sleutel van de Cosmos-emulator. Publiek gedocumenteerd door
#: Microsoft en identiek in elke emulator ter wereld — dit is geen geheim en
#: hoort daarom niet in de kluis.
COSMOS_EMULATOR_SLEUTEL = (
    "C2y6yDjf5/R+ob0N8A7Cgv30VRDJIWEHLM+4QDU5DE2nQ9nDuVTqobD4b8mGGyPMbIZnqyMsEcaGQy67XIw/Jw=="
)


@dataclass(frozen=True)
class DienstSjabloon:
    """Een dienst die LabX kan nabouwen als een container."""

    naam: str
    image: str
    poorten: Tuple[int, ...]
    #: Regex op de NAAM van de omgevingsvariabele.
    sleutelpatronen: Tuple[str, ...]
    #: Regex op de WAARDE, als de naam alleen niet genoeg onderscheidt.
    #: `DATABASE_URL` zegt niets; `postgres://` erin zegt alles.
    waardepatroon: Optional[str] = None
    #: Omgeving voor de container zelf (wachtwoord, accept-eula, ...).
    container_omgeving: Dict[str, str] = field(default_factory=dict)
    #: Wat een branch-lab meekrijgt zodat het deze dienst vindt. `{dienst}`
    #: wordt vervangen door de naam die de dienst in het cluster krijgt.
    lab_omgeving: Dict[str, str] = field(default_factory=dict)
    toelichting: str = ""


DIENSTEN: Tuple[DienstSjabloon, ...] = (
    DienstSjabloon(
        naam="cosmos",
        image="mcr.microsoft.com/cosmosdb/linux/azure-cosmos-emulator:vnext-preview",
        poorten=(8081, 1234),
        sleutelpatronen=(r"^COSMOS_(ENDPOINT|KEY|CONNECTION_STRING)$", r"^COSMOSDB_"),
        lab_omgeving={
            "COSMOS_ENDPOINT": "https://{dienst}:8081",
            "COSMOS_KEY": COSMOS_EMULATOR_SLEUTEL,
        },
        toelichting="Azure Cosmos DB-emulator. De sleutel is vast en publiek; die hoeft niet in de kluis.",
    ),
    DienstSjabloon(
        naam="postgres",
        image="postgres:16-alpine",
        poorten=(5432,),
        sleutelpatronen=(r"^(DATABASE_URL|DB_URL)$", r"^POSTGRES_"),
        waardepatroon=r"postgres(ql)?://",
        container_omgeving={"POSTGRES_PASSWORD": "labx", "POSTGRES_USER": "labx", "POSTGRES_DB": "labx"},
        lab_omgeving={"DATABASE_URL": "postgresql://labx:labx@{dienst}:5432/labx"},
    ),
    DienstSjabloon(
        naam="mysql",
        image="mysql:8",
        poorten=(3306,),
        sleutelpatronen=(r"^(DATABASE_URL|DB_URL)$", r"^MYSQL_"),
        waardepatroon=r"mysql://",
        container_omgeving={"MYSQL_ROOT_PASSWORD": "labx", "MYSQL_DATABASE": "labx"},
        lab_omgeving={"DATABASE_URL": "mysql://root:labx@{dienst}:3306/labx"},
    ),
    DienstSjabloon(
        naam="redis",
        image="redis:7-alpine",
        poorten=(6379,),
        sleutelpatronen=(r"^REDIS_(URL|HOST|CONNECTION_STRING)$",),
        lab_omgeving={"REDIS_URL": "redis://{dienst}:6379"},
    ),
    DienstSjabloon(
        naam="mongo",
        image="mongo:7",
        poorten=(27017,),
        sleutelpatronen=(r"^MONGO(DB)?_(URL|URI|CONNECTION_STRING)$",),
        lab_omgeving={"MONGODB_URI": "mongodb://{dienst}:27017"},
    ),
    DienstSjabloon(
        naam="rabbitmq",
        image="rabbitmq:3-management-alpine",
        poorten=(5672, 15672),
        sleutelpatronen=(r"^AMQP_URL$", r"^RABBITMQ_"),
        lab_omgeving={"AMQP_URL": "amqp://guest:guest@{dienst}:5672/"},
    ),
    DienstSjabloon(
        naam="azurite",
        image="mcr.microsoft.com/azure-storage/azurite",
        poorten=(10000, 10001, 10002),
        sleutelpatronen=(r"^AZURE_STORAGE_(CONNECTION_STRING|ACCOUNT)$", r"^AZURITE_"),
        waardepatroon=r"devstoreaccount1|UseDevelopmentStorage",
        toelichting="Azurite, de opslagemulator. Herkend aan het standaard-ontwikkelaccount in de waarde.",
    ),
    DienstSjabloon(
        naam="elasticsearch",
        image="docker.elastic.co/elasticsearch/elasticsearch:8.15.0",
        poorten=(9200,),
        sleutelpatronen=(r"^(ELASTIC|ELASTICSEARCH)_(URL|HOST|NODE)$",),
        container_omgeving={"discovery.type": "single-node", "xpack.security.enabled": "false"},
        lab_omgeving={"ELASTICSEARCH_URL": "http://{dienst}:9200"},
    ),
    DienstSjabloon(
        naam="mailpit",
        image="axllent/mailpit",
        poorten=(1025, 8025),
        sleutelpatronen=(r"^(SMTP|MAIL)_(HOST|URL|SERVER)$",),
        lab_omgeving={"SMTP_HOST": "{dienst}", "SMTP_PORT": "1025"},
        toelichting="Een mailvanger, zodat uitgaande post in het lab blijft.",
    ),
)


@dataclass(frozen=True)
class ExterneDienst:
    """Een echte dienst zonder emulator: je hebt er een geheim voor nodig."""

    naam: str
    sleutelpatronen: Tuple[str, ...]
    toelichting: str = ""


EXTERNE_DIENSTEN: Tuple[ExterneDienst, ...] = (
    ExterneDienst("application-insights", (r"^APP(LICATION)?INSIGHTS_",), "Telemetrie bij Azure."),
    ExterneDienst("langfuse", (r"^LANGFUSE_",), "Observatie van modelaanroepen."),
    ExterneDienst("locize", (r"^LOCIZE_",), "Vertalingen."),
    ExterneDienst("azure-openai", (r"^AZURE_OPENAI_",)),
    ExterneDienst("openai", (r"^OPENAI_(API_)?KEY$",)),
    ExterneDienst("azure-cognitive", (r"^(AZURE_)?COGNITIVE_",)),
    ExterneDienst("azure-servicebus", (r"^SERVICEBUS_", r"^AZURE_SERVICEBUS_"), "Geen emulator beschikbaar."),
    ExterneDienst("sentry", (r"^SENTRY_DSN$",)),
    ExterneDienst("stripe", (r"^STRIPE_",)),
    ExterneDienst("github", (r"^(GITHUB|GH)_(TOKEN|PAT)$",)),
)

#: Sleutels die nergens bij passen maar wel naar een geheim rieken. Komen op
#: de lijst "moet van buiten komen" als onbekend geheim.
GEHEIM_PATROON = re.compile(
    r"(_KEY|_SECRET|_TOKEN|_PASSWORD|_PASS|_CREDENTIAL|_CONNECTION_STRING|_DSN|_API_KEY)$",
    re.IGNORECASE,
)

#: Sleutels die er geheim uitzien maar het niet zijn: schakelaars, niveaus,
#: namen. Zonder deze lijst staat `LOG_LEVEL` als geheim op de lijst en gaat
#: niemand die lijst nog lezen.
NIET_GEHEIM = re.compile(r"^(NODE_ENV|LOG_LEVEL|PORT|HOST|DEBUG|CI|TZ)$", re.IGNORECASE)


def zoek_dienst(sleutel: str, waarde: str = "") -> Optional[DienstSjabloon]:
    """Welke na te bouwen dienst hoort bij deze omgevingssleutel?

    De waarde telt mee waar de naam niet onderscheidt: `DATABASE_URL` is pas
    een Postgres als er `postgres://` in staat. Staat er niets in de waarde
    (een `.env.example` laat die vaak leeg), dan valt zo'n sjabloon af — dat
    is bedoeld: dan is het een vondst voor het model, met lage zekerheid,
    en niet iets wat LabX hard beweert.
    """
    for sjabloon in DIENSTEN:
        if not any(re.search(p, sleutel) for p in sjabloon.sleutelpatronen):
            continue
        if sjabloon.waardepatroon and not re.search(sjabloon.waardepatroon, waarde or "", re.IGNORECASE):
            continue
        return sjabloon
    return None


def zoek_externe_dienst(sleutel: str) -> Optional[ExterneDienst]:
    for dienst in EXTERNE_DIENSTEN:
        if any(re.search(p, sleutel, re.IGNORECASE) for p in dienst.sleutelpatronen):
            return dienst
    return None


def is_geheim(sleutel: str) -> bool:
    """Riekt deze sleutel naar iets dat uit de kluis moet komen?"""
    if NIET_GEHEIM.match(sleutel):
        return False
    return bool(GEHEIM_PATROON.search(sleutel))


def lab_omgeving_voor(sjabloon: DienstSjabloon, dienstnaam: str) -> Dict[str, str]:
    """De omgeving die een branch-lab krijgt om deze dienst te vinden."""
    return {k: v.replace("{dienst}", dienstnaam) for k, v in sjabloon.lab_omgeving.items()}
