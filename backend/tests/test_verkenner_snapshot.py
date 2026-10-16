"""Het repo-beeld: begrensd, en zonder geheimen.

De scherpste regel hier is de redactie. Een gekloonde repo hoort geen `.env`
met werkende sleutels te bevatten, maar "hoort" is geen garantie — op de
eerste echte repo waar de verkenner op losging (LabX zelf) stonden er vijf in.
Wat uit de verkenningscontainer komt gaat een modelvenster in, dus gaan de
waarden eraf bij de BRON. Gaat dat kapot, dan lekt er een geheim naar een
model, een log en een plan tegelijk — en merkt niemand het.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.lab.verkenner_snapshot import (  # noqa: E402
    EXTRA_SCRIPT,
    MAX_BOOM,
    SCRIPT,
    _REDACTIE,
)


def _redactie():
    ruimte: dict = {}
    exec(compile(_REDACTIE, "<redactie>", "exec"), ruimte)  # noqa: S102 — eigen tekst
    return ruimte["schoon_env"]


def _draai(script: str, *args: str) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(script)
        pad = f.name
    uit = subprocess.run([sys.executable, pad, *args], capture_output=True, text=True, timeout=60)
    assert uit.returncode == 0, uit.stderr
    return json.loads(uit.stdout)


# ── de redactie ──────────────────────────────────────────────────────────────

def test_geheime_waarden_gaan_eraf_namen_blijven():
    schoon = _redactie()
    uit = schoon(
        "LABX_ADMIN_USERNAME=admin\n"
        "LABX_ADMIN_PASSWORD=heelgeheim\n"
        "LABX_JWT_SECRET=abc123\n"
        "GITHUB_TOKEN=ghp_werkelijkwaar\n"
    )
    assert "heelgeheim" not in uit and "abc123" not in uit and "ghp_" not in uit
    assert "LABX_ADMIN_PASSWORD=" in uit, "de NAAM moet blijven; daar detecteren we op"
    assert "LABX_ADMIN_USERNAME=admin" in uit, "niet-geheime waarden blijven leesbaar"


def test_inloggegevens_uit_een_url_gaan_eraf_het_schema_blijft():
    """`postgres://` bepaalt welke database het is. Het wachtwoord niet."""
    uit = _redactie()("DATABASE_URL=postgres://gebruiker:wachtwoord@host:5432/db\n")
    assert "wachtwoord" not in uit and "gebruiker" not in uit
    assert "postgres://" in uit and "host:5432/db" in uit


def test_publieke_emulatorconstanten_blijven_staan():
    """Anders wordt de detectie blind voor juist de diensten die we zoeken."""
    schoon = _redactie()
    uit = schoon("AZURE_STORAGE_CONNECTION_STRING=UseDevelopmentStorage=true\n")
    assert "UseDevelopmentStorage" in uit


def test_commentaar_en_lege_regels_blijven_zoals_ze_zijn():
    tekst = "# uitleg\n\nA=1\n"
    assert _redactie()(tekst).splitlines() == ["# uitleg", "", "A=1"]


# ── het beeld ────────────────────────────────────────────────────────────────

def test_beeld_slaat_rommelmappen_over_en_leest_de_inrichting():
    with tempfile.TemporaryDirectory() as map_:
        wortel = Path(map_)
        (wortel / "package.json").write_text('{"name":"x"}')
        (wortel / ".env").write_text("API_KEY=geheim\n")
        for rommel in ("node_modules", ".venv-test", "dist"):
            (wortel / rommel).mkdir()
            (wortel / rommel / "troep.js").write_text("x")
        (wortel / "src").mkdir()
        (wortel / "src" / "index.ts").write_text("export {}")

        beeld = _draai(SCRIPT, str(wortel))

    assert "package.json" in beeld["inhoud"]
    assert "src/index.ts" in beeld["boom"], "gewone broncode blijft zichtbaar in de boom"
    assert not [p for p in beeld["boom"] if p.startswith(("node_modules", ".venv-test", "dist"))]
    assert "src/index.ts" not in beeld["inhoud"], "alleen inrichting wordt gelezen, niet de hele repo"
    assert beeld["inhoud"][".env"].strip() == "API_KEY=", "geheim eruit, naam erin"


def test_binaire_bestanden_komen_niet_in_de_inhoud():
    with tempfile.TemporaryDirectory() as map_:
        wortel = Path(map_)
        (wortel / "README.md").write_bytes(b"tekst\x00\x01binair")
        beeld = _draai(SCRIPT, str(wortel))
    assert "README.md" in beeld["boom"] and "README.md" not in beeld["inhoud"]


def test_nalezen_blijft_binnen_de_repo():
    """Een pad met `..` erin mag niet buiten de werkkopie wijzen."""
    with tempfile.TemporaryDirectory() as map_:
        wortel = Path(map_) / "repo"
        wortel.mkdir()
        (wortel / "goed.txt").write_text("hier")
        (Path(map_) / "buiten.txt").write_text("niet hier")
        uit = _draai(EXTRA_SCRIPT, str(wortel), json.dumps(["goed.txt", "../buiten.txt"]))
    assert uit == {"goed.txt": "hier"}


def test_nalezen_redigeert_net_zo_hard():
    """"Het model vroeg erom" is geen reden om een geheim wel door te geven."""
    with tempfile.TemporaryDirectory() as map_:
        wortel = Path(map_)
        (wortel / ".env.productie").write_text("STRIPE_SECRET_KEY=sk_live_echt\n")
        uit = _draai(EXTRA_SCRIPT, str(wortel), json.dumps([".env.productie"]))
    assert "sk_live_echt" not in uit[".env.productie"]


def test_de_boom_heeft_een_plafond_en_zegt_dat():
    with tempfile.TemporaryDirectory() as map_:
        wortel = Path(map_)
        for i in range(MAX_BOOM + 50):
            (wortel / f"b{i}.txt").write_text("x")
        beeld = _draai(SCRIPT, str(wortel))
    assert len(beeld["boom"]) == MAX_BOOM and beeld["boom_afgekapt"] is True
