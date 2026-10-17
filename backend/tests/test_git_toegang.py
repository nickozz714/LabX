"""Hoe een wegwerpcontainer bij een privérepo komt.

Twee methoden, één plek. Die ene plek is geen netheid maar noodzaak: de
HTTPS-variant stond eerder op drie plaatsen, en op alle drie stond dezelfde
fout — de auth-header werd per opdracht meegegeven, waarna de shell hem op
spaties splitste en git `basic` voor een subcommando aanzag. Drie keer
dezelfde fout vinden is één keer te veel.

Waarom er een SSH-variant bij moest: een organisatie met goedkeuringsbeleid
houdt een token tegen. Gemeten op deze machine — een `gh`-token met
`repo`-scope ziet 30 eigen repo's, geen enkele van de organisatie, en
`user/memberships/orgs` is leeg. Een deploy key is op REPO-niveau en valt
buiten dat beleid.
"""
import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.lab.git_auth import bouw, url_voor  # noqa: E402
from services.lab.git_url import naar_ssh  # noqa: E402

PRIVE = "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----\n"


# ── welke methode wint ───────────────────────────────────────────────────────

def test_een_sleutel_wint_van_een_token():
    """Specifieker (één repo) en niet afhankelijk van iemands goedkeuring."""
    assert bouw(pat="ghp_x", ssh_prive=PRIVE).soort == "ssh"
    assert bouw(pat="ghp_x").soort == "https"
    assert bouw().soort == "geen"


def test_zonder_toegang_komt_er_niets_in_de_omgeving():
    toegang = bouw()
    assert toegang.omgeving == {}
    assert toegang.script("git clone x") == "set -e; git clone x"


# ── de SSH-variant ───────────────────────────────────────────────────────────

def test_de_sleutel_reist_als_base64():
    """Een meerregelige PEM overleeft `docker run -e` niet ongeschonden."""
    toegang = bouw(ssh_prive=PRIVE)
    assert base64.b64decode(toegang.omgeving["LABX_SSH_KEY"]).decode() == PRIVE
    assert "\n" not in toegang.omgeving["LABX_SSH_KEY"]


def test_de_sleutel_krijgt_de_juiste_rechten_en_wordt_gepind():
    toegang = bouw(ssh_prive=PRIVE)
    assert "chmod 600" in toegang.aanloop
    assert "IdentitiesOnly=yes" in toegang.aanloop, "anders probeert ssh ook andere sleutels"
    # accept-new en niet `no`: dat laatste accepteert OOK een hostsleutel die
    # later verandert, en dat is juist het signaal dat je wilt zien.
    assert "StrictHostKeyChecking=accept-new" in toegang.aanloop
    assert "StrictHostKeyChecking=no" not in toegang.aanloop


def test_de_sleutel_landt_buiten_het_volume_van_het_lab():
    """De werkkopie wordt gedeeld en bewaard; deze container niet."""
    assert "/tmp/" in bouw(ssh_prive=PRIVE).aanloop
    assert "/workspace" not in bouw(ssh_prive=PRIVE).aanloop


# ── de HTTPS-variant ─────────────────────────────────────────────────────────

def test_de_header_gaat_in_de_config_en_niet_per_opdracht():
    """`git -c "http.extraHeader=AUTHORIZATION: basic $X" clone` ziet er goed
    uit en is het niet: de shell splitst de expansie alsnog op spaties, dus
    git kreeg "basic" als los woord."""
    aanloop = bouw(pat="ghp_x").aanloop
    assert "git config --global http.extraHeader" in aanloop
    assert base64.b64decode(bouw(pat="ghp_x").omgeving["GIT_AUTH"]).decode() == "x-access-token:ghp_x"


def test_het_token_komt_niet_letterlijk_in_de_omgeving():
    assert "ghp_x" not in str(bouw(pat="ghp_x").omgeving)


# ── het protocol volgt de methode ────────────────────────────────────────────

def test_een_deploy_key_dwingt_de_ssh_vorm_af():
    """Een deploy key werkt alleen over SSH. Staat er https in de URL, dan
    krijg je een foutmelding over sleutels terwijl de oorzaak het protocol
    is — en die melding wijst de verkeerde kant op."""
    url = url_voor("https://github.com/beeminds-data/Intelligenthive.git", bouw(ssh_prive=PRIVE))
    assert url == "git@github.com:beeminds-data/Intelligenthive.git"


def test_een_token_dwingt_de_https_vorm_af():
    url = url_voor("git@github.com:beeminds-data/Intelligenthive.git", bouw(pat="ghp_x"))
    assert url == "https://github.com/beeminds-data/Intelligenthive.git"


def test_omzetten_naar_ssh_verdraagt_wat_je_plakt():
    assert naar_ssh("https://github.com/org/repo") == "git@github.com:org/repo.git"
    assert naar_ssh("https://github.com/org/repo.git") == "git@github.com:org/repo.git"
    assert naar_ssh("git@github.com:org/repo.git") == "git@github.com:org/repo.git"
    assert naar_ssh("") == ""
