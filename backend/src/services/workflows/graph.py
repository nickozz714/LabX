"""
services/workflows/graph.py

De vorm van een workflow: activiteiten (nodes) en verbindingen (edges), plus
het inlezen van een oude workflow die alleen stappen had.

**Waarom een graaf.** Zolang alle stappen in één prompt naar het model gingen,
bestond er tijdens het draaien niets om op te vertakken, te herhalen of te
loggen. Met activiteiten die LabX zelf uitvoert, komen `als`, herhalingen en
een runverslag alle drie uit dezelfde verandering voort.

**Soorten activiteiten.**
- `agent`  — een opdracht aan de agent in het lab. Optioneel met een rol (een
             systeeminstructie voor deze ene stap) en een JSON-schema, zodat de
             uitvoer gestructureerd terugkomt en een volgende `als` erop kan
             beslissen.
- `shell`  — een commando in de container. Deterministisch en goedkoop; voor
             alles waar geen model voor nodig is.
- `als`    — splitst op een voorwaarde. Verbindingen `ja` en `nee`.
- `wacht`  — een pauze, bijvoorbeeld tussen twee pogingen.
- `voorelk` — een LUS: alles wat erin zit draait één keer per element van een
             lijst, netjes achter elkaar. Binnen de lus zijn `item` en
             `iteratie` beschikbaar, óók in een `als` die erin ligt — dat is
             het verschil met `herhaal_over` op één activiteit, dat maar één
             stap kan herhalen.
- `parallel` — een BUBBEL waar activiteiten in zitten die gelijktijdig draaien.
             De activiteiten erin hebben `groep` op de id van de bubbel, staan
             niet in de gewone wandeling door de graaf (de bubbel voert ze uit)
             en krijgen elk een EIGEN sessie: één CLI-sessie kan geen twee
             beurten tegelijk hebben. Zitten er meer werkers in het lab, dan
             worden ze over die containers verdeeld; anders draaien ze naast
             elkaar in dezelfde — twee keer Claude op één pc, met dezelfde
             afweging als bij een bundel in een planning.

**Herhalen zit op de activiteit, niet op een aparte lus-activiteit.** Een node
met `herhaal_over` draait één keer per element van die lijst (`item` is dan
beschikbaar in de tekst en in voorwaarden); met `herhaal_tot` draait hij
opnieuw tot de voorwaarde klopt, met een harde bovengrens. Dat dekt het
gewone geval — "doe dit voor elke tabel" — zonder de scoping-ellende van een
lus die een heel blok omsluit. Een lus over meerdere activiteiten hoort thuis
in een sub-workflow, en die komt later.

**Verbindingen** hebben een soort: `succes`, `fout`, `altijd` (na een gewone
activiteit) of `ja` / `nee` (na een `als`). Zonder uitgaande verbinding is de
workflow daar klaar.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

NODE_SOORTEN = ("agent", "shell", "als", "wacht", "parallel", "voorelk")
EDGE_SOORTEN = ("succes", "fout", "altijd", "ja", "nee")

# Een run stopt hier hoe dan ook. Vangnet tegen een graaf die in een kringetje
# loopt: zonder dit kan één verkeerd getekende verbinding een lab een nacht
# lang bezig houden.
MAX_ACTIVITEITEN_PER_RUN = 200
STANDAARD_HERHAAL_MAX = 25


def _slug(tekst: str) -> str:
    """De naam waarmee je in een expressie naar een activiteit verwijst."""
    kaal = re.sub(r"[^a-z0-9]+", "_", (tekst or "").lower()).strip("_")
    return kaal or "stap"


def normaliseer_node(ruw: Dict[str, Any], index: int) -> Dict[str, Any]:
    soort = str(ruw.get("type") or ruw.get("soort") or "agent").lower()
    if soort not in NODE_SOORTEN:
        soort = "agent"
    naam = str(ruw.get("naam") or ruw.get("title") or f"Stap {index}").strip()
    node: Dict[str, Any] = {
        "id": str(ruw.get("id") or f"n{index}"),
        "type": soort,
        "naam": naam,
        "sleutel": str(ruw.get("sleutel") or _slug(naam)),
        "positie": ruw.get("positie") or {"x": 0, "y": (index - 1) * 140},
    }
    if soort == "agent":
        node["prompt"] = str(ruw.get("prompt") or ruw.get("instruction") or "")
        node["rol"] = (ruw.get("rol") or None)
        node["verse_sessie"] = bool(ruw.get("verse_sessie"))
        node["json_schema"] = (ruw.get("json_schema") or None)
        node["model"] = (ruw.get("model") or None)
    elif soort == "shell":
        node["commando"] = str(ruw.get("commando") or "")
        node["timeout"] = int(ruw.get("timeout") or 120)
    elif soort == "als":
        node["conditie"] = ruw.get("conditie") or {}
    elif soort == "wacht":
        node["seconden"] = max(1, min(int(ruw.get("seconden") or 30), 3600))
    elif soort == "voorelk":
        # Waar de lijst vandaan komt: een verwijzing naar eerdere uitvoer,
        # bijvoorbeeld stap.analyse.json.incidentGroups.
        node["bron"] = str(ruw.get("bron") or "")
        # Een bovengrens, want een lijst die onverwacht duizend lang is, is
        # duizend agent-beurten.
        node["max_items"] = max(1, min(int(ruw.get("max_items") or 50), 200))
        node["fout_gedrag"] = ("doorgaan" if str(ruw.get("fout_gedrag") or "stop") == "doorgaan"
                               else "stop")
    elif soort == "parallel":
        # Hoeveel er tegelijk mogen. Niet ongelimiteerd: acht agents in één
        # lab is acht keer hetzelfde geheugen en dezelfde processen.
        node["max_gelijktijdig"] = max(1, min(int(ruw.get("max_gelijktijdig") or 4), 8))
        # Wat er gebeurt als er één omvalt: "stop" laat de bubbel falen zodra
        # de rest klaar is, "doorgaan" telt het als een geslaagde bubbel met
        # een mislukte tak erin.
        node["fout_gedrag"] = ("doorgaan" if str(ruw.get("fout_gedrag") or "stop") == "doorgaan"
                               else "stop")
    # Herhalen kan op elke uitvoerende activiteit.
    if ruw.get("herhaal_over"):
        node["herhaal_over"] = str(ruw["herhaal_over"])
    if ruw.get("herhaal_tot"):
        node["herhaal_tot"] = ruw["herhaal_tot"]
    node["herhaal_max"] = max(1, min(int(ruw.get("herhaal_max") or STANDAARD_HERHAAL_MAX), 100))
    # Een activiteit die mag mislukken zonder de run te stoppen (dan telt de
    # `fout`-verbinding, of loopt hij gewoon door als die er niet is).
    node["mag_falen"] = bool(ruw.get("mag_falen"))
    # In welke bubbel deze activiteit zit (de id van een `parallel`-activiteit).
    # Zit hij in een bubbel, dan wandelt de motor er niet zelf naartoe: de
    # bubbel start hem.
    if ruw.get("groep"):
        node["groep"] = str(ruw["groep"])
    return node


def normaliseer_edge(ruw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    van, naar = str(ruw.get("van") or ""), str(ruw.get("naar") or "")
    if not van or not naar:
        return None
    soort = str(ruw.get("soort") or "succes").lower()
    return {"van": van, "naar": naar, "soort": soort if soort in EDGE_SOORTEN else "succes"}


def normaliseer(nodes: List[Dict[str, Any]],
                edges: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    nn = [normaliseer_node(n, i) for i, n in enumerate(nodes or [], start=1)]
    ids = {n["id"] for n in nn}
    ee = [e for e in (normaliseer_edge(x) for x in (edges or [])) if e]
    # Een verbinding naar een activiteit die niet meer bestaat is geen fout om
    # over te vallen, maar hem laten staan zou de uitvoering laten stranden op
    # iets dat niemand meer ziet.
    ee = [e for e in ee if e["van"] in ids and e["naar"] in ids]
    return nn, ee


def uit_stappen(steps: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Een oude workflow (alleen stappen) als rechte keten van agent-activiteiten.

    Zo blijft elke bestaande workflow gewoon werken en hoeft niemand iets
    opnieuw te tekenen — hij is daarna alleen wél uit te breiden."""
    nodes: List[Dict[str, Any]] = []
    for i, s in enumerate(steps or [], start=1):
        nodes.append(normaliseer_node({
            "id": f"n{i}", "type": "agent",
            "naam": s.get("title") or f"Stap {i}",
            "prompt": s.get("instruction") or "",
        }, i))
    edges = [{"van": nodes[i]["id"], "naar": nodes[i + 1]["id"], "soort": "succes"}
             for i in range(len(nodes) - 1)]
    return nodes, edges


