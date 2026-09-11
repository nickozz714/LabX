"""De data-guard mag beheer-API's niet aanzien voor klantdata.

Op 11-09-2026 blokkeerde de guard 62 Fabric-commando's op één dag, allemaal met
"leest ruwe bestandsinhoud van een datafile". Dat lag aan één teken in een
regex: `[^|]*` matcht ook NEWLINES, dus in

    TOKEN=$(cat /tmp/fabric_token.txt)
    curl ... -o /workspace/nb_def.json

koppelde hij de `cat` van het token aan de `.json` tien regels verderop. Elk
script dat een token uitleest en ergens een .json noemt, gold daarmee als het
lezen van een datafile.

Daaronder zat een tweede vraag: is een pipeline- of notebookdefinitie
klantdata? Nee — dat is code en configuratie. De guard bestaat om klantgegevens
binnen te houden, niet om de agent te beletten naar zijn eigen werk te kijken.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.lab.data_guard import classify_command as provenance  # noqa: E402
from services.lab.governed_policy import classify_command  # noqa: E402

FABRIC_DEF = (
    'TOKEN=$(cat /tmp/fabric_token.txt)\n'
    'curl -s -X POST "https://api.fabric.microsoft.com/v1/workspaces/$WS/items/$ID/getDefinition" '
    '-H "Authorization: Bearer $TOKEN" -o /workspace/def.json -w "%{http_code}\\n"\n'
    'jq -r ".definition.parts[].path" /workspace/def.json')


def test_de_regex_loopt_niet_meer_over_regelgrenzen():
    """De kern van de 62 valse treffers: een `cat` op regel 1 mag niet
    gekoppeld worden aan een `.json` op regel 3."""
    klasse, reden = classify_command(FABRIC_DEF)
    assert klasse != "value_revealing", reden


def test_fabric_definitie_lezen_mag():
    klasse, reden = classify_command(FABRIC_DEF)
    assert klasse == "counting_safe"
    assert "beheer-API" in reden
    assert provenance(FABRIC_DEF) == "control"


@pytest.mark.parametrize("cmd", [
    'curl "https://api.fabric.microsoft.com/v1/workspaces/$WS/items"',
    'curl "https://api.powerbi.com/v1.0/myorg/groups"',
    'az group list --output json',
    'az account show',
    'curl "https://management.azure.com/subscriptions/x/resourceGroups?api-version=2021-04-01"',
])
def test_beheer_apis_zijn_control_plane(cmd):
    assert classify_command(cmd)[0] == "counting_safe"


# ── en wat er DICHT moet blijven ────────────────────────────────────────────

@pytest.mark.parametrize("cmd,waarom", [
    ("python3 -c \"spark.sql('SELECT naam, plaats FROM klanten').show()\"", "SELECT"),
    ('duckdb -c "SELECT MAX(bedrag) FROM t"', "aggregatie"),
    ('python3 -c "df.head(20)"', "sample-rijen"),
    ("python3 -c \"print(df.groupby('stad').size())\"", "GROUP BY"),
])
def test_waarde_onthullende_opdrachten_blokkeren_zelf(cmd, waarom):
    """Hier is het commando het enige betrouwbare signaal: `SELECT MAX(bedrag)`
    levert eén getal op dat in geen enkele uitvoertelling opvalt en tóch een
    echte magnitude is."""
    assert classify_command(cmd)[0] == "value_revealing", waarom


@pytest.mark.parametrize("cmd", [
    "head -50 /workspace/klanten.csv",
    "cat /workspace/export.parquet",
])
def test_bestandslezing_is_een_vermoeden_geen_vaststelling(cmd):
    """"Iemand leest een bestand met een data-achtige extensie" is een gok op de
    opdracht. Die blokkeert niet zelf — de uitvoer beslist, en wordt streng
    beoordeeld. Zo gaan er geen commando's meer dicht die 13 bytes teruggaven."""
    assert classify_command(cmd)[0] == "vermoedelijk"


