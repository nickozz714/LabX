# De managed identity van een klant gebruiken

LabX draait niet in Azure. Toch kan een lab werken **als de user-assigned
managed identity van de klant** — zonder secret, zonder certificaat en zonder
iets van ons dat in Azure draait.

## Waarom dat normaal niet kan, en hier wel

Een managed identity hangt aan een Azure-resource. Zijn token komt van het
metadata-endpoint (`169.254.169.254`) *in* die resource, en er is geen sleutel
waarmee je van buitenaf "als de UAMI" kunt inloggen — dat is de bedoeling ervan.

**Workload identity federation** draait dat om. Op de UAMI registreer je een
*federated credential* met een **issuer** en een **subject**. Wie een getekend
token van die issuer kan tonen met dat subject, krijgt van Entra een access
token als die identiteit.

LabX wordt dus zelf die issuer. Niet als draaiende dienst — alleen **twee
statische bestanden** op een publiek adres:

```
https://jouwdomein.nl/oidc/.well-known/openid-configuration
https://jouwdomein.nl/oidc/jwks.json
```

De privésleutel blijft in LabX, versleuteld. Bij elke tokenaanvraag tekent LabX
een verse JWT (RS256, tien minuten geldig, met `kid` in de kop) en wisselt die
bij Entra in voor een token.

Elke actie in Fabric staat daarmee op naam van de UAMI van díé klant — niet op
een gedeeld serviceaccount en niet op iemands persoonlijke inlog.

## Eenmalig: het profiel en de bestanden

1. **Azure-profielen → Nieuw → "Managed identity van de klant (federatie)"**.
   Invullen: tenant-id van de klant, **client**-id van de managed identity, je
   issuer-URL en een subject (bijvoorbeeld `labx-swinkels`).

   > De **client**-id gebruik je voor de tokenaanvraag. Het **object/principal**-id
   > is een ander getal en heb je nodig voor groepen en workspace-rollen. Ze
   > verwisselen geeft `AADSTS700016`, waarin niets over de oorzaak staat.

2. LabX maakt het sleutelpaar. Open **Publiceren en registreren bij de klant**:
   daar staan de twee bestanden met een kopieerknop.

3. **Publiceer ze** op je domein, statisch, met `Content-Type: application/json`.

   ```nginx
   location /oidc/ {
       alias /var/www/oidc/;
       default_type application/json;
       # Zonder deze regel blokkeert een gangbare "verberg dotfiles"-regel
       # juist .well-known — en de foutmelding van Entra zegt daar niets over.
       location ~ ^/oidc/\.well-known/ { alias /var/www/oidc/.well-known/; }
   }
   ```

   Controleren:

   ```bash
   curl -sI https://jouwdomein.nl/oidc/.well-known/openid-configuration | head -3
   curl -s  https://jouwdomein.nl/oidc/jwks.json | jq '.keys[] | {kid, alg, use}'
   ```

   Verwacht: HTTP 200, `content-type: application/json`, en een sleutel met
   `"alg": "RS256"` en `"use": "sig"`.

## Eenmalig per klant: in hun tenant

4. **Federated credential op de UAMI.** Portal → de managed identity →
   *Federated credentials* → *Add* → scenario **Other issuer**. Vul de drie
   waarden in die LabX toont — letterlijk, één teken verschil is genoeg om het
   te laten mislukken.

   Of met de CLI:

   ```bash
   az identity federated-credential create \
     --name labx --identity-name <uami-naam> --resource-group <rg> \
     --issuer 'https://jouwdomein.nl/oidc' \
     --subject 'labx-swinkels' \
     --audiences 'api://AzureADTokenExchange'
   ```

   Een UAMI draagt er maximaal 20.

5. **Fabric toelaten.** Fabric admin portal → *Tenant settings* → de instelling
   waarmee service principals de Fabric-API's mogen gebruiken, beperkt tot een
   security group. Maak zo'n groep, zet de managed identity erin (met het
   **object**-id) en wijs de groep aan. Deze instelling wisselt wel eens van
   naam; doe hem via de portal en controleer wat er staat.

