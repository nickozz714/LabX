"""Meldingen en de weg terug.

De weg terug is het punt van deze functie: je leest op je telefoon dat een
ticket vastloopt en typt "gebruik TST". Twee dingen bepalen of dat aankomt —
of het ANTWOORD schoon uit de mail komt (niet de hele geciteerde draad), en of
LabX weet bij WELKE sessie het hoort. Beide worden hier vastgelegd.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.notify import mail, telegram  # noqa: E402
from services.notify import notify_service as ns  # noqa: E402


# ── mail: alleen het nieuwe deel van een antwoord ───────────────────────────

def test_citaat_van_een_nederlandse_client():
    tekst = (
        "Gebruik TST, niet ACC.\n"
        "\n"
        "Op wo 10 sep 2026 om 18:04 schreef LabX <labx@voorbeeld.nl>:\n"
        "> SWI-88 staat stil: ik weet niet welke omgeving ik moet gebruiken.\n"
        "> Antwoord op dit bericht om verder te gaan.\n")
    assert mail.knip_citaat(tekst) == "Gebruik TST, niet ACC."


def test_citaat_van_outlook():
    tekst = ("Ja, ga door.\n\n"
             "-----Oorspronkelijk bericht-----\n"
             "Van: LabX\nVerzonden: woensdag 10 september 2026\n"
             "Onderwerp: SWI-88\n\nDe agent wacht.\n")
    assert mail.knip_citaat(tekst) == "Ja, ga door."


def test_handtekening_gaat_eraf():
    assert mail.knip_citaat("Doe maar.\n\n-- \nNick\n06-12345678") == "Doe maar."


def test_antwoord_zonder_citaat_blijft_heel():
    tekst = "Gebruik TST.\nEn kijk ook even naar de logging."
    assert mail.knip_citaat(tekst) == tekst


def test_alleen_citaat_levert_liever_iets_dan_niets():
    """Een mail die uitsluitend uit citaat bestaat: liever de ruwe tekst dan een
    leeg bericht, want leeg wordt genegeerd en dan is het antwoord weg."""
    assert mail.knip_citaat("> alleen citaat").strip() != ""


# ── mail: het merkteken in het onderwerp ────────────────────────────────────

def test_merkteken_is_terug_te_vinden():
    onderwerp = f"Re: SWI-88 klaar {mail.merkteken('0004d2')}"
    assert mail.zoek_merkteken(onderwerp) == "0004d2"
    assert int(mail.zoek_merkteken(onderwerp), 16) == 1234


def test_geen_merkteken_geeft_none():
    assert mail.zoek_merkteken("Re: gewoon een mailtje") is None
    assert mail.zoek_merkteken("") is None


# ── telegram: opmaak mag het bericht niet tegenhouden ───────────────────────

@pytest.mark.parametrize("tekst", [
    "SWI-88: klaar (2 van de 3)",
    "Pad: /workspace/uploads/a-b_c.txt",
    "Fout: `cat` gaf exit 1 — zie #INC-22949",
    "100% klaar! [details] {json} <tag>",
])
def test_telegram_ontsnapt_alles_wat_hij_moet(tekst):
    """MarkdownV2 weigert het HELE bericht bij één niet-ontsnapt teken. Een
    melding die niet aankomt omdat er een punt in stond is de vervelendste
    soort storing: je merkt hem niet."""
    uit = telegram._escape(tekst)
    for i, teken in enumerate(uit):
        if teken in telegram._SPECIAAL:
            assert i > 0 and uit[i - 1] == "\\", f"{teken!r} niet ontsnapt in {uit!r}"


def test_telegram_kort_lange_tekst_in():
    lang = "x" * 9000
    uit = telegram._kort(lang)
    assert len(uit) < 4096
    assert "ingekort" in uit


# ── de weg terug ────────────────────────────────────────────────────────────

class NepQuery:
    def __init__(self, rijen): self.rijen = rijen
    def filter(self, *a, **k): return self
    def order_by(self, *a, **k): return self
    def first(self): return self.rijen[0] if self.rijen else None


class NepDb:
    def __init__(self, rijen): self.rijen = rijen
    def query(self, *a, **k): return NepQuery(self.rijen)


def _kanaal():
    return SimpleNamespace(id=1, kind="telegram", name="telefoon", config={},
                           allow_reply=True)


def test_antwoord_op_een_bericht_vindt_die_melding():
    oud = SimpleNamespace(id=1, external_id="100", channel_id=1, status="sent")
    ns_db = NepDb([oud])
    assert ns._zoek_melding(ns_db, _kanaal(), external_id="100") is oud


def test_zonder_verwijzing_valt_hij_terug_op_de_laatste():
    """Op een telefoon typ je gewoon 'ja doe maar' zonder de antwoordknop. Dan
    bedoel je het bericht dat je net las — en een antwoord dat nergens heen kan
    is erger dan een antwoord dat naar het meest recente gesprek gaat."""
    laatste = SimpleNamespace(id=9, external_id="900", channel_id=1, status="sent")
    assert ns._zoek_melding(NepDb([laatste]), _kanaal(), external_id=None) is laatste
    assert ns._zoek_melding(NepDb([laatste]), _kanaal(), external_id="") is laatste


def test_niets_verstuurd_geeft_geen_melding_terug():
    assert ns._zoek_melding(NepDb([]), _kanaal(), external_id="100") is None


# ── kanaalkeuze ─────────────────────────────────────────────────────────────

class NepKanaalQuery:
    def __init__(self, rijen): self.rijen = rijen
    def filter(self, *a, **k): return self
    def all(self): return self.rijen


class NepKanaalDb:
    def __init__(self, rijen): self.rijen = rijen
    def query(self, *a, **k): return NepKanaalQuery(self.rijen)


def test_kanaal_zonder_keuze_krijgt_alles():
    """Wat je bedoelt als je net een kanaal hebt aangemaakt en nog niets hebt
    aangevinkt: laat maar horen."""
    kanaal = SimpleNamespace(id=1, enabled=True, events=[])
    assert ns.kanalen_voor(NepKanaalDb([kanaal]), "storing") == [kanaal]


def test_kanaal_met_keuze_krijgt_alleen_dat():
    kanaal = SimpleNamespace(id=1, enabled=True, events=["aandacht_nodig"])
    db = NepKanaalDb([kanaal])
    assert ns.kanalen_voor(db, "aandacht_nodig") == [kanaal]
    assert ns.kanalen_voor(db, "run_klaar") == []


def test_onbekende_gebeurtenis_doet_niets():
    """Een typfout in een meld()-aanroep mag geen uitzondering opleveren in de
    code die hem aanroept — dat is meestal een afloop-hook van een run."""
    ns.meld("bestaat_niet", "titel", "tekst", {})


def test_samenvatting_wordt_ingekort_met_spoor():
    lang = "y" * 5000
    uit = ns._kort(lang)
    assert len(uit) < 1500
    assert "ingekort" in uit


# ── de offset: één plek waar een fout je alles dubbel laat doen ─────────────

def test_offset_is_hoogste_update_plus_een():
    """Telegram bevestigt met de VOLGENDE update_id. Stuur je het id zelf terug,
    dan krijg je hetzelfde bericht elke ronde opnieuw — en start LabX bij elke
    ronde opnieuw een agent-run op hetzelfde antwoord.

    Dit legt de rekensom vast zoals _inbox_telegram hem maakt.
    """
    updates = [{"update_id": 7}, {"update_id": 9}, {"update_id": 8}]
    hoogste = 0
    for u in updates:
        hoogste = max(hoogste, int(u["update_id"]) + 1)
    assert hoogste == 10


def test_afsluiter_nodigt_uit_om_te_antwoorden():
    """Zonder die regel weet niemand dat terugpraten kan, en dan is de weg
    terug er wel maar wordt hij niet gebruikt."""
    kanaal = SimpleNamespace(allow_reply=True)
    regel = SimpleNamespace(body="De agent is klaar.", context={"ticket_key": "SWI-88"})
    tekst = ns._met_afsluiter(kanaal, regel)
    assert "SWI-88" in tekst
    assert "Antwoord op dit bericht" in tekst


def test_kanaal_zonder_antwoord_krijgt_geen_uitnodiging():
    kanaal = SimpleNamespace(allow_reply=False)
    regel = SimpleNamespace(body="Klaar.", context={})
    assert ns._met_afsluiter(kanaal, regel) == "Klaar."
