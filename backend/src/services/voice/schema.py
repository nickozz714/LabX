"""Het toolschema dat een brein te zien krijgt.

Zestien functies, in de vorm waarin je ze uitspreekt. LabX heeft er 190 in zijn
API; die allemaal aanbieden zou de context vullen met schema's voordat er iets
gezegd is — en het zou de verboden acties binnen handbereik brengen.

De beschrijvingen zijn geschreven vóór het model, niet vóór een ontwikkelaar:
ze zeggen wannéér je iets gebruikt, niet wat het technisch doet.
"""
from __future__ import annotations

from typing import Any, Dict, List


def _t(naam: str, omschrijving: str, props: Dict[str, Any],
       verplicht: List[str] | None = None) -> Dict[str, Any]:
    return {
        "type": "function",
        "name": naam,
        "description": omschrijving,
        "parameters": {"type": "object", "properties": props,
                       "required": verplicht or []},
    }


_TICKET = {"type": "string",
           "description": "Hoe de gebruiker het ticket noemde: een sleutel als "
                          "KRI-114, of een omschrijving als 'het Holland Malt ticket'."}
_KLANT = {"type": "string",
          "description": "De klant of het bord, als de gebruiker dat noemde."}

TOOLSCHEMA: List[Dict[str, Any]] = [
    # ── lezen ────────────────────────────────────────────────────────────
    _t("wat_loopt_er",
       "Wat er op dit moment draait: agents op tickets, workflows en "
       "achtergrondtaken. Gebruik dit bij 'wat loopt er', 'waar ben ik mee bezig' "
       "of 'is er nog iets aan de gang'.",
       {}),
    _t("ticket_status",
       "Hoe het met één ticket staat: de kolom, of er een agent op draait en hoe "
       "lang, waar die volgens zijn stappen mee bezig is, en de laatste opmerking. "
       "Dit is de tool voor 'hoe staat het met …'.",
       {"ticket": _TICKET, "klant": _KLANT}, ["ticket"]),
    _t("zoek_tickets",
       "Tickets opzoeken, bijvoorbeeld 'wat staat er open bij Swinkels' of "
       "'welke tickets gaan over doelgroepenvervoer'.",
       {"klant": _KLANT,
        "kolom": {"type": "string", "description": "Bijvoorbeeld todo of review."},
        "zoek": {"type": "string", "description": "Woorden uit de titel."}}),
    _t("chat_status",
       "De laatste chats en of er nog iets in loopt.",
       {"lab": {"type": "string", "description": "Het lab, als de gebruiker dat noemde."}}),
    _t("workflow_status",
       "Hoe de laatste run van een workflow afliep.",
       {"workflow": {"type": "string", "description": "De naam van de workflow."}}),
    _t("schedules_overzicht",
       "Wat er gepland staat en wanneer het volgende draait.", {}),
    _t("lab_details",
       "Hoe een lab is ingericht: het beeld, hoeveel werkers er staan en "
       "mogen staan, hoeveel sessies er tegelijk per werker mogen, en welk "
       "Azure-profiel eraan hangt.",
       {"lab": {"type": "string", "description": "De naam van het lab."}}, ["lab"]),
    _t("workflow_details",
       "Wat een workflow precies doet: de stappen op volgorde en welke "
       "parameters hij vraagt.",
       {"workflow": {"type": "string", "description": "De naam van de workflow."}},
       ["workflow"]),
    _t("schedule_details",
       "Wat er in een planning staat: het patroon, of hij aan staat, en "
       "wanneer hij voor het laatst draaide en weer draait.",
       {"schedule": {"type": "string", "description": "De naam van de planning."}},
       ["schedule"]),
    _t("labs_status",
       "Welke labs draaien en hoeveel werkers er bezet zijn.", {}),
    _t("uren_overzicht",
       "Hoeveel uren er op een periode staan, per project, met de overlap van "
       "werk dat tegelijk liep.",
       {"van": {"type": "string", "description": "Begindag, JJJJ-MM-DD."},
        "tot": {"type": "string", "description": "Einddag, JJJJ-MM-DD."}}),

    # ── schrijven (altijd via bevestiging) ───────────────────────────────
    # ── begeleide opdracht ───────────────────────────────────────────────
    # Deze vier voeren het gesprek; rond_opdracht_af is de schrijfactie.
    _t("begin_opdracht",
       "Begin met het opbouwen van een planning of een workflow. Gebruik dit "
       "als de gebruiker er eentje wil MAKEN -- dat kost meerdere beurten, en "
       "jij stelt steeds de vraag die je terugkrijgt.",
       {"soort": {"type": "string", "enum": ["planning", "workflow"],
                  "description": "Wat er opgebouwd wordt."}},
       ["soort"]),
    _t("vul_aan",
       "Het antwoord van de gebruiker op de vraag die nu openstaat. Geef "
       "letterlijk door wat hij zei; je hoeft niet te weten bij welk veld het "
       "hoort, dat weet het systeem. Bij een stappenlijst sluit 'klaar' de "
       "lijst af.",
       {"waarde": {"type": "string", "description": "Wat de gebruiker zei."}},
       ["waarde"]),
    _t("toon_concept",
       "Wat er tot nu toe is opgebouwd. Gebruik dit bij 'wat heb ik nu' of "
       "'lees het nog eens voor'.", {}),
    _t("stop_opdracht",
       "Gooi de lopende opdracht weg. Gebruik dit bij 'laat maar', 'stop' of "
       "'begin opnieuw'.", {}),
    _t("start_agent",
       "Zet de agent aan het werk op een ticket. De gebruiker moet dit eerst "
       "bevestigen; die bevestigingszin krijg je terug en lees je voor.",
       {"ticket": _TICKET, "klant": _KLANT,
        "instructie": {"type": "string",
                       "description": "Wat de agent moet doen, in de woorden van "
                                      "de gebruiker, als volledige opdracht."}},
       ["ticket"]),
    _t("maak_ticket",
       "Maak een nieuw ticket aan. Vraag door tot je een bruikbare titel hebt; "
       "'maak een ticket' zonder onderwerp is geen opdracht.",
       {"klant": _KLANT, "titel": {"type": "string"},
        "omschrijving": {"type": "string"}},
       ["klant", "titel"]),
    _t("verplaats_ticket",
       "Zet een ticket in een andere kolom.",
       {"ticket": _TICKET, "klant": _KLANT,
        "kolom": {"type": "string", "description": "De kolom, zoals de gebruiker "
                                                   "hem noemde."}},
       ["ticket", "kolom"]),
    _t("plaats_opmerking",
       "Zet een opmerking bij een ticket. Gebruik de woorden van de gebruiker.",
       {"ticket": _TICKET, "klant": _KLANT, "tekst": {"type": "string"}},
       ["ticket", "tekst"]),
    _t("stop_agent",
       "Breek de agent af die op een ticket draait. Het werk van die run gaat "
       "verloren.",
       {"ticket": _TICKET, "klant": _KLANT}, ["ticket"]),
    _t("start_chat",
       "Start een chat op een lab en stel daar meteen een vraag.",
       {"lab": {"type": "string"}, "vraag": {"type": "string"}},
       ["lab", "vraag"]),
    _t("start_workflow",
       "Voer een workflow uit op een lab.",
       {"workflow": {"type": "string"}, "lab": {"type": "string"}},
       ["workflow"]),
    _t("rond_opdracht_af",
       "De opgebouwde planning of workflow daadwerkelijk aanmaken. Doe dit "
       "pas als alles ingevuld is; je krijgt een bevestigingszin terug met "
       "het hele concept erin.", {}),
    _t("wijzig_werkers",
       "Meer of minder werkers in een lab. Gebruik dit bij 'zet er meer "
       "werkers op' of 'schaal terug'.",
       {"lab": {"type": "string", "description": "De naam van het lab."},
        "maximaal": {"type": "integer", "description": "Het nieuwe maximum."},
        "minimaal": {"type": "integer", "description": "Het nieuwe minimum."}},
       ["lab"]),
    _t("wijzig_sessies",
       "Hoeveel sessies er tegelijk in één werker mogen draaien.",
       {"lab": {"type": "string", "description": "De naam van het lab."},
        "aantal": {"type": "integer", "description": "Het nieuwe aantal, 1 tot 10."}},
       ["lab", "aantal"]),
    _t("zet_schedule",
       "Een planning aan- of uitzetten.",
       {"schedule": {"type": "string", "description": "De naam van de planning."},
        "aan": {"type": "boolean", "description": "True = aan, False = uit."}},
       ["schedule", "aan"]),
    _t("voer_schedule_uit",
       "Laat een geplande taak nu meteen draaien.",
       {"schedule": {"type": "string"}}, ["schedule"]),
]

NAMEN = [t["name"] for t in TOOLSCHEMA]
