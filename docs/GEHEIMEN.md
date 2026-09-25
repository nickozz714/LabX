# Geheimen

## De afspraak, in één zin

Een `{{secret:naam}}` wordt **alleen ingevuld op weg naar buiten** — een
tool-aanroep, een commando, een HTTP-verzoek — en **nooit op weg naar het
model**. Het model mag geheimen dus *kiezen*, niet *kennen*.

Dat is geen implementatiedetail maar de hele reden dat dit veilig is. In een
prompt, een skill-instructie of de opdracht van een workflow-activiteit blijft
de verwijzing staan zoals hij is; het model ziet de naam en nooit de waarde.

## Twee kluizen, dezelfde schrijfwijze

| | Waar | Waarvoor |
| --- | --- | --- |
| **Kluis** (Instellingen → Geheimen) | overal in LabX, of beperkt tot bepaalde labs | een webhook-URL, een API-sleutel, een wachtwoord |
| **Lab-geheim** (bij het lab zelf) | dat ene lab | iets dat in die container ontstaat, zoals een token dat elk uur ververst wordt |

Ze gebruiken dezelfde vorm: `{{secret:naam}}`. Bestaat een naam in allebei, dan
wint het lab-geheim — dat is de specifiekere afspraak.

## Waar je ze kunt gebruiken

- **Argumenten van elke tool.** Het model schrijft `{{secret:teams-webhook}}`
  in bijvoorbeeld een `url`-veld; LabX vult de waarde in vlak voor de aanroep.
- **Shell-commando's in een lab.** Daar gaat de waarde niet eens in het
  commando: hij komt in een bestand in de container (mode 600) en het commando
  leest een omgevingsvariabele. Zo staat hij ook niet in `ps` op de host.
- **Workflow-stappen, skills, tool-configuratie** — overal waar tekst
  uiteindelijk naar buiten gaat.

## Wat er gebeurt als iets misgaat

- **Onbekende naam** → de aanroep gaat *niet* door. Het model krijgt te horen
  welke geheimen er wél zijn. Zou LabX de verwijzing laten staan, dan vertrekt
  `{{secret:typefout}}` letterlijk naar een externe dienst — die hem niet
  begrijpt, of hem opslaat.
- **Een waarde die terugkomt** (een dienst die je sleutel terugspiegelt in een
  foutmelding, een tool die zijn eigen aanroep echoot) wordt gemaskeerd naar
  `{{secret:naam}}`. Korte waarden niet: dan raak je willekeurige tekst.
- **Een geheim dat bij een klant hoort** kun je beperken tot bepaalde labs. Een
  sleutel van de ene klant werkt dan niet in het lab van een andere — ook niet
  buiten een lab om, want dan is er niets om aan te toetsen.

## Wat er vastgelegd wordt

Dat er een geheim is ingevuld, bij welke tool, en met welke **naam**. Nooit de
waarde — anders staat hij alsnog in het spoor dat juist bedoeld is om achteraf
te kunnen kijken. Per geheim zie je hoe vaak en wanneer hij voor het laatst is
gebruikt; dat is het antwoord op "wordt dit ding eigenlijk nog gebruikt"
voordat je hem weggooit.

## Wat je niet kunt

Een waarde terugzien. Er is geen endpoint en geen knop die hem toont, ook niet
voor jou. Wel een **controle**-knop die zegt hoe lang hij is en met welke twee
tekens hij begint — genoeg om te zien of je de juiste hebt geplakt, te weinig
om hem te gebruiken. Wat je niet kunt tonen, kun je ook niet per ongeluk in een
prompt plakken.
