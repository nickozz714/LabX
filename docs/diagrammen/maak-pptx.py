#!/usr/bin/env python3
"""maak-pptx.py — zet de platen in deze map om naar één PowerPoint.

    python3 maak-pptx.py --titel "LabX" --ondertitel "..." --uit LabX-platen.pptx

Meerdere series in één deck (elke sectie krijgt een tussendia):

    python3 maak-pptx.py --titel "LabX en Nectar" --uit beide.pptx \
        --sectie "LabX|../../../LabX/docs/diagrammen|3B82F6|zeven platen" \
        --sectie "Nectar|../../../Nectar/docs/diagrammen|E08C1E|acht platen"

**Waarom er een PNG én de SVG in het bestand zit.** PowerPoint toont sinds 2019
een ingebedde SVG rechtstreeks (scherp op elk formaat, en met rechtermuis →
"Converteren naar vorm" tot losse vormen te maken). Oudere versies en de
web-viewer kennen dat niet en vallen terug op de bitmap die er als voorvertoning
bij hoort. Beide meeleveren kost wat bestandsgrootte en levert een deck op dat
overal opengaat.

De bitmap wordt gemaakt met Chrome in headless-modus, en niet met ImageMagick:
die laatste rendert SVG-tekst met een eigen, beperkte engine en maakt er een
potje van zodra er een lettertype-fallback aan te pas komt. Chrome gebruikt
gewoon de tekstopmaak van het systeem — precies wat je in een presentatie ook
ziet.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.opc.package import Part
from pptx.opc.packuri import PackURI
from pptx.oxml.ns import qn, _nsmap
from pptx.util import Emu, Pt

HIER = Path(__file__).resolve().parent
BREED, HOOG = Emu(12192000), Emu(6858000)          # 13,333 × 7,5 inch = 16:9
SCHAAL = 2                                          # 3200 × 1800 px voorvertoning

# De svgBlip-uitbreiding van Microsoft. Zonder deze prefix in de namespace-tabel
# verzint lxml er zelf een (ns0); dat werkt, maar is onleesbaar in de XML.
_nsmap.setdefault("asvg", "http://schemas.microsoft.com/office/drawing/2016/SVG/main")
SVG_EXT_URI = "{96DAC541-7B7A-43D3-8B79-37D633B846F1}"


def chrome() -> str:
    kandidaten = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ]
    for naam in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
        pad = shutil.which(naam)
        if pad:
            kandidaten.insert(0, pad)
    for pad in kandidaten:
        if Path(pad).exists():
            return pad
    sys.exit("Geen Chrome/Chromium/Edge gevonden om de platen mee te renderen.")


def naar_png(svg: Path, png: Path, browser: str) -> None:
    png.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [browser, "--headless", "--disable-gpu", "--hide-scrollbars",
         f"--force-device-scale-factor={SCHAAL}", "--window-size=1600,900",
         "--default-background-color=FFFFFFFF", f"--screenshot={png}", svg.as_uri()],
        check=True, capture_output=True,
    )
    if not png.exists():
        sys.exit(f"Renderen van {svg.name} leverde geen bestand op.")


def hang_svg_aan(dia, plaatje, svg: Path, nummer: int) -> None:
    """De SVG als tweede bron aan hetzelfde plaatje hangen.

    PowerPoint kiest dan zelf: kent hij svgBlip, dan toont hij de vector; zo
    niet, dan de bitmap die er al in zat. Eén vorm, twee bronnen — geen twee
    plaatjes over elkaar heen.
    """
    pakket = dia.part.package
    deel = Part(PackURI(f"/ppt/media/plaat{nummer}.svg"), "image/svg+xml",
                pakket, svg.read_bytes())
    rId = dia.part.relate_to(deel, RT.IMAGE)

    blip = plaatje._element.blipFill.blip
    ext_lst = blip.makeelement(qn("a:extLst"), {})
    ext = blip.makeelement(qn("a:ext"), {"uri": SVG_EXT_URI})
    svg_blip = blip.makeelement(qn("asvg:svgBlip"), {qn("r:embed"): rId})
    ext.append(svg_blip)
    ext_lst.append(ext)
    blip.append(ext_lst)


def tekstvak(dia, x, y, b, h, tekst, *, grootte, kleur, vet=False, spatie=0):
    vak = dia.shapes.add_textbox(Emu(x), Emu(y), Emu(b), Emu(h))
    kader = vak.text_frame
    kader.word_wrap = True
    p = kader.paragraphs[0]
    p.alignment = PP_ALIGN.LEFT
    run = p.add_run()
    run.text = tekst
    run.font.size = Pt(grootte)
    run.font.bold = vet
    run.font.color.rgb = RGBColor.from_string(kleur)
    run.font.name = "Archivo"
    if spatie:
        run.font._rPr.set("spc", str(int(spatie * 100)))
    return vak


def donkere_dia(prs, titel: str, ondertitel: str, accent: str, *, klein=False) -> None:
    """Titel- of tussendia: donker vlak, accentbalk, twee regels tekst.

    Dezelfde vorm voor allebei, alleen kleiner gezet bij een tussendia — die
    hoort een adempauze te zijn en geen tweede opening."""
    dia = prs.slides.add_slide(prs.slide_layouts[6])
    vlak = dia.shapes.add_shape(1, Emu(0), Emu(0), BREED, HOOG)   # 1 = rechthoek
    vlak.fill.solid()
    vlak.fill.fore_color.rgb = RGBColor.from_string("0B1220")
    vlak.line.fill.background()
    vlak.shadow.inherit = False

    balk = dia.shapes.add_shape(1, Emu(914400), Emu(2200000), Emu(1100000), Emu(60000))
    balk.fill.solid()
    balk.fill.fore_color.rgb = RGBColor.from_string(accent)
    balk.line.fill.background()
    balk.shadow.inherit = False

    tekstvak(dia, 914400, 2500000, 9600000, 1200000, titel,
             grootte=38 if klein else 54, kleur="FFFFFF", vet=True)
    if ondertitel:
        tekstvak(dia, 914400, 3450000 if klein else 3700000, 9600000, 900000, ondertitel,
                 grootte=18 if klein else 20, kleur="94A3B8")


def bouw(secties: list[dict], titel: str, ondertitel: str, accent: str, uit: Path) -> None:
    browser = chrome()
    prs = Presentation()
    prs.slide_width, prs.slide_height = BREED, HOOG
    donkere_dia(prs, titel, ondertitel, accent)

    teller = 0
    for sectie in secties:
        map_ = Path(sectie["map"]).resolve()
        platen = sorted(map_.glob("*.svg"))
        if not platen:
            sys.exit(f"Geen platen gevonden in {map_}")

        if len(secties) > 1:
            donkere_dia(prs, sectie["naam"], sectie.get("ondertitel", ""),
                        sectie.get("accent", accent), klein=True)
            print(f"\n{sectie['naam']}")

        notities = {}
        nj = map_ / "notities.json"
        if nj.exists():
            notities = json.loads(nj.read_text())

        for svg in platen:
            teller += 1
            # De cache staat bij de bron, niet bij het script: bij een deck over
            # meerdere series zou anders de ene map vol raken met renders van de
            # andere.
            png = map_ / ".png-cache" / f"{svg.stem}.png"
            if not png.exists() or png.stat().st_mtime < svg.stat().st_mtime:
                naar_png(svg, png, browser)
            dia = prs.slides.add_slide(prs.slide_layouts[6])
            plaatje = dia.shapes.add_picture(str(png), Emu(0), Emu(0), BREED, HOOG)
            hang_svg_aan(dia, plaatje, svg, teller)
            if notities.get(svg.name):
                dia.notes_slide.notes_text_frame.text = notities[svg.name]
            print(f"  {svg.name}")

    prs.save(str(uit))
    print(f"\n{uit}  ({uit.stat().st_size / 1_048_576:.1f} MB, {len(prs.slides._sldIdLst)} dia's)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--titel", required=True)
    ap.add_argument("--ondertitel", default="")
    ap.add_argument("--accent", default="3B82F6", help="hex, zonder #")
    ap.add_argument("--map", default=str(HIER), help="één serie; genegeerd als --sectie wordt gebruikt")
    ap.add_argument("--sectie", action="append", default=[],
                    help='"naam|map|accent|ondertitel" — herhaalbaar')
    ap.add_argument("--uit", required=True)
    a = ap.parse_args()

    if a.sectie:
        secties = []
        for ruw in a.sectie:
            deel = (ruw.split("|") + ["", "", ""])[:4]
            secties.append({"naam": deel[0], "map": deel[1],
                            "accent": deel[2] or a.accent, "ondertitel": deel[3]})
    else:
        secties = [{"naam": a.titel, "map": a.map, "accent": a.accent}]

    bouw(secties, a.titel, a.ondertitel, a.accent, Path(a.uit))


if __name__ == "__main__":
    main()
