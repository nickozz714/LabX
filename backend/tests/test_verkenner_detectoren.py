"""De detectoren van de clusterverkenner: harde vondsten met een vindplaats.

Wat hier vastligt is de regel die het plan nakijkbaar houdt: **geen vondst
zonder vindplaats.** Een detector die wel een dienst voorstelt maar niet kan
zeggen wáár hij dat las, levert een mening op, en een plan vol meningen keurt
niemand nog goed.

Twee dingen die stil kunnen verdwijnen als iemand hier later aan sleutelt:

- Een compose-service met `build:` is de APPLICATIE, geen vaste dienst. Gaat
  die scheiding kapot, dan belandt de app in de vaste laag en draait elke
  branch tegen de code van wie het cluster aanmaakte.
- Van `8081:8081` nemen we de RECHTERkant. De linkerkant overnemen is hoe de
  emulator bij de eerste proef op een bezette poort botste.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.lab import verkenner_catalogus as cat  # noqa: E402
from services.lab.verkenner_detectoren import (  # noqa: E402
    Bewijs,
    Vondst,
    detecteer_alles,
    detecteer_compose,
    detecteer_env_profielen,
    detecteer_manifesten,
    detecteer_omgevingsbestanden,
    lees_env,
    staaf,
)

#: Een repo in de vorm van IntelligentHive: yarn-workspaces, geen compose,
#: Cosmos alleen zichtbaar in de omgevingssleutels.
HIVE = {
    "package.json": (
        '{"packageManager":"yarn@4.9.2","engines":{"node":">=22"},'
        '"workspaces":["components/*"],'
        '"scripts":{"build:dependencies":"a","build:apps":"b",'
        '"start:api":"c","start:app":"d","start:agent":"e"}}'
    ),
    "components/api/.env.example": (
        "NODE_ENV=development\n"
        "LOG_LEVEL=info\n"
        "COSMOS_ENDPOINT=\n"
        "COSMOS_KEY=\n"
        "APPLICATIONINSIGHTS_CONNECTION_STRING=\n"
        "LANGFUSE_SECRET_KEY=\n"
        "LOCIZE_API_KEY=\n"
        "API_KEY=\n"
    ),
    "components/app/.env.debug": "VITE_TENANT_ID=x\n",
    "components/app/.env.realestate": "VITE_TENANT_ID=y\n",
    "components/app/.env.insurance": "VITE_TENANT_ID=z\n",
}


def _van(vondsten, soort, sleutel=None):
    uit = [v for v in vondsten if v.soort == soort and (sleutel is None or v.sleutel == sleutel)]
    return uit[0] if uit else None


# ── de kernregel ─────────────────────────────────────────────────────────────

def test_elke_vondst_heeft_een_vindplaats():
    for vondst in detecteer_alles(HIVE):
        assert vondst.bewijs, f"{vondst.soort}/{vondst.sleutel} heeft geen bewijs"
        for bewijs in vondst.bewijs:
            assert bewijs.bestand in HIVE, f"bewijs wijst naar een bestand dat er niet is: {bewijs.bestand}"


# ── trede 3: de omgevingssleutels ────────────────────────────────────────────

def test_cosmos_volgt_uit_de_omgevingssleutels():
    """Zonder compose is dit de enige aanwijzing dat de emulator nodig is."""
    vondst = _van(detecteer_omgevingsbestanden(HIVE), "dienst", "cosmos")
    assert vondst is not None
    assert "cosmos-emulator" in vondst.waarde["image"]
    assert vondst.waarde["poorten"] == [8081, 1234]
    assert vondst.zekerheid == "midden"  # afgeleid, niet afgelezen
    regels = sorted(b.regel for b in vondst.bewijs)
    assert regels == [3, 4], "moet naar COSMOS_ENDPOINT en COSMOS_KEY wijzen"


def test_emulatorsleutel_komt_uit_de_catalogus_en_niet_uit_de_kluis():
    """De Cosmos-sleutel is publiek. Hem als geheim opvoeren is ruis."""
    vondst = _van(detecteer_omgevingsbestanden(HIVE), "dienst", "cosmos")
    assert vondst.waarde["lab_omgeving"]["COSMOS_KEY"] == cat.COSMOS_EMULATOR_SLEUTEL
    assert _van(detecteer_omgevingsbestanden(HIVE), "geheim", "COSMOS_KEY") is None


def test_externe_diensten_komen_op_de_tweede_lijst():
    """Het rijtje waar een branch-lab veertig minuten later op omvalt."""
    vondsten = detecteer_omgevingsbestanden(HIVE)
    namen = {v.sleutel for v in vondsten if v.soort == "extern"}
    assert {"application-insights", "langfuse", "locize"} <= namen
    for naam in namen:
        assert _van(vondsten, "dienst", naam) is None, "extern is geen na te bouwen dienst"


def test_onbekend_geheim_wordt_gemeld_maar_schakelaars_niet():
    vondsten = detecteer_omgevingsbestanden(HIVE)
    geheimen = {v.sleutel for v in vondsten if v.soort == "geheim"}
    assert "API_KEY" in geheimen
    assert "NODE_ENV" not in geheimen and "LOG_LEVEL" not in geheimen


def test_database_url_zonder_waarde_levert_geen_harde_dienst():
    """`DATABASE_URL=` zegt niet of het postgres of mysql is. Dan niets beweren."""
    beeld = {".env.example": "DATABASE_URL=\n"}
    assert _van(detecteer_omgevingsbestanden(beeld), "dienst") is None
    beeld = {".env.example": "DATABASE_URL=postgresql://u:p@localhost/db\n"}
    assert _van(detecteer_omgevingsbestanden(beeld), "dienst", "postgres") is not None


def test_echte_envwaarden_komen_niet_in_het_plan():
    """Een `.env` kan geldige sleutels bevatten. Alleen de NAAM mag eruit."""
    beeld = {".env": "STRIPE_SECRET_KEY=sk_live_heelgeheim\n"}
    vondst = _van(detecteer_omgevingsbestanden(beeld), "extern", "stripe")
    assert vondst is not None
    for bewijs in vondst.bewijs:
        assert "sk_live_heelgeheim" not in bewijs.fragment


def test_meerdere_profielen_worden_een_keuze():
    vondst = _van(detecteer_env_profielen(HIVE), "omgeving", "env_profiel")
    assert vondst is not None
    assert vondst.waarde["keuzes"] == ["debug", "insurance", "realestate"]
    assert vondst.waarde["standaard"] == "debug"


def test_een_enkel_profiel_is_geen_keuze():
    assert detecteer_env_profielen({"components/app/.env.debug": "A=1\n"}) == []


# ── trede 4: de manifesten ───────────────────────────────────────────────────

def test_yarn_vier_levert_een_node_image_geen_python():
    vondst = _van(detecteer_manifesten(HIVE), "lab_image", "node")
    assert vondst is not None
    assert vondst.waarde["image"].startswith("node:22")
    assert "corepack" in vondst.waarde["installeer"]


def test_bouw_en_startcommandos_worden_opgehaald():
    vondst = _van(detecteer_manifesten(HIVE), "startscript", "scripts")
    assert sorted(vondst.waarde["bouwen"]) == ["build:apps", "build:dependencies"]
    assert sorted(vondst.waarde["starten"]) == ["start:agent", "start:api", "start:app"]
    assert vondst.waarde["werkruimtes"] is True


# ── trede 1: compose ─────────────────────────────────────────────────────────

COMPOSE = {
    "docker-compose.yml": (
        "services:\n"
        "  api:\n"
        "    build: ./components/api\n"
        "    ports:\n"
        "      - '3000:3000'\n"
        "  redis:\n"
        "    image: redis:7-alpine\n"
        "    ports:\n"
        "      - '16379:6379'\n"
        "    environment:\n"
        "      - REDIS_ARGS=--save 60 1\n"
    ),
}


def test_compose_scheidt_de_app_van_haar_afhankelijkheden():
    vondsten = detecteer_compose(COMPOSE)
    assert _van(vondsten, "applicatie", "api") is not None, "build: = de applicatie zelf"
    dienst = _van(vondsten, "dienst", "redis")
    assert dienst is not None and dienst.zekerheid == "hoog"
    assert _van(vondsten, "dienst", "api") is None, "de app hoort niet in de vaste laag"


def test_van_een_poortkoppeling_telt_de_containerkant():
    """De linkerkant overnemen is hoe de emulator op een bezette poort botste."""
    dienst = _van(detecteer_compose(COMPOSE), "dienst", "redis")
    assert dienst.waarde["poorten"] == [6379]
    assert dienst.waarde["omgeving"] == {"REDIS_ARGS": "--save 60 1"}


def test_kapotte_compose_sloopt_de_verkenning_niet():
    beeld = {"docker-compose.yml": "services:\n  - dit: [is geen\n", **HIVE}
    vondsten = detecteer_alles(beeld)
    assert _van(vondsten, "dienst", "cosmos") is not None


# ── staven: de controle op wat het model voorstelt ───────────────────────────

def test_modelvondst_met_bewijs_wordt_bevestigd():
    vondst = Vondst(soort="dienst", sleutel="cosmos", waarde={"image": "x/azure-cosmos-emulator"},
                    bron="model", bewijs=[])
    staaf(vondst, HIVE)
    assert vondst.bevestigd is True
    assert vondst.bewijs and vondst.bewijs[0].bestand == "components/api/.env.example"


def test_modelvondst_zonder_bewijs_blijft_staan_maar_onbevestigd():
    """Niet weggooien: tonen met lage zekerheid, uitgevinkt in het scherm."""
    vondst = Vondst(soort="dienst", sleutel="rabbitmq", waarde={"image": "rabbitmq:3"},
                    bron="model", zekerheid="hoog",
                    bewijs=[Bewijs(bestand="docs/BESTAAT-NIET.md", regel=12)])
    staaf(vondst, HIVE)
    assert vondst.bevestigd is False
    assert vondst.zekerheid == "laag"
    assert vondst.bewijs, "de verzonnen vindplaats blijft zichtbaar, juist als vindplaats"


def test_staven_vervangt_een_verzonnen_regelnummer():
    vondst = Vondst(soort="geheim", sleutel="API_KEY", bron="model",
                    bewijs=[Bewijs(bestand="components/api/.env.example", regel=999)])
    staaf(vondst, HIVE)
    assert vondst.bevestigd is True
    assert all(b.regel != 999 for b in vondst.bewijs)


# ── envlezer ─────────────────────────────────────────────────────────────────

def test_envlezer_verdraagt_commentaar_export_en_aanhalingstekens():
    regels = lees_env('# uitleg\nexport A="een"\nB=twee\n\nniet-een-sleutel\nC=\n')
    assert regels == [(2, "A", "een"), (3, "B", "twee"), (6, "C", "")]


# ── compose over meerdere bestanden ──────────────────────────────────────────

TWEE_COMPOSES = {
    "docker-compose.yml": (
        "services:\n"
        "  api:\n"
        "    build: ./backend\n"
        "  ollama:\n"
        "    image: ollama/ollama\n"
    ),
    "deploy/docker-compose.server.yml": (
        "services:\n"
        "  api:\n"
        "    image: ghcr.io/org/api:latest\n"
        "  tls:\n"
        "    image: caddy:2\n"
    ),
}


def test_dezelfde_dienst_in_twee_composes_wordt_een_vondst():
    """`build:` wint, waar het ook staat.

    Een repo heeft vaak twee composes: een om te ontwikkelen, een om uit te
    rollen. Los geteld is `api` dan tegelijk de applicatie en een vaste
    dienst — en draait elke branch tegen de code van wie het cluster aanmaakte.
    """
    vondsten = detecteer_compose(TWEE_COMPOSES)
    api = [v for v in vondsten if v.sleutel == "api"]
    assert len(api) == 1 and api[0].soort == "applicatie"
    assert {b.bestand for b in api[0].bewijs} == set(TWEE_COMPOSES), "beide vindplaatsen blijven"
    assert "uitgerolde versie" in api[0].toelichting


def test_diensten_uit_beide_composes_blijven_bestaan():
    namen = {v.sleutel for v in detecteer_compose(TWEE_COMPOSES) if v.soort == "dienst"}
    assert namen == {"ollama", "tls"}


def test_dienst_zonder_image_en_zonder_build_valt_af():
    beeld = {"compose.yml": "services:\n  leeg:\n    restart: always\n"}
    assert detecteer_compose(beeld) == []


# ── aanvullen: wat de detector afleest en wat het model afleidt ──────────────

def test_het_model_vult_aan_waar_de_detector_niets_weet():
    """Een compose zegt DAT `ollama` draait; niet onder welke naam de
    applicatie hem zoekt. Dat leest het model uit de code. Zonder aanvullen
    krijgt een branch-lab een lege omgeving en zoekt hij een dienst die er
    wel is -- op een adres dat hij niet kent."""
    from services.lab.verkenner import _vul_aan

    van_detector = Vondst(soort="dienst", sleutel="ollama",
                          waarde={"image": "ollama/ollama", "poorten": [11434],
                                  "lab_omgeving": {}},
                          bron="detector:compose", zekerheid="hoog")
    van_model = Vondst(soort="dienst", sleutel="ollama",
                       waarde={"image": "ollama/ollama:latest", "poorten": [1],
                               "lab_omgeving": {"OLLAMA_HOST": "ollama:11434"}},
                       bron="model", zekerheid="laag")
    _vul_aan(van_detector, van_model)

    assert van_detector.waarde["lab_omgeving"] == {"OLLAMA_HOST": "ollama:11434"}
    assert van_detector.waarde["poorten"] == [11434], "wat de detector AFLAS blijft staan"
    assert van_detector.waarde["image"] == "ollama/ollama"


# ── waarop luistert de applicatie ────────────────────────────────────────────

def test_expose_is_het_bewijs_voor_de_poort():
    """LabX publiceerde standaard 3000 en 7071. Luistert de applicatie op
    8000, dan gebeurt er niets: geen foutmelding, geen aanwijzing, een lab dat
    draait en onbereikbaar is."""
    from services.lab.verkenner_detectoren import detecteer_poorten

    beeld = {"Dockerfile": "FROM python:3.11-slim\nWORKDIR /app\nEXPOSE 8000\nCMD [\"./start.sh\"]\n"}
    vondst = detecteer_poorten(beeld)[0]
    assert vondst.waarde["poorten"] == [8000]
    assert vondst.zekerheid == "hoog"
    assert vondst.bewijs[0].regel == 3, "met regelnummer, want anders is het een bewering"


def test_de_wortel_wint_van_een_onderliggende_map():
    """`frontend/Dockerfile` met EXPOSE 80 is de losse webserver van de SPA.
    In een branch-lab serveert de applicatie die meestal zelf."""
    from services.lab.verkenner_detectoren import detecteer_poorten

    beeld = {
        "frontend/Dockerfile": "FROM nginx\nEXPOSE 80\n",
        "Dockerfile": "FROM python:3.11\nEXPOSE 8000\n",
    }
    assert detecteer_poorten(beeld).pop().waarde["poorten"][0] == 8000


def test_een_poort_met_protocol_wordt_gelezen():
    from services.lab.verkenner_detectoren import detecteer_poorten

    beeld = {"Dockerfile": "FROM x\nEXPOSE 8080/tcp 9000\n"}
    assert detecteer_poorten(beeld).pop().waarde["poorten"] == [8080, 9000]


def test_zonder_expose_wordt_er_niets_beweerd():
    from services.lab.verkenner_detectoren import detecteer_poorten

    assert detecteer_poorten({"Dockerfile": "FROM python:3.11\nCMD [\"x\"]\n"}) == []


# ── de Azure CLI: alleen waar hij echt nodig is ─────────────────────────────

def test_een_repo_die_az_aanroept_vraagt_om_de_azure_cli():
    """Een branch-lab krijgt de Azure CLI standaard NIET: in een
    node-monorepo kost dat 616 MB voor gereedschap dat nooit wordt
    aangeroepen. Maar soms wordt hij wel aangeroepen — IntelligentHive draait
    `.devcontainer/ensure-access.sh` en dat strandde op `az: command not
    found`, in een lus die zichzelf eindeloos opnieuw probeerde. Het lab leek
    dus te leven terwijl er niets gebeurde.
    """
    from services.lab.verkenner_detectoren import detecteer_azure_cli

    beeld = {".devcontainer/ensure-access.sh":
             "#!/bin/bash\nset -e\nif ! az account show; then\n  az login\nfi\n"}
    vondst = detecteer_azure_cli(beeld)[0]
    assert vondst.waarde["nodig"] is True
    assert vondst.zekerheid == "hoog"
    assert vondst.bewijs[0].bestand == ".devcontainer/ensure-access.sh"
    assert vondst.bewijs[0].regel == 3, "met vindplaats, zoals elke vondst"


def test_een_repo_zonder_azure_vraagt_er_niet_om():
    from services.lab.verkenner_detectoren import detecteer_azure_cli

    assert detecteer_azure_cli(HIVE) == []


def test_het_woord_az_in_proza_telt_niet():
    """Anders krijgt elke repo met "az" in een README de Azure CLI erbij."""
    from services.lab.verkenner_detectoren import detecteer_azure_cli

    assert detecteer_azure_cli({"README.md": "Dit project heet az en doet dingen.\n"}) == []
