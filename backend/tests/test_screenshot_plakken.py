"""Een screenshot plakken in plaats van opslaan-en-kiezen.

Dit gaat dezelfde weg als de paperclip die er al was: het plaatje wordt naar
het lab geüpload en in je bericht komt het PAD. Dat is geen omweg maar de
bedoeling -- de agent werkt in dat lab en opent het daar met het gereedschap
dat erbij hoort.

De frontend heeft geen testrunner, dus wordt hier gelezen wat er staat. Dat
vangt niet alles, maar wel het soort fout dat stil is: een haak die bestaat
en nergens wordt aangeroepen.
"""
from pathlib import Path

WORTEL = Path(__file__).resolve().parents[2] / "frontend" / "src"
BIJLAGEN = (WORTEL / "components" / "Bijlagen.tsx").read_text(encoding="utf-8")
CHAT = (WORTEL / "pages" / "ChatPage.tsx").read_text(encoding="utf-8")
TICKET = (WORTEL / "components" / "TicketDrawer.tsx").read_text(encoding="utf-8")


def test_de_haak_bestaat_naast_de_paperclip():
    """In hetzelfde bestand, want het is dezelfde weg met een andere
    aanleiding."""
    assert "export function usePlakbijlage" in BIJLAGEN
    assert "export function BijlageKnop" in BIJLAGEN


def test_gewone_tekst_blijft_gewone_tekst():
    """Alleen bij een afbeelding onderscheppen we het plakken; anders moet de
    browser de tekst gewoon in het veld laten vallen."""
    assert 'i.type.startsWith("image/")' in BIJLAGEN
    haak = BIJLAGEN[BIJLAGEN.index("export function usePlakbijlage"):]
    volgorde = haak.index("if (!plaatjes.length) return;") < haak.index("e.preventDefault()")
    assert volgorde, "pas tegenhouden als het écht een afbeelding is"


def test_een_geplakt_plaatje_krijgt_een_eigen_naam():
    """Het klembord levert geen bestandsnaam; zonder dit heet alles image.png
    en overschrijft het elkaar in het lab."""
    assert "schermafbeelding-" in BIJLAGEN
    assert "toISOString" in BIJLAGEN


def test_plakken_staat_uit_als_het_veld_uit_staat():
    assert "if (disabled || !labId) return;" in BIJLAGEN


def test_het_werkt_in_de_chat():
    assert "usePlakbijlage" in CHAT
    assert "onPaste={onPaste}" in CHAT


def test_het_werkt_bij_de_opdracht_en_bij_een_opmerking():
    """De twee plekken op een bord-item waar je iets typt."""
    assert "usePlakbijlage" in TICKET
    assert TICKET.count("onPaste={onPaste}") == 2, (
        "zowel de instructie voor de run als het opmerkingenveld")


def test_er_komt_geen_tweede_uploadweg_bij():
    """Eén weg naar het lab houdt de limieten, de overgeslagen bestanden en
    de foutmeldingen op één plek."""
    haak = BIJLAGEN[BIJLAGEN.index("export function usePlakbijlage"):]
    assert "labsApi.upload(" in haak
    assert "fetch(" not in haak


def test_de_haak_staat_voor_de_vroege_return():
    """Dit sloopte het ticketscherm, en typecontrole zag het niet.

    In TicketDrawer staat `if (!ticket) return <Laden…>` terwijl het ticket
    nog opgehaald wordt. Een haak die DAARNA wordt aangeroepen, draait de ene
    render wel en de andere niet; React telt haken en gooit dan "Rendered more
    hooks than during the previous render". Resultaat: de hele interface klapt
    om zodra je een ticket opent.

    De lint van dit project meldt het niet (64 waarschuwingen, geen fouten),
    dus staat het hier.
    """
    haak = TICKET.index("usePlakbijlage({")
    vroege_return = TICKET.index("if (!ticket) {")

    assert haak < vroege_return, (
        "usePlakbijlage hoort bij de andere haken te staan, vóór elke return")
