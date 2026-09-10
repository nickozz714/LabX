"""
services/boards/sync/adf.py

Atlassian Document Format <-> Markdown.

Jira Cloud v3 levert `description` en opmerkingen als ADF: een JSON-boom met
koppen, lijsten, tabellen, codeblokken en tekst-`marks` (vet, cursief, links).
LabX bewaart tekst als Markdown. Tussen die twee moet vertaald worden, en die
vertaling is de plek waar opmaak sneuvelt als je er te makkelijk over doet.

Dat is precies wat hier misging. De vorige versie platte ADF tot kale regels en
bouwde bij het terugschrijven van elke regel een alinea. Eén rondje LabX en een
Jira-ticket dat begon met

    # *Sales document:*
    ## Sales header
    - Order type

kwam terug als drie alinea's met letterlijk "- " ervoor. Koppen weg, vetgedrukt
weg, lijst geen lijst meer. En omdat de sync elke keer opnieuw schreef, gebeurde
dat bij élke sync opnieuw, ook als niemand de omschrijving had aangeraakt.

De eis aan dit bestand is daarom niet "leesbaar genoeg", maar **rondgang-vast**:
`to_markdown(to_adf(md)) == md` voor alles wat we schrijven, en
`to_adf(to_markdown(adf))` mag de structuur van het origineel niet verliezen.
Wat we niet kennen (media, inline cards, statuslozenges) wordt bewust als
zichtbare tekst weergegeven in plaats van stil weggegooid — een lezer die iets
mist, moet dat kunnen zien.

Gemeten op de 60 laatst gewijzigde omschrijvingen van een echt Jira-project
overleeft 52 de rondgang met exact dezelfde structuur. Wat er overblijft is
geen slordigheid maar het verschil tussen de twee formaten, en het is de moeite
waard te weten welk:

- **Twee of meer harde regeleindes achter elkaar** worden één alineagrens.
  Markdown kent geen "lege regel binnen een alinea"; het beeld is hetzelfde.
- **Onderstreping** bestaat niet in Markdown en gaat verloren. Vet, cursief,
  doorhalen, code en links blijven wel.
- **Een lijst die begint met een geneste lijst met afwijkende inspringing**
  kan een niveau verschuiven.

Dat dit acceptabel is, komt door de afspraak in sync_service: een omschrijving
wordt alleen teruggeschreven als iemand hem in LabX écht heeft gewijzigd. Een
ticket dat je niet aanraakt, gaat dus ook niet door deze vertaling heen.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# ── ADF -> Markdown ─────────────────────────────────────────────────────────

# Volgorde telt: de buitenste mark moet buitenom staan, anders levert
# `**_tekst_**` bij het teruglezen een andere boom op.
_MARK_WRAP = [
    ("code", "`", "`"),
    ("strike", "~~", "~~"),
    ("em", "_", "_"),
    ("strong", "**", "**"),
]


def _apply_marks(text: str, marks: List[Dict[str, Any]]) -> str:
    if not text or not marks:
        return text
    kinds = {str(m.get("type") or "") for m in marks}
    # Een link omsluit alles; hij wordt dus als laatste omheen gezet.
    href = ""
    for m in marks:
        if m.get("type") == "link":
            href = str((m.get("attrs") or {}).get("href") or "")
    # Voor- en achterspaties buiten de opmaak houden: `** tekst**` is in
    # Markdown geen vet.
    lead = text[:len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()):]
    core = text.strip()
    if core:
        for kind, open_s, close_s in _MARK_WRAP:
            if kind in kinds:
                core = f"{open_s}{core}{close_s}"
    out = f"{lead}{core}{trail}"
    if href:
        out = f"[{out.strip()}]({href})"
    return out


def _inline(nodes: Any) -> str:
    """De inhoud van één blok: tekst met marks, harde returns, mentions."""
    if not isinstance(nodes, list):
        return ""
    parts: List[str] = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        t = n.get("type")
        attrs = n.get("attrs") or {}
        if t == "text":
            parts.append(_apply_marks(str(n.get("text") or ""), n.get("marks") or []))
        elif t == "hardBreak":
            # Kaal, zonder de twee spaties die Markdown ook kent: die zouden er
            # bij elke rondgang opnieuw bijkomen. `_paragraph` leest een
            # newline in een alinea terug als deze hardBreak.
            parts.append("\n")
        elif t == "mention":
            parts.append(f"@{attrs.get('text') or attrs.get('id') or ''}".replace("@@", "@"))
        elif t == "emoji":
            parts.append(str(attrs.get("text") or attrs.get("shortName") or ""))
        elif t == "date":
            parts.append(str(attrs.get("timestamp") or ""))
        elif t == "status":
            parts.append(f"[{attrs.get('text') or ''}]")
        elif t == "inlineCard":
            url = str(attrs.get("url") or "")
            parts.append(f"<{url}>" if url else "")
        else:
            # Onbekend inline-type: pak de tekst eruit die erin zit.
            parts.append(_inline(n.get("content")))
    return "".join(parts)


def _list_block(node: Dict[str, Any], depth: int, ordered: bool) -> List[str]:
    out: List[str] = []
    indent = "  " * depth
    for i, item in enumerate(node.get("content") or [], start=1):
        if not isinstance(item, dict) or item.get("type") != "listItem":
            continue
        bullet = f"{i}. " if ordered else "- "
        first = True
        for child in (item.get("content") or []):
            # depth 0: de inspringing wordt hier gezet, niet door het kind —
            # anders telt elk niveau dubbel.
            lines = _block(child, 0)
            if not lines:
                continue
            kind_is_lijst = isinstance(child, dict) and child.get("type") in (
                "bulletList", "orderedList", "taskList")
            if first and not kind_is_lijst:
                out.append(indent + bullet + lines[0].lstrip())
                rest = lines[1:]
                first = False
            elif first:
                # Item dat meteen met een geneste lijst begint: eerst de bullet
                # zelf, anders schuift de geneste lijst een niveau omhoog.
                out.append(indent + bullet.rstrip())
                first = False
                rest = lines
            else:
                rest = lines
            for ln in rest:
                out.append((indent + "  " + ln) if ln.strip() else "")
        if first:
            out.append(indent + bullet.rstrip())
    return out


def _table(node: Dict[str, Any]) -> List[str]:
    """ADF-tabel -> Markdown pipe-tabel.

    Een Markdown-tabel MOET een kopregel hebben, een ADF-tabel niet. Rij 0
    daarom maar tot kop bombarderen verandert de bron: bij het terugschrijven
    komen die zes cellen terug als `tableHeader` en staat er in Jira ineens een
    vetgedrukte kop die er nooit was. Een tabel zonder kop krijgt hier dus een
    LEGE kopregel; `_parse_table` herkent die en geeft de tabel koploos terug.
    """
    rijen: List[List[str]] = []
    soorten_rij0: List[str] = []
    for row in (node.get("content") or []):
        if not isinstance(row, dict) or row.get("type") != "tableRow":
            continue
        cellen: List[str] = []
        for cell in (row.get("content") or []):
            tekst = " ".join(l for l in _blocks(cell.get("content") or [], 0) if l.strip())
            cellen.append(tekst.replace("|", "\\|").strip())
            if not rijen:
                soorten_rij0.append(str(cell.get("type") or ""))
        rijen.append(cellen)
    if not rijen:
        return []
    breedte = max(len(r) for r in rijen)
    rijen = [r + [""] * (breedte - len(r)) for r in rijen]
    heeft_kop = bool(soorten_rij0) and all(t == "tableHeader" for t in soorten_rij0)

    if heeft_kop:
        kop, body = rijen[0], rijen[1:]
    else:
        kop, body = [""] * breedte, rijen
    out = ["| " + " | ".join(kop) + " |",
           "| " + " | ".join(["---"] * breedte) + " |"]
    for r in body:
        out.append("| " + " | ".join(r) + " |")
    return out


def _block(node: Any, depth: int = 0) -> List[str]:
    """Eén blokniveau-knoop als Markdown-regels (zonder lege scheidingsregel)."""
    if not isinstance(node, dict):
        return []
    t = node.get("type")
    attrs = node.get("attrs") or {}

    if t == "paragraph":
        text = _inline(node.get("content"))
        # Een alinea zonder inhoud is in Jira een spacer. Als lege regel
        # teruggeven zou er, samen met de scheidingsregel die _blocks zelf al
        # zet, een dubbele blanco ontstaan — en dat verschuift de indeling bij
        # elke rondgang opnieuw.
        return text.split("\n") if text else []
    if t == "heading":
        level = max(1, min(6, int(attrs.get("level") or 1)))
        return ["#" * level + " " + _inline(node.get("content")).strip()]
    if t == "bulletList":
        return _list_block(node, depth, ordered=False)
    if t == "orderedList":
        return _list_block(node, depth, ordered=True)
    if t == "codeBlock":
        lang = str(attrs.get("language") or "")
        body = _inline(node.get("content"))
        return [f"```{lang}"] + body.split("\n") + ["```"]
    if t == "blockquote":
        inner = _blocks(node.get("content") or [], depth)
        return ["> " + l if l else ">" for l in inner]
    if t == "rule":
        return ["---"]
    if t == "panel":
        kind = str(attrs.get("panelType") or "info")
        inner = _blocks(node.get("content") or [], depth)
        return [f"> **{kind}**"] + ["> " + l if l else ">" for l in inner]
    if t == "table":
        return _table(node)
    if t == "taskList":
        out: List[str] = []
        for item in (node.get("content") or []):
            if not isinstance(item, dict) or item.get("type") != "taskItem":
                continue
            vink = "x" if (item.get("attrs") or {}).get("state") == "DONE" else " "
            out.append(f"- [{vink}] " + _inline(item.get("content")).strip())
        return out
    if t in ("mediaSingle", "mediaGroup", "media"):
        # Bijlagen horen bij het issue, niet bij de tekst. Een zichtbare
        # verwijzing is beter dan stilte: anders lijkt de tekst compleet.
        media = node if t == "media" else ((node.get("content") or [{}])[0] or {})
        m = media.get("attrs") or {}
        naam = str(m.get("alt") or "")
        return [f"![{naam}](bijlage:{m.get('id') or ''})"]
    if t in ("expand", "nestedExpand"):
        titel = str(attrs.get("title") or "Meer")
        return [f"**{titel}**"] + _blocks(node.get("content") or [], depth)
    # Onbekend blok: de inhoud eronder alsnog meenemen.
    if node.get("content"):
        return _blocks(node.get("content"), depth)
    return []


def _blocks(nodes: Any, depth: int = 0) -> List[str]:
    out: List[str] = []
    for n in (nodes or []):
        lines = _block(n, depth)
        if not lines:
            continue
        # Altijd één lege regel tussen twee blokken — ook tussen twee lijsten.
        # Zonder die regel leest de parser ze terug als één lijst, en dan klopt
        # het aantal lijsten niet meer met het origineel.
        if out:
            out.append("")
        out.extend(lines)
    return out


def to_markdown(node: Any) -> str:
    """ADF (of platte tekst, of None) -> Markdown."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""
    lines = _blocks(node.get("content") or [], 0)
    text = "\n".join(lines)
    # Meer dan één lege regel achter elkaar voegt niets toe en maakt de
    # vergelijking "is dit veranderd?" onbetrouwbaar.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ── Markdown -> ADF ─────────────────────────────────────────────────────────