6. **Workspace-rol.** Workspace → *Manage access* → de identiteit toevoegen met
   de rol die past. Via de API:

   ```bash
   curl -X POST https://api.fabric.microsoft.com/v1/workspaces/<id>/roleAssignments \
     -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
     -d '{"principal":{"id":"<object-id>","type":"ServicePrincipal"},"role":"Contributor"}'
   ```

## Gebruiken

**Controleren** op het profiel wisselt een verse assertie in en laat zien *wie*
je dan blijkt te zijn — het `oid` van de managed identity. Dat is de vraag die
je hier stelt: staat straks bij de klant de juiste naam op de actie?

**Naar lab** zet het token als geheim in dat lab. De agent gebruikt het met
`{{secret:<profielnaam>}}` en krijgt de waarde nooit te zien: LabX vult hem in
vlak vóór het commando, via een bestand dat alleen root in die container leest.
Het token staat daarmee op geen enkele commandoregel, niet in `ps` op de host,
niet in de prompt en niet in het audit-spoor.

Een token leeft een uur. LabX vervangt het **tien minuten voor het verloopt**,
elke vijf minuten nagelopen op de achtergrond.

## Als het niet werkt

| Wat je ziet | Wat het betekent |
| --- | --- |
| `AADSTS70021` | Geen federated credential die bij deze issuer én dit subject past. Controleer beide letterlijk — let op een schuine streep aan het eind. |
| `AADSTS700211` | Entra kent de issuer niet als vertrouwde uitgever. Staat de credential op de juiste identity? |
| `AADSTS700024` | De assertie is verlopen of nog niet geldig. Meestal loopt de klok van de server scheef. |
| `AADSTS700027` | De handtekening klopt niet. Entra heeft een andere publieke sleutel opgehaald — is de nieuwe `jwks.json` al gepubliceerd, en staat de gebruikte `kid` erin? |
| `AADSTS700016` | Deze client-id bestaat niet in die tenant. Vaak het object-id in plaats van het client-id. |
| Entra kan de sleutels niet ophalen | De bestanden staan niet publiek, geven geen `application/json`, of `.well-known` wordt geblokkeerd. |
| Fabric geeft 401 | Het token is voor de verkeerde scope, of verlopen. |
| Fabric geeft 403 | Het token klopt, maar de identiteit mag niet bij die workspace — of de tenant-instelling voor service principals staat uit. |

LabX zet bij elke `AADSTS`-code de uitleg erbij en geeft de melding van Entra
onverkort door: elke code heeft een andere oorzaak die je zelf moet oplossen, en
"token ophalen mislukt" helpt je bij geen enkele.

**Propagatie** duurt even. Een net aangemaakte federated credential of
roltoewijzing is niet meteen overal bekend; wacht een paar minuten voor je gaat
zoeken.

## Sleutel roteren

1. **Sleutel roteren** op het profiel. De nieuwe komt in de `jwks.json` te staan
   **naast** de oude, en LabX tekent er meteen mee.
2. Publiceer de nieuwe `jwks.json`.
3. Controleer dat het weer werkt.
4. **Oude sleutels opruimen.**

Die volgorde is de hele truc: Entra haalt de sleutels op wanneer het hém uitkomt
en cachet ze. Zou de oude sleutel meteen verdwijnen, dan is er een venster waarin
Entra nog met de oude rekent terwijl wij al met de nieuwe tekenen — en dat levert
`AADSTS700027` op bij iets wat gisteren werkte.

## De prijs

De privésleutel in LabX ís de identiteit, bij elke klant waar hij als federated
credential staat. Hij verlaat de server niet en staat versleuteld opgeslagen,
maar wie hem heeft, is de UAMI van al die klanten.

- Gebruik **per klant een eigen subject**; dan is in het auditspoor te zien welke
  koppeling er aan de knoppen zat, en kun je er één intrekken zonder de rest.
- Roteer bij twijfel. Dat kan zonder onderbreking.
