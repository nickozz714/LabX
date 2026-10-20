# models/cluster.py
#
# Een development cluster: vaste diensten die blijven staan, en branch-labs
# die komen en gaan.
#
# Waarom dit een eigen ding is en geen slim lab: de vaste diensten horen bij
# GEEN branch. De Cosmos-emulator moet blijven draaien terwijl je drie
# branch-labs weggooit en vier nieuwe maakt. Dat is precies het verschil
# tussen een cluster en een lab, en de reden dat het hier apart staat.
#
# Wat het NIET is: een tweede soort lab. Een branch-lab blijft een gewoon
# LabX-lab met een branch en een cluster erbij, zodat werkers, data-guard,
# geheimen en verplaatsen op één plek geregeld blijven.
from __future__ import annotations

from sqlalchemy import Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base

#: `nieuw`     — bestaat, nog niets gestart
#: `draait`    — de vaste diensten staan
#: `fout`      — een vaste dienst kwam niet op; staat erbij waarom
CLUSTER_STATUSSEN = ("nieuw", "draait", "fout")


class Cluster(Base):
    __tablename__ = "clusters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    naam: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    #: Op welke machine het hele cluster draait. NULL = deze server. Eén
    #: cluster hoort bij één machine: de vaste diensten en de branch-labs
    #: moeten elkaar over een lokaal netwerk kunnen vinden, en dat kan niet
    #: als ze verspreid staan.
    host_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    #: Het docker-netwerk waarop alles van dit cluster zit. Afgeleid van de
    #: naam, maar opgeslagen zodat hernoemen het netwerk niet kwijtraakt.
    netwerk: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="nieuw")
    fout: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # ── wat een branch-lab nodig heeft (stap 2) ──────────────────────────────
    #
    # Deze staan er nu al omdat ze bij het CLUSTER horen en niet bij een lab:
    # je schrijft één keer op waar de repo staat en hoe de applicatie start,
    # en elk branch-lab gebruikt dat. Een tweede migratie ervoor zou geen
    # ander model opleveren.
    repo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: De naam van het geheim in de kluis waar het toegangstoken staat. NIET
    #: het token zelf: dat hoort in de kluis, met alles wat daar al voor
    #: geregeld is (versleuteling, maskeren in uitvoer, vervaltijd).
    repo_token_geheim: Mapped[str | None] = mapped_column(String(120), nullable=True)
    #: De publieke helft van de deploy key van dit cluster. Geen geheim --
    #: die hoort juist gekopieerd te worden, naar Settings → Deploy keys van
    #: de repo.
    ssh_publiek: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: De privéhelft, Fernet-versleuteld. Staat hier en niet in de kluis van
    #: het cluster: die kluis is voor geheimen van de APPLICATIE, en de lijst
    #: "wat moet ik nog invullen" wordt onleesbaar als er infrastructuur
    #: tussen staat die LabX zelf beheert.
    ssh_prive_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Het image waarin een branch-lab draait. IntelligentHive heeft Node en
    #: yarn 4 nodig; het standaard python-image volstaat daar niet.
    lab_image: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Hoe ruim een branch-lab bemeten is. Een lab is standaard 1 CPU en
    #: 2 GB -- genoeg om in rond te kijken, te krap om een monorepo in te
    #: bouwen. `yarn build:dependencies` over twaalf packages loopt daarop
    #: vast of duurt eindeloos, en dat ziet eruit als een hangend lab.
    lab_cpu: Mapped[float | None] = mapped_column(Integer, nullable=True)
    lab_mem_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Het Azure-profiel dat elk branch-lab meekrijgt.
    #:
    #: Nodig zodra de applicatie zelf bij Azure moet kunnen -- en dat is
    #: precies wat `geheimen_zelf` betekent: het project haalt zijn
    #: instellingen uit een vault, maar daarvoor moet er iemand aangemeld
    #: zijn. In het lab kan niemand `az login` doen; de sessie komt van dit
    #: profiel. Zonder dat valt de API om op "Missing API_KEY" terwijl de
    #: front-end gewoon draait, en dat ziet eruit als een half geslaagd lab.
    azure_profiel_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Het model waarop ELK lab van dit cluster draait, en hoeveel beurten een
    #: agent-run daar mag doen.
    #:
    #: Allebei per CLUSTER en niet per lab, omdat het bij het WERK hoort en
    #: niet bij een wegwerpcontainer: zet je Opus op dit cluster, dan hoort
    #: elke branch eronder op Opus te draaien zonder dat je dat per lab
    #: herhaalt. Anders is één vergeten lab genoeg om een run op een ander
    #: model te laten lopen dan je dacht.
    #:
    #: `lab_max_turns` LEEG betekent in een cluster: GEEN beurtenplafond.
    #: Niet "volg de instellingen" -- dat is het verschil met een gewoon lab.
    #:
    #: Een beurt is geen maat voor werk. Gemeten op dit cluster liep een
    #: ticket stuk op `error_max_turns` bij 250, waarvan honderd stappen
    #: opgingen aan het vínden van de code in een monorepo. Het getal
    #: verhogen verplaatst dat alleen: bij 600 valt hetzelfde ticket om op
    #: een groter bestand. Er staan twee betere vangnetten omheen -- de
    #: tijdslimiet van de run en het kostenplafond -- die meten wat je
    #: werkelijk wilt begrenzen. Vul je hier wél een getal in, dan is dat
    #: een harde grens.
    lab_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lab_max_turns: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Krijgt elk branch-lab de data-egress-guard aan, en het lokale model
    #: als tweede controle?
    #:
    #: Standaard allebei AAN -- dat hoort de veilige kant te zijn. Maar een
    #: cluster waarvan de repo's geen klantdata bevatten betaalt daar elke
    #: tool-aanroep voor, en dan is het een rem zonder dat er iets te remmen
    #: valt. Die keuze hoort bij het CLUSTER en niet per lab: je weet het van
    #: de repo, en een branch-lab erft hem dus in plaats van dat je het bij
    #: elk nieuw lab opnieuw uitzet (en één keer vergeet).
    #:
    #: Dit zet hem alleen voor NIEUWE branch-labs. Bestaande labs houden wat
    #: ze hebben; dat staat per lab in de instellingen en werkt daar direct,
    #: zonder herstart.
    lab_data_guard: Mapped[bool] = mapped_column(Integer, nullable=False, default=1)
    lab_llm_guard: Mapped[bool] = mapped_column(Integer, nullable=False, default=1)
    #: Haalt deze applicatie haar geheimen zelf op uit een vault?
    #:
    #: Dan is "deze tien sleutels ontbreken in de kluis" een verkeerde
    #: conclusie. Lokaal start het project gewoon: een script trekt de
    #: waarden eruit en zet ze klaar. Wat LabX moet leveren is niet de
    #: WAARDE maar het GEREEDSCHAP -- de CLI en een aanmelding.
    geheimen_zelf: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    #: Heeft deze applicatie de Azure CLI nodig? Standaard NIET.
    #:
    #: Deze installatie zet hem in elk lab, want ze is voor Fabric-werk
    #: gebouwd. Een branch-lab is iets anders: daar kost het 616 MB en vier
    #: minuten voor gereedschap dat een node-monorepo nooit aanroept.
    lab_azure: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    #: Bouwen en starten, één keer opgeschreven voor het hele cluster.
    start_script: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Welk env-profiel de front-end gebruikt (debug, realestate, ...). Een
    #: instelling, want die keuze hoort niet in een script vastgeroest.
    env_profiel: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Omgevingsvariabelen die élk branch-lab krijgt, bovenop zijn eigen.
    #: Hier staat bijvoorbeeld COSMOS_ENDPOINT, zodat elke branch dezelfde
    #: vaste dienst vindt zonder dat iemand dat per lab overtypt.
    omgeving: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    #: Welke geheimen er in de kluis moeten staan voordat een branch-lab kan
    #: draaien: {naam-in-de-kluis: waarvoor}. Komt uit de tweede lijst van de
    #: verkenner -- diensten waar geen emulator van bestaat. Hier staat de
    #: NAAM, nooit de waarde.
    benodigde_geheimen: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    #: Op welke poorten de applicatie IN een branch-lab luistert. LabX
    #: publiceert deze naar buiten. Leeg = de oude standaard (3000, 7071), en
    #: dat is precies de gok die een lab onbereikbaar maakt terwijl het draait.
    lab_poorten: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    #: Waar dit project env-bestanden verwacht, als lijst van paden in de repo.
    #: Komt uit de verkenning: de detector leidt `x/.env` af uit `x/.env.example`,
    #: en het model vult aan wat gitignored is maar wel in de documentatie staat
    #: (`components/app/.env.local`). De INHOUD staat in cluster_env_files; dit
    #: zijn alleen de plekken, zodat het scherm kan tonen wat nog ontbreekt.
    env_paden: Mapped[list | None] = mapped_column(JSON, nullable=True)

    #: Extra pakketten die elk branch-lab erbij krijgt, als lijst van sleutels
    #: uit de extra-catalogus (zie services/lab/extras_catalog.py). Git en het
    #: basisgereedschap zitten al in `_BASE_STEPS` en horen hier NIET in: die
    #: komen in elk lab, ongeacht het image.
    #:
    #: Twee bronnen, allebei nodig: jij vinkt ze aan, en de verkenner mag ze
    #: voorstellen op grond van wat er in de repo staat. Een voorstel komt
    #: binnen als onbevestigd, zoals elke vondst.
    lab_extras: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # ── de proefbouw ─────────────────────────────────────────────────────────
    #
    # Een plan dat er goed uitziet maar waarvan het script omvalt, heeft zijn
    # werk niet af. Daarom bouwt LabX het één keer echt, op een proefbranch,
    # en stelt het script bij op grond van wat er werkelijk misgaat.
    #
    # En "af" is niet "exitcode 0": af is wanneer JIJ zegt dat de branch goed
    # is. Daarom blijft het proeflab staan, bereikbaar en bruikbaar, tot je
    # het bevestigt.
    #: `geen` · `bezig` · `wacht_op_fiat` · `goedgekeurd` · `mislukt`
    proef_status: Mapped[str] = mapped_column(String(20), nullable=False, default="geen")
    #: Het lab waarin geprobeerd is. Blijft bestaan na goedkeuring -- het IS
    #: een branch-lab, er is niets bijzonders aan behalve hoe het ontstond.
    proef_lab_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Elke ronde: wat er draaide, wat er misging, en wat het model veranderde.
    #: Zodat je kunt meelezen in plaats van af te wachten.
    proef_log: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    notitie: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)