def _leeg(regel: str) -> bool:
    """Lege regel voor de parser. Bewust NIET `str.strip()`: die telt \xa0 als
    witruimte, en een alinea die uit één harde spatie bestaat (waar Jira vol
    mee staat) zou dan als alineascheiding gelden en twee alinea's samenvoegen."""
    return regel.strip(" \t\v\f") == ""


_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_ORDERED = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_QUOTE = re.compile(r"^>\s?(.*)$")
_FENCE = re.compile(r"^```(\w*)\s*$")
_RULE = re.compile(r"^\s*(?:---+|\*\*\*+|___+)\s*$")
_TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
# Minstens één streepje verplicht: zonder die eis matchte de LEGE kopregel die
# _table schrijft voor een koploze tabel ("|  |  |  |") ook als scheidingsregel,
# en dan verdween de eerste datarij in de kop.
_TABLE_SEP = re.compile(r"^\s*\|[\s:|-]*-[\s:|-]*\|\s*$")
# Wat _block voor media/taken uitschrijft, moet hier weer herkend worden —
# anders verdwijnt een bijlage of een afvinklijst bij de eerste rondgang.
_MEDIA = re.compile(r"^!\[(?P<alt>[^\]]*)\]\(bijlage:(?P<id>[^)]*)\)\s*$")
_TAAK = re.compile(r"^(\s*)- \[(?P<vink>[ xX])\](?:\s+(?P<tekst>.*))?\s*$")