def zorg_voor_graaf(workflow) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """De graaf van deze workflow, desnoods afgeleid uit zijn stappen."""
    nodes = list(getattr(workflow, "nodes_json", None) or [])
    edges = list(getattr(workflow, "edges_json", None) or [])
    if nodes:
        return normaliseer(nodes, edges)
    return uit_stappen(list(getattr(workflow, "steps_json", None) or []))


def kinderen(nodes: List[Dict[str, Any]], groep_id: str) -> List[Dict[str, Any]]:
    """De activiteiten in deze bubbel, in de volgorde waarin ze staan."""
    return [n for n in nodes if n.get("groep") == groep_id]


def losse(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Alles wat NIET in een bubbel zit — de gewone wandeling door de graaf.

    Een activiteit in een bubbel wordt door die bubbel gestart; hem ook nog via
    een verbinding laten lopen zou hem twee keer uitvoeren."""
    return [n for n in nodes if not n.get("groep")]


def startnode(nodes: List[Dict[str, Any]],
              edges: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Waar de run begint: de activiteit waar niets naartoe wijst.

    Zijn het er meerdere (losse takken), dan wint de eerste in de lijst — de
    volgorde waarin ze getekend zijn. Wijst alles naar iets (een kringetje),
    dan nemen we ook gewoon de eerste; de bovengrens op het aantal
    activiteiten vangt de gevolgen op."""
    vrijstaand = losse(nodes)
    if not vrijstaand:
        return None
    doelen = {e["naar"] for e in edges}
    vrij = [n for n in vrijstaand if n["id"] not in doelen]
    return vrij[0] if vrij else vrijstaand[0]


def volgende(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]],
             node_id: str, tak: str) -> List[Dict[str, Any]]:
    """De activiteiten die na deze aan de beurt zijn, gegeven de uitkomst.

    `altijd` telt bij elke uitkomst mee: dat is de verbinding voor "ruim op",
    "meld het" — het soort stap dat juist moet lopen als er iets misging."""
    # Alleen losse activiteiten: een activiteit in een bubbel hoort door zijn
    # bubbel gestart te worden, niet door een verbinding.
    op_id = {n["id"]: n for n in losse(nodes)}
    uit = []
    for e in edges:
        if e["van"] != node_id:
            continue
        if e["soort"] == tak or e["soort"] == "altijd":
            doel = op_id.get(e["naar"])
            if doel is not None:
                uit.append(doel)
    return uit