#: `gestopt` — bestaat als rij, draait niet
#: `ophalen` — het image staat er nog niet en wordt binnengehaald. Dit is een
#:             eigen stand omdat het MINUTEN duurt: de Cosmos-emulator is 1,7
#:             GB, en zonder deze stand kijk je naar een knop die hangt zonder
#:             een woord. Eén keer per image per machine.
#: `draait`  — container is op. Let op: dat is iets ANDERS dan "klaar". Een
#:             emulator die net begonnen is antwoordt nog niet; wat daarvan
#:             waar is, meet `dienst_gezondheid`.
#: `fout`    — gestart maar gevallen; `fout` zegt waarom
DIENST_STATUSSEN = ("gestopt", "ophalen", "draait", "fout")


class ClusterDienst(Base):
    """Een vaste container in een cluster — de Cosmos-emulator bijvoorbeeld.

    Hij hoort bij het cluster en niet bij een branch, en blijft dus staan als
    branch-labs verdwijnen. Opnieuw opbouwen vanaf een andere branch is
    gewoon: image wijzigen en herstarten.
    """

    __tablename__ = "cluster_diensten"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cluster_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    #: Ook de DNS-naam op het clusternetwerk. Een branch-lab praat dus tegen
    #: `ih-cosmos:8081` en hoeft geen adres of poort te kennen.
    naam: Mapped[str] = mapped_column(String(120), nullable=False)
    image: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Poorten die naar BUITEN gepubliceerd worden, op de loopback van de
    #: clustermachine. Voor onderling verkeer is dit niet nodig -- dat gaat
    #: over het netwerk, op naam.
    #: Welke poorten IN de container naar buiten moeten. Welk nummer dat op
    #: de machine wordt, bepaalt LabX (zie `poort_map`) -- anders botst de
    #: emulator op 8081 met het eerste het beste dat daar al luistert, en dat
    #: is precies wat er bij de eerste proef gebeurde.
    poorten: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    #: {containerpoort: machinepoort}, toegekend bij het starten en bewaard,
    #: zodat het adres niet verandert zolang de dienst bestaat.
    poort_map: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    omgeving: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    container_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="gestopt")
    fout: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)


