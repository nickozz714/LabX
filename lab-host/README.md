# Lab-host — een computer in je netwerk labs laten draaien

Eén server die alles doet, krijgt het op zijn staart. Een pc die toch aan
staat kan er labs bij nemen, en dan verdubbel je je capaciteit zonder iets te
kopen. Dit is wat je op díé computer installeert.

---

## Installeren

Op de computer die mee gaat doen, in een **gewoon terminalvenster**:

```bash
curl -fsSL https://raw.githubusercontent.com/nickozz714/LabX/main/lab-host/install.sh \
  | sh -s -- --labx http://jouw-labx:8081
```

Vervang `http://jouw-labx:8081` door het adres waarop je LabX bereikt. Bij
Labs → Machines staat die regel met je eigen adres er al in, en met een
kopieerknop.

Liever zelf kijken wat je draait (verstandig bij een `| sh`):

```bash
curl -fsSL https://raw.githubusercontent.com/nickozz714/LabX/main/lab-host/install.sh -o install.sh
less install.sh
sh install.sh --labx http://jouw-labx:8081
```

Geen internet op die machine? Dan serveert je eigen LabX hetzelfde script uit:

```bash
curl -fsSL http://jouw-labx:8081/api/lab-hosts/agent -o labx_lab_host.py
python3 labx_lab_host.py --labx http://jouw-labx:8081
```

Hij draait daarna als **achtergronddienst** en start bij het inloggen opnieuw.
Een lab-host die je alleen draait zolang er een terminalvenster openstaat, is
geen lab-host.

### Als hij LabX niet bereikt (macOS)

Het installatiescript controleert na het starten óf de agent werkelijk
aankomt, en zegt het als dat niet zo is. Krijg je die melding, dan is het
bijna altijd dit: macOS laat een achtergronddienst niet zomaar op het lokale
netwerk, en de vraag om toestemming komt alleen in beeld bij een programma met
een venster. Een dienst krijgt hem nooit te zien en wordt stil geweigerd.

Hij staat na die eerste poging wél in de lijst:

1. **Systeeminstellingen → Privacy en beveiliging → Lokaal netwerk**
2. Vinkje aan bij **Python** (of `python3`)
3. `launchctl kickstart -k gui/$(id -u)/nl.labx.labhost`

Meekijken met wat hij doet kan altijd: `--voorgrond` draait hem in je venster
in plaats van als dienst.

---

## Daarna: koppelen in LabX

De agent toont een **koppelcode** van zes cijfers.

1. Ga in LabX naar **Labs → Machines**. Die computer staat er nu bij, als
   `nieuw`.
2. Druk op **Koppelen**, vul de code in plus het SSH-adres van die machine
   (`ssh://gebruiker@naam.local`).
3. Plak de **sleutelregel** uit dat venster één keer in een terminal op die
   computer. Daarmee mag LabX er containers starten.
4. Druk op **Verbinding testen**. Groen = klaar.

Een aanmelding is namelijk géén bewijs: iets op je netwerk zégt dat het een
lab-host is. Wie zich meldt komt daarom binnen als `nieuw` en krijgt niets —
geen labs, geen toegang — tot een mens de code bevestigt die op het scherm van
díé machine staat. Zo kan een apparaat zichzelf niet toelaten.

---

## Wat deze agent wél en niet doet

**Wel:**

- **Zich aanmelden.** Docker zendt niets uit — geen mDNS, geen
  service-advertentie. Zonder dit zou je IP-adressen moeten overtypen en zelf
  bijhouden welke pc aan staat.
- **De machine wakker houden** zolang er labs draaien. Een pc die na tien
  minuten in slaap valt is geen lab-host, en dat merk je anders aan een
  nachtrun die halverwege stopt.
- **Melden hoe het ervoor staat**: kernen, geheugen, vrije schijf, load,
  Docker-versie.
- **Docker vindbaar maken voor SSH.** Een SSH-sessie op macOS krijgt een kaal
  PATH (`/usr/bin:/bin:/usr/sbin:/sbin`), en Docker Desktop staat in
  `/usr/local/bin`. De agent vult daarvoor één herkenbaar blok aan in
  `~/.zshenv`.

**Niet:** commando's uitvoeren. Niet één.

Het aansturen van containers gaat volledig buiten deze agent om, via
`DOCKER_HOST=ssh://jij@dezepc` — de docker-CLI op de LabX-server praat
rechtstreeks met de Docker hier, over SSH. Geen open poort, geen eigen
protocol, en de authenticatie is je SSH-sleutel.

Dat is met opzet zo gesplitst: **wie deze agent overneemt, kan niets starten.**
De gevaarlijke kant zit achter een sleutel die jij beheert, niet achter een
dienstje dat op je netwerk staat te luisteren.

---

## Eisen aan die machine

| Wat | Waarom |
|---|---|
| **Docker** draait | De labs zijn containers; zonder Docker valt er niets te hosten. |
| **Externe login (SSH) aan** | Hierlangs stuurt LabX de containers aan. macOS: Systeeminstellingen → Algemeen → Delen → Externe login. |
| **Python 3.9 of nieuwer** | De agent zelf. Zit op macOS en de meeste Linux-machines al. |
| **Genoeg schijfruimte** | Reken op minstens 20 GB vrij; een image ophalen loopt anders stuk. LabX waarschuwt onder de 15 GB. |
| **Blijft wakker** | De agent regelt dat zelf zolang er labs draaien (`caffeinate` / `systemd-inhibit`), maar hij kan geen pc wekken die al uit staat. |

---

## Beheren

| | macOS (launchd) | Linux (systemd) |
|---|---|---|
| **Logboek** | `~/labx-lab-host/agent.log` | `journalctl --user -u labx-labhost -f` |
| **Herstarten** | `launchctl kickstart -k gui/$(id -u)/nl.labx.labhost` | `systemctl --user restart labx-labhost` |
| **Stoppen** | `launchctl bootout gui/$(id -u)/nl.labx.labhost` | `systemctl --user stop labx-labhost` |

Stoppen laat draaiende labs gewoon staan; LabX ziet die machine daarna als
offline.

---

## Wat er op die machine komt te staan

| Bestand | Waarvoor |
|---|---|
| `~/labx-lab-host/labx_lab_host.py` | de agent |
| `~/.labx-lab-host-id` | zijn identiteit tussen aanmeldingen door — blijft gelijk als het IP verandert, zodat hij geen tweede rij in de lijst wordt |
| `~/.zshenv` | één gemarkeerd blok dat docker in het PATH van een SSH-sessie zet (alleen macOS) |
| `~/Library/LaunchAgents/nl.labx.labhost.plist` | alleen met `--autostart` |

Alles weghalen = die vier verwijderen, en de machine in LabX bij
**Labs → Machines** verwijderen.
