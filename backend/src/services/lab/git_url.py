"""
services/lab/git_url.py

Repo-URL's omzetten naar de vorm waarmee LabX kan klonen.

LabX kloont over HTTPS met een token in een header. Een repo die je lokaal
hebt staan is vaak over SSH gekloond — IntelligentHive op de MacBook heeft
`git@github.com:beeminds-data/Intelligenthive.git` als remote — en dat is
precies de URL die iemand kopieert en plakt. Die zonder meer doorgeven levert
een kale sleutelfout op in een container die geen sleutels heeft.

Omzetten kan, en is eenduidig: dezelfde host, hetzelfde pad, ander protocol.
Het alternatief (een deploy-key in het lab) is een heel ander verhaal met
sleutelbeheer eraan vast; dat is geen stap die je per ongeluk wilt zetten.

**Wel zeggen dat je het deed.** Een URL stil veranderen is hoe iemand een uur
kwijt is aan de vraag waarom de verkeerde repo gekloond werd.
"""
from __future__ import annotations

import re
from typing import NamedTuple


class RepoUrl(NamedTuple):
    url: str
    omgezet: bool
    #: Leeg als er niets te melden valt.
    melding: str = ""


#: git@host:org/repo.git
_SCP = re.compile(r"^(?:ssh://)?(?:[\w.-]+@)?([\w.-]+):(?!\d)(.+?)(?:\.git)?/?$")
#: ssh://git@host[:poort]/org/repo.git
_SSH = re.compile(r"^ssh://(?:[\w.-]+@)?([\w.-]+)(?::\d+)?/(.+?)(?:\.git)?/?$")


def naar_ssh(ruw: str) -> str:
    """De SSH-vorm van deze URL, voor een deploy key.

    De omgekeerde weg van `normaliseer`. Nodig omdat LabX een repo-URL
    opslaat in HTTPS-vorm -- dat is wat je plakt en wat een token nodig heeft
    -- terwijl een deploy key alleen over SSH werkt.
    """
    url = (ruw or "").strip()
    if not url:
        return ""
    if url.startswith(("git@", "ssh://")):
        return url
    kaal = re.sub(r"^https?://", "", url).rstrip("/")
    kaal = re.sub(r"^[^/@]+@", "", kaal)  # een gebruikersnaam in de URL hoort hier niet
    host, _, pad = kaal.partition("/")
    if not pad:
        return url
    return f"git@{host}:{pad.removesuffix('.git')}.git"


def naar_https(ruw: str) -> RepoUrl:
    """Alias van `normaliseer`, zodat de bedoeling in de aanroep staat."""
    return normaliseer(ruw)


def normaliseer(ruw: str) -> RepoUrl:
    """Een repo-URL in kloonbare vorm, plus of er iets veranderd is."""
    url = (ruw or "").strip()
    if not url:
        return RepoUrl("", False)

    if url.startswith(("http://", "https://")):
        return RepoUrl(url.rstrip("/"), False)

    for patroon in (_SSH, _SCP):
        treffer = patroon.match(url)
        if treffer:
            host, pad = treffer.group(1), treffer.group(2).strip("/")
            nieuw = f"https://{host}/{pad}.git"
            return RepoUrl(nieuw, True, (
                f"De URL was een SSH-adres; LabX kloont over HTTPS met een token uit de "
                f"kluis. Omgezet naar {nieuw}."))

    return RepoUrl(url, False)