def volgende_in_groep(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]],
                      node_id: str, tak: str, groep_id: str) -> List[Dict[str, Any]]:
    """Zoals `volgende`, maar dan binnen een lus of bubbel.

    Een verbinding die de groep uit wijst telt hier niet: wat er ná de lus
    gebeurt, gebeurt één keer — niet bij elke ronde."""
    op_id = {n["id"]: n for n in kinderen(nodes, groep_id)}
    uit = []
    for e in edges:
        if e["van"] != node_id:
            continue
        if e["soort"] == tak or e["soort"] == "altijd":
            doel = op_id.get(e["naar"])
            if doel is not None:
                uit.append(doel)
    return uit


def startnode_in_groep(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]],
                       groep_id: str) -> Optional[Dict[str, Any]]:
    """Waar een ronde begint: de activiteit in de groep waar binnen die groep
    niets naartoe wijst."""
    leden = kinderen(nodes, groep_id)
    if not leden:
        return None
    ids = {n["id"] for n in leden}
    doelen = {e["naar"] for e in edges if e["van"] in ids and e["naar"] in ids}
    vrij = [n for n in leden if n["id"] not in doelen]
    return vrij[0] if vrij else leden[0]


def valideer(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> List[str]:
    """Wat er mis is aan deze graaf, in gewone taal.

    Bewust waarschuwingen en geen harde fouten: een workflow mag half af
    opgeslagen worden — je bent hem aan het bouwen."""
    meldingen: List[str] = []
    if not nodes:
        return ["Deze workflow heeft nog geen activiteiten."]
    sleutels: Dict[str, int] = {}
    for n in nodes:
        sleutels[n["sleutel"]] = sleutels.get(n["sleutel"], 0) + 1
        if n["type"] == "agent" and not (n.get("prompt") or "").strip():
            meldingen.append(f"'{n['naam']}' heeft geen opdracht.")
        if n["type"] == "shell" and not (n.get("commando") or "").strip():
            meldingen.append(f"'{n['naam']}' heeft geen commando.")
        if n["type"] == "als":
            cond = n.get("conditie") or {}
            if not cond.get("links"):
                meldingen.append(f"'{n['naam']}' heeft geen voorwaarde.")
            elif not any(e["van"] == n["id"] and e["soort"] in ("ja", "nee") for e in edges):
                meldingen.append(f"'{n['naam']}' heeft geen ja/nee-verbinding — "
                                 f"de workflow stopt daar.")
    for sleutel, aantal in sleutels.items():
        if aantal > 1:
            meldingen.append(f"Meerdere activiteiten heten '{sleutel}'; in een expressie "
                             f"verwijs je dan naar de laatste die gedraaid heeft.")
    ids = {n["id"] for n in nodes}
    for n in nodes:
        if n["type"] in ("parallel", "voorelk") and not kinderen(nodes, n["id"]):
            woord = "bubbel" if n["type"] == "parallel" else "lus"
            meldingen.append(f"De {woord} '{n['naam']}' is leeg — sleep er activiteiten in.")
        if n["type"] == "voorelk" and not (n.get("bron") or "").strip():
            meldingen.append(f"'{n['naam']}' heeft geen lijst om langs te lopen.")
        if n.get("groep") and n["groep"] not in ids:
            meldingen.append(f"'{n['naam']}' hoort bij een groep die niet meer bestaat.")
        if n.get("groep") and n["type"] in ("parallel", "voorelk"):
            meldingen.append(f"'{n['naam']}' kan niet in een andere groep: een lus of bubbel "
                             f"hoort in de hoofdstroom.")
        ouder = next((x for x in nodes if x["id"] == n.get("groep")), None)
        if ouder is not None and ouder["type"] == "parallel" and n["type"] == "als":
            meldingen.append(f"'{n['naam']}' kan niet in een bubbel: een tak die tegelijk "
                             f"draait heeft geen volgende stap om naartoe te vertakken.")
    doelen = {e["naar"] for e in edges}
    vrijstaand = losse(nodes)
    los = [n["naam"] for n in vrijstaand[1:] if n["id"] not in doelen]
    if los:
        meldingen.append("Niet verbonden (draait nooit): " + ", ".join(los))
    return meldingen
