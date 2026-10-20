"""
services/lab/git_auth.py

Hoe een wegwerpcontainer bij een privérepo komt. Eén plek, twee methoden.

**Waarom één plek.** De HTTPS-variant stond eerder op drie plaatsen in deze
codebase, en op alle drie stond dezelfde fout: de auth-header werd per
opdracht meegegeven, waarna de shell hem op spaties splitste en git `basic`
voor een subcommando aanzag. Drie keer dezelfde fout vinden is één keer te
veel; een tweede methode erbij zou er zes maken.

**Twee methoden, en waarom allebei.**

- *Token over HTTPS.* Wat LabX altijd deed. Werkt zodra je een token hebt --
  maar bij een organisatie met goedkeuringsbeleid heb je dat niet zomaar.
- *Deploy key over SSH.* Een sleutelpaar per cluster. De publieke helft zet
  je één keer bij de repo onder Settings → Deploy keys. Die zijn op
  REPO-niveau en vallen buiten het OAuth- en PAT-goedkeuringsbeleid van een
  organisatie -- precies de muur waar het token op stuitte. Gemeten op deze
  machine: een `gh`-token met `repo`-scope ziet 30 eigen repo's maar geen
  enkele van de organisatie, en `user/memberships/orgs` is leeg.

Staat er een sleutel, dan wint die: hij is specifieker (één repo) en hij is
niet afhankelijk van iemands goedkeuring.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Dict, Optional

#: Waar de sleutel in de container landt. In /tmp en niet in /root/.ssh,
#: zodat hij buiten het volume van het lab blijft: de werkkopie wordt gedeeld
#: en bewaard, deze container niet.
SLEUTELPAD = "/tmp/labx_deploy_key"


@dataclass
class GitToegang:
    """Wat een container nodig heeft om te mogen klonen."""

    #: Omgevingsvariabelen voor de container.
    omgeving: Dict[str, str] = field(default_factory=dict)
    #: Shell-regels die vóór elk git-commando moeten draaien.
    aanloop: str = ""
    #: ssh | https | geen
    soort: str = "geen"

    def script(self, *git_regels: str) -> str:
        """De aanloop plus jouw git-opdrachten, als één shell-commando."""
        return "set -e; " + self.aanloop + " ".join(git_regels)


def bouw(*, pat: Optional[str] = None, ssh_prive: Optional[str] = None) -> GitToegang:
    """De toegang voor deze repo. Sleutel wint van token.

    De sleutel komt als base64 de container in en wordt daar pas uitgepakt:
    een meerregelige PEM in een omgevingsvariabele overleeft de reis door
    `docker run -e` niet ongeschonden.
    """
    if ssh_prive:
        omgeving = {"LABX_SSH_KEY": base64.b64encode(ssh_prive.encode()).decode()}
        aanloop = (
            f'printf %s "$LABX_SSH_KEY" | base64 -d > {SLEUTELPAD}; '
            f'chmod 600 {SLEUTELPAD}; '
            # accept-new en niet `no`: de eerste keer is er niets om tegen te
            # toetsen, en StrictHostKeyChecking=no accepteert OOK een sleutel
            # die later verandert -- en dat is juist het signaal dat je wilt
            # zien.
            f'export GIT_SSH_COMMAND="ssh -i {SLEUTELPAD} -o IdentitiesOnly=yes '
            f'-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/tmp/labx_known_hosts"; '
        )
        return GitToegang(omgeving=omgeving, aanloop=aanloop, soort="ssh")

    if pat:
        omgeving = {"GIT_AUTH": base64.b64encode(f"x-access-token:{pat}".encode()).decode()}
        # De header in de git-CONFIG zetten, niet per opdracht meegeven. Dat
        # laatste ziet er goed uit en is het niet: de shell splitst het
        # resultaat van de expansie alsnog op spaties, dus git kreeg "basic"
        # als los woord en antwoordde "'basic' is not a git command".
        aanloop = (
            'if [ -n "$GIT_AUTH" ]; then '
            '  git config --global http.extraHeader "AUTHORIZATION: basic $GIT_AUTH"; '
            'fi; '
        )
        return GitToegang(omgeving=omgeving, aanloop=aanloop, soort="https")

    return GitToegang()


def url_voor(repo_url: str, toegang: GitToegang) -> str:
    """De URL in de vorm die bij deze toegang hoort.

    Een deploy key werkt alleen over SSH, en een token alleen over HTTPS.
    Staat er het verkeerde protocol in de URL, dan krijg je een foutmelding
    over sleutels terwijl er een token klaarligt (of andersom) -- en die
    foutmelding wijst de verkeerde kant op.
    """
    from services.lab.git_url import naar_https, naar_ssh

    if toegang.soort == "ssh":
        return naar_ssh(repo_url)
    return naar_https(repo_url).url
