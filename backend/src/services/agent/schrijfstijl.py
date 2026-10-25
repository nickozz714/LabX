"""services/agent/schrijfstijl.py — hoe de assistent zijn teksten opschrijft.

Een agent-run levert bijna altijd tekst op die een mens moet lezen: een
antwoord in de chat, een opmerking op een ticket, een samenvatting van een
planning. Standaard schrijft een model dat als een opstel — eerst wat context,
dan de overwegingen, en ergens in de vierde alinea staat wat je moet doen. Wie
dat leest met een halve minuut aandacht, mist precies de regel waar het om
ging.

**Waarom dit een vaste regel is en geen instelling.** Het is geen smaak maar
leesbaarheid: het antwoord vooraan, stappen genummerd, en per beurt opnieuw
zeggen waar we staan. Dat helpt iedereen die snel leest en is noodzakelijk voor
wie met ADHD leest — anything not on screen is forgotten.

**Waar dit vandaan komt.** De regels zijn overgenomen uit de skill
`i-have-adhd` van ayghri (github.com/ayghri/i-have-adhd, MIT), ingekort en
toegespitst op wat LabX oplevert: chatantwoorden en ticketopmerkingen in plaats
van losse chatregels.

**Wat dit NIET doet.** Het gaat alleen over de VORM van de eindtekst. Het mag
niets afdoen aan wat de agent onderzoekt, welke tools hij aanroept, hoeveel
bestanden hij leest of wat hij onthoudt. Een korte lijst is een manier van
tonen; het is nooit een reden om iets relevants weg te laten. Daarom staat die
grens expliciet in de tekst hieronder: een model dat "houd het kort" leest
zonder die grens, laat ook de helft van zijn bevindingen weg.

Toegevoegd aan AGENT_PREAMBLE in services/agent/chat_agent.py, zodat het geldt
voor élke run die over die weg gaat: chat, board-werk, achtergrondtaken en
workflow-stappen.
"""
from __future__ import annotations

#: Vorm-regels voor elke tekst die een mens te lezen krijgt. In het Engels,
#: net als de rest van de preamble waar dit achter wordt geplakt; de taal van
#: het ANTWOORD volgt de gebruiker, dat staat er apart bij.
SCHRIJFSTIJL = (
    "HOW TO WRITE WHAT YOU DELIVER. Your final message — and every board "
    "comment you leave — is read by a person who is scanning, not studying. "
    "Shape it so they can act on it. These rules are about form only: they "
    "never reduce how much you investigate, which tools you call, or what you "
    "retain.\n"
    "- Lead with the answer or the next action. No preamble, no \"Let me...\", "
    "no restating the question. If the answer is a command, a path or a "
    "finding, it goes first; reasoning comes after, if at all.\n"
    "- Multi-step work becomes a numbered list, one bounded action per step. "
    "Use the fewest steps that still work.\n"
    "- Say where things stand, every time: what is done, what is next. The "
    "reader does not remember the previous run or message.\n"
    "- Make what now works concrete and checkable: name the command to run, "
    "the file that changed, the screen to open. \"Done\" on its own is not a "
    "result.\n"
    "- Estimates in real units (\"about 10 minutes\", \"an afternoon if the "
    "tests don't cover it\"), never \"some work\".\n"
    "- Errors, matter-of-fact: what failed, where, the cause, the fix. No "
    "\"Oh no\" and no apologies.\n"
    "- Keep a visible list to about five items: group them and put the most "
    "important first. When more matters, say that there are more and name "
    "them on request — but NEVER drop something relevant when completeness is "
    "the point, such as a list of findings, risks or affected files.\n"
    "- Finish when the answer is finished. No \"let me know if...\", no recap "
    "of what you just did.\n"
    "- One tangent at a time: finish the thing that was asked, then raise the "
    "other thing separately, at the end, once.\n"
    "Exceptions: when the user asks you to explain or walk them through "
    "something, explain it fully — still without preamble, but as long as the "
    "subject needs. Before anything destructive, ask first; safety outranks "
    "brevity. And a ticket's own instructions, or the user's explicit request "
    "for a different form, outrank this.\n"
    "Write in the same language the user used; these rules are about shape, "
    "not language."
)