#: `voorstel`     — verkend, wacht op jou
#: `goedgekeurd`  — omgezet in een cluster; vanaf nu bevroren
#: `verworpen`    — je wilde het niet; blijft staan om te kunnen vergelijken
PLAN_STATUSSEN = ("voorstel", "goedgekeurd", "verworpen")


class ClusterPlan(Base):
    """Wat de verkenner van een repo maakte, en wat jij daarvan overnam.

    **Waarom dit bewaard wordt en niet alleen toegepast.** Het model is
    leidend in de verkenning, en dat betekent dat twee verkenningen van
    dezelfde repo kunnen verschillen. Die prijs wordt hier afgekocht: een
    goedgekeurd plan is de configuratie van het cluster en wordt niet meer
    stilletjes overschreven. Opnieuw verkennen is iets dat JIJ doet, en het
    resultaat komt terug als een VERSCHIL met dit plan — niet als vervanging.

    `cluster_id` mag leeg zijn: je kunt een repo verkennen vóórdat er een
    cluster bestaat. Dat is zelfs de normale volgorde, want het plan is juist
    wat bepaalt hoe het cluster eruit gaat zien.
    """

    __tablename__ = "cluster_plannen"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cluster_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    repo_url: Mapped[str] = mapped_column(String(500), nullable=False)
    branch: Mapped[str] = mapped_column(String(200), nullable=False)
    #: De commit waarop verkend is. Hieraan hangt de hele uitkomst: hetzelfde
    #: plan opnieuw openen draait niets opnieuw.
    commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="voorstel")
    #: De hele uitkomst van de verkenning: vondsten met bewijs, startscript,
    #: samenvatting, openstaande vragen.
    plan: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    #: Wat jij aangevinkt hebt bij het goedkeuren. Apart van `plan`, want het
    #: verschil tussen "voorgesteld" en "overgenomen" is precies wat je later
    #: wilt kunnen teruglezen.
    keuzes: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    #: De naam van het geheim waarmee gekloond is -- nooit de waarde. Staat
    #: hier omdat een plan ook ZONDER cluster kan bestaan (dat is zelfs de
    #: normale volgorde), en dan is er later niets meer om op terug te vallen:
    #: opnieuw verkennen strandde dan op een privérepo zonder token.
    token_geheim: Mapped[str | None] = mapped_column(String(120), nullable=True)
    #: Wat JIJ geantwoord hebt op de vragen van de vorige verkenning:
    #: {vraag: antwoord}. Ze gaan mee naar het model en worden doorgegeven aan
    #: elk volgend plan, zodat je niet twee keer hetzelfde hoeft te typen.
    antwoorden: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    #: Wat jij VOORAF meegaf: "de agent hoort er niet bij", "gebruik node 22".
    #: Anders dan `opmerking`, dat gaat over een plan dat er al ligt. Deze
    #: staat er vóór de eerste regel gelezen is, en scheelt dus een hele ronde.
    instructie: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Env-bestanden die JIJ meegaf bij het verkennen, versleuteld:
    #: [{"pad": ..., "inhoud": <versleuteld>, "sleutels": [...]}].
    #:
    #: Ze staan hier en niet bij het cluster omdat een plan ook ZONDER cluster
    #: bestaat -- dat is zelfs de normale volgorde. Wie zijn .env pas na
    #: goedkeuring kan aanleveren, laat het model raden naar sleutels die hij
    #: had kunnen weten: het startscript verzint dan een naam waar jij er al
    #: een had. Bij goedkeuren verhuizen ze naar cluster_env_files.
    #:
    #: Naar het model gaan alleen de NAMEN. De waarden gaan een kant op: naar
    #: de werkkopie in een lab.
    env_bestanden: Mapped[list | None] = mapped_column(JSON, nullable=True)
    #: Extra pakketten die JIJ vooraf koos. Het model mag er ook voorstellen;
    #: bij goedkeuren worden de twee samengevoegd. Vooraf kunnen kiezen
    #: scheelt een ronde: anders keur je een plan goed, zie je dat node
    #: ontbreekt, en mag je opnieuw.
    lab_extras: Mapped[list | None] = mapped_column(JSON, nullable=True)
    #: Wat je van afzonderlijke ONDERDELEN vond: {"dienst::mailpit": "deze
    #: hoort er niet bij"}. Antwoorden kon al, maar alleen op vragen die het
    #: model toevallig stelde -- en een plan zonder vragen liet je dus met
    #: niets. Terugkoppeling hoort niet af te hangen van wat het model wilde
    #: weten.
    opmerkingen: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    #: Wat je van het plan als geheel vond.
    opmerking: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Het plan waar dit uit voortkwam. Een antwoord VERVANGT een plan niet --
    #: het levert er een naast op, zodat je ziet wat je antwoord veranderde.
    vorig_plan_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_gebruikt: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    fout: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)