def test_vermoeden_blokkeert_niet_bij_onschuldige_uitvoer():
    from services.lab.data_guard import guard_lab_output

    uit = guard_lab_output({"exit_code": 0, "output": "200\n", "truncated": False},
                           enabled=True, command="cat /workspace/def.json")
    assert not uit.get("guarded"), "een HTTP-status is geen klantdata"


def test_vermoeden_blokkeert_wel_bij_echte_records():
    from services.lab.data_guard import guard_lab_output

    rijen = "\n".join(f"{i},Jansen,Amsterdam,{1000+i},2026-01-{i%28+1:02d}" for i in range(8))
    uit = guard_lab_output({"exit_code": 0, "output": rijen, "truncated": False},
                           enabled=True, command="cat /workspace/klanten.json")
    assert uit.get("guarded"), "records horen dicht te blijven"


# ── code schrijven is geen data lezen ───────────────────────────────────

def test_code_wegschrijven_telt_niet_mee():
    """`cat > cel4.py << EOF … df.groupBy(…) … EOF` produceert geen enkele rij;
    het legt een script neer. De inhoud meewegen blokkeerde het SCHRIJVEN van
    pyspark-code — precies het werk waar een lab voor is."""
    cmd = ("cat > /tmp/cell4.py << 'CELL4EOF'\n"
           "from pyspark.sql.functions import col\n"
           "df = spark.sql('SELECT naam, plaats FROM klanten')\n"
           "CELL4EOF")
    assert classify_command(cmd)[0] != "value_revealing"


def test_heredoc_naar_een_interpreter_telt_wel_mee():
    """De grens: dit DRAAIT wel. `python3 << EOF` is geen bestand neerleggen."""
    cmd = ("python3 << 'PYEOF'\n"
           "spark.sql('SELECT naam FROM klanten').show()\n"
           "PYEOF")
    assert classify_command(cmd)[0] == "value_revealing"


def test_control_plane_wint_niet_van_echte_data():
    """Een script dat een Fabric-URL noemt én uit OneLake leest, is gewoon
    data-plane. De beheer-API is geen vrijbrief voor de rest van het script."""
    cmd = ('curl "https://api.fabric.microsoft.com/v1/workspaces/$WS/items" -o /tmp/i.json\n'
           'curl "https://onelake.dfs.fabric.microsoft.com/$WS/$LH/Tables/klanten/deel.parquet"')
    assert provenance(cmd) == "data"
    assert classify_command(cmd)[0] != "counting_safe"


def test_een_select_in_hetzelfde_script_wint_van_de_beheer_api():
    cmd = ('curl "https://api.fabric.microsoft.com/v1/workspaces/$WS/items"\n'
           'python3 -c "spark.sql(\'SELECT naam FROM klanten\').show()"')
    assert classify_command(cmd)[0] == "value_revealing"


def test_telling_opent_de_deur_niet_voor_data_plane():
    """`counting_safe` mag de herkomst op 'control' zetten en daarmee de
    record-telling overslaan — maar niet als hetzelfde script uit OneLake
    leest. Anders opent elke COUNT de deur voor de rijen eromheen."""
    from services.lab.data_guard import guard_lab_output

    cmd = ('python3 -c "import duckdb; duckdb.sql(\'select count(*) from t\')"\n'
           'curl https://onelake.dfs.fabric.microsoft.com/ws/lh/Tables/klanten')
    rijen = "\n".join(f"{i},Jansen,Amsterdam,{1000+i},2026-01-{i%28+1:02d}" for i in range(20))
    uit = guard_lab_output({"exit_code": 0, "output": rijen, "truncated": False},
                           enabled=True, command=cmd)
    assert uit.get("guarded"), "data-plane met recordinhoud moet dicht blijven"
    assert (uit.get("guard_facts") or {}).get("provenance") == "data"