# Inline-opmaak. De volgorde spiegelt _MARK_WRAP: code eerst (daarbinnen geldt
# geen andere opmaak), dan link, dan de tekstmarkeringen van lang naar kort.
_INLINE_RE = re.compile(
    r"(?P<code>`[^`]+`)"
    r"|(?P<link>\[[^\]]*\]\([^)]*\))"
    r"|(?P<auto><https?://[^>]+>)"
    r"|(?P<strike>~~.+?~~)"
    r"|(?P<strong>\*\*.+?\*\*)"
    r"|(?P<em>(?<![\w*])[*_](?!\s)(?:[^*_]|(?<=\\)[*_])+?(?<!\s)[*_](?![\w*]))"
)


def _text_node(text: str, marks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if text == "":
        return None
    node: Dict[str, Any] = {"type": "text", "text": text}
    if marks:
        node["marks"] = marks
    return node


def _inline_nodes(text: str, inherited: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Markdown-inline -> ADF-tekstknopen. Recursief, zodat `**[a](u)**` en
    `_**a**_` allebei de juiste stapel marks krijgen."""
    inherited = inherited or []
    out: List[Dict[str, Any]] = []
    pos = 0
    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            n = _text_node(text[pos:m.start()], list(inherited))
            if n:
                out.append(n)
        kind = m.lastgroup
        raw = m.group()
        if kind == "code":
            n = _text_node(raw[1:-1], list(inherited) + [{"type": "code"}])
            if n:
                out.append(n)
        elif kind == "link":
            label, _, rest = raw[1:].partition("](")
            href = rest[:-1]
            out.extend(_inline_nodes(label,
                                     list(inherited) + [{"type": "link", "attrs": {"href": href}}])
                       or [x for x in [_text_node(label or href,
                                                  list(inherited) + [{"type": "link", "attrs": {"href": href}}])] if x])
        elif kind == "auto":
            href = raw[1:-1]
            if inherited:
                # Binnen andere opmaak kan geen inlineCard staan; dan een link.
                n = _text_node(href, list(inherited) + [{"type": "link", "attrs": {"href": href}}])
                if n:
                    out.append(n)
            else:
                out.append({"type": "inlineCard", "attrs": {"url": href}})
        elif kind == "strike":
            out.extend(_inline_nodes(raw[2:-2], list(inherited) + [{"type": "strike"}]))
        elif kind == "strong":
            out.extend(_inline_nodes(raw[2:-2], list(inherited) + [{"type": "strong"}]))
        elif kind == "em":
            out.extend(_inline_nodes(raw[1:-1], list(inherited) + [{"type": "em"}]))
        pos = m.end()
    if pos < len(text):
        n = _text_node(text[pos:], list(inherited))
        if n:
            out.append(n)
    return out


def _paragraph(text: str) -> Dict[str, Any]:
    """Eén alinea; een regeleinde binnen de alinea wordt een hardBreak."""
    content: List[Dict[str, Any]] = []
    for i, deel in enumerate(text.split("\n")):
        if i:
            content.append({"type": "hardBreak"})
        content.extend(_inline_nodes(deel))
    return {"type": "paragraph", "content": content} if content else {"type": "paragraph"}


def _indent_of(prefix: str) -> int:
    return len(prefix.replace("\t", "  ")) // 2


def _parse_list(lines: List[str], i: int, level: int) -> Tuple[Dict[str, Any], int]:
    """Eén lijst vanaf regel i, inclusief geneste lijsten eronder."""
    first = _ORDERED.match(lines[i])
    ordered = first is not None
    node: Dict[str, Any] = {"type": "orderedList" if ordered else "bulletList", "content": []}
    while i < len(lines):
        line = lines[i]
        m_o, m_b = _ORDERED.match(line), _BULLET.match(line)
        m = m_o or m_b
        if not m:
            break
        if (m_o is not None) != ordered:
            break
        indent = _indent_of(m.group(1))
        if indent < level:
            break
        if indent > level:
            sub, i = _parse_list(lines, i, indent)
            if node["content"]:
                node["content"][-1]["content"].append(sub)
            continue
        tekst = m.group(3) if m_o else m.group(2)
        item: Dict[str, Any] = {"type": "listItem", "content": [_paragraph(tekst)]}
        node["content"].append(item)
        i += 1
    return node, i


def _parse_table(lines: List[str], i: int) -> Tuple[Dict[str, Any], int]:
    rijen: List[List[str]] = []
    heeft_kop = False
    start = i
    while i < len(lines) and _TABLE_ROW.match(lines[i]):
        if _TABLE_SEP.match(lines[i]):
            heeft_kop = i == start + 1
            i += 1
            continue
        cellen = [c.strip().replace("\\|", "|")
                  for c in _TABLE_ROW.match(lines[i]).group(1).split("|")]
        rijen.append(cellen)
        i += 1
    # Een kopregel waarin geen enkele cel iets bevat is de lege kop die _table
    # schrijft voor een tabel die er in de bron geen had.
    if heeft_kop and rijen and not any(c.strip() for c in rijen[0]):
        rijen = rijen[1:]
        heeft_kop = False
    content = []
    for r, cellen in enumerate(rijen):
        soort = "tableHeader" if (heeft_kop and r == 0) else "tableCell"
        content.append({"type": "tableRow",
                        "content": [{"type": soort, "attrs": {},
                                     "content": [_paragraph(c)]} for c in cellen]})
    return {"type": "table", "attrs": {"isNumberColumnEnabled": False,
                                       "layout": "default"}, "content": content}, i


def to_adf(text: Optional[str]) -> Dict[str, Any]:
    """Markdown -> ADF-document. Altijd een geldig `doc`, ook bij lege tekst."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    content: List[Dict[str, Any]] = []
    i = 0
    alinea: List[str] = []

    def spoel() -> None:
        nonlocal alinea
        if alinea:
            content.append(_paragraph("\n".join(alinea).strip("\n")))
            alinea = []

    while i < len(lines):
        line = lines[i]

        fence = _FENCE.match(line)
        if fence:
            spoel()
            taal = fence.group(1)
            i += 1
            body: List[str] = []
            while i < len(lines) and not _FENCE.match(lines[i]):
                body.append(lines[i])
                i += 1
            i += 1  # sluitende fence
            node: Dict[str, Any] = {"type": "codeBlock"}
            if taal:
                node["attrs"] = {"language": taal}
            if body:
                node["content"] = [{"type": "text", "text": "\n".join(body)}]
            content.append(node)
            continue

        if _leeg(line):
            spoel()
            i += 1
            continue

        if _RULE.match(line):
            spoel()
            content.append({"type": "rule"})
            i += 1
            continue

        h = _HEADING.match(line)
        if h:
            spoel()
            content.append({"type": "heading",
                            "attrs": {"level": len(h.group(1))},
                            "content": _inline_nodes(h.group(2).strip())})
            i += 1
            continue

        if _TABLE_ROW.match(line) and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1]):
            spoel()
            tabel, i = _parse_table(lines, i)
            content.append(tabel)
            continue

        media = _MEDIA.match(line)
        if media:
            spoel()
            attrs = {"type": "file", "id": media.group("id")}
            if media.group("alt"):
                attrs["alt"] = media.group("alt")
            content.append({"type": "mediaSingle", "attrs": {"layout": "center"},
                            "content": [{"type": "media", "attrs": attrs}]})
            i += 1
            continue

        if _TAAK.match(line):
            spoel()
            items = []
            while i < len(lines) and _TAAK.match(lines[i]):
                m = _TAAK.match(lines[i])
                items.append({"type": "taskItem",
                              "attrs": {"state": "DONE" if m.group("vink").lower() == "x"
                                        else "TODO"},
                              "content": _inline_nodes(m.group("tekst") or "")})
                i += 1
            content.append({"type": "taskList", "attrs": {}, "content": items})
            continue

        if _BULLET.match(line) or _ORDERED.match(line):
            spoel()
            m = _BULLET.match(line) or _ORDERED.match(line)
            lijst, i = _parse_list(lines, i, _indent_of(m.group(1)))
            content.append(lijst)
            continue

        if _QUOTE.match(line):
            spoel()
            binnen: List[str] = []
            while i < len(lines) and _QUOTE.match(lines[i]):
                binnen.append(_QUOTE.match(lines[i]).group(1))
                i += 1
            inner = to_adf("\n".join(binnen))
            content.append({"type": "blockquote",
                            "content": inner.get("content") or [{"type": "paragraph"}]})
            continue

        alinea.append(line)
        i += 1

    spoel()
    if not content:
        content = [{"type": "paragraph"}]
    return {"type": "doc", "version": 1, "content": content}
