# Sykkel til jobben

`ha_commutes.py` registrerer sykkelturer mellom hjem og jobb fra Home Assistant.
Scriptet kombinerer aktivitetshistorikk med sonehistorikk og lagrer én CSV-fil
per år. Det kan kjøres manuelt eller som en Docker-container via cron.

Repoet inneholder også en eldre [AirTag-basert app](#eldre-airtag-basert-app)
som ikke fungerer i dagens oppsett. Den bruker ikke Home Assistant-scriptets
data eller konfigurasjon.

## Krav

- Home Assistant med historikk for både aktivitets- og posisjonsentiteten i
  `recorder`, inkludert hjem- og jobbsone.
- En aktivitetsentitet som rapporterer `Cycling`, og en posisjonsentitet som
  rapporterer soner.
- Et Home Assistant-token med tilgang til historikk.
- Docker for containeroppsett, eller Python 3.10+ på macOS/Linux for lokal kjøring.

## Konfigurasjon

Opprett `.env` i samme mappe som `ha_commutes.py`:

```dotenv
HA_URL=http://homeassistant.localdomain:8123
HA_TOKEN=sett_tokenet_ditt_her
ZONE_WORK_NAME="Å Energi"
ACTIVITY_ENTITY=sensor.erlings_iphone_3_activity
TRACKER_ENTITY=device_tracker.erlings_iphone_3
```

Bytt ut verdiene med dine egne. `HA_TOKEN` er påkrevd; øvrige verdier har
standardverdiene vist over. `ZONE_WORK_NAME` må være navnet på jobbsonen, mens
hjemmesonen må hete `home`. URL-en må kunne nås fra der scriptet kjører.
Inne i en container viser `localhost` til containeren, ikke til vertsmaskinen.

`.env` inneholder et hemmelig token. Ikke legg filen i Git eller containerimaget;
den er ignorert av begge. Beskytt filen med `chmod 600 .env`. Miljøvariabler
overstyrer filen. Verdier leses bokstavelig, uten `${...}`-utvidelser.

## Docker

Kopier `ha_commutes.py`, `Dockerfile`, `.dockerignore` og `.env` til samme
mappe på maskinen med Docker. Opprett `data/` og kopier eventuelle eksisterende
årsfiler dit. Flytt også en eksisterende `ha-commutes.state.json` dit hvis
kjørestaten skal videreføres.

Bygg og kjør fra denne mappen:

```sh
chmod 600 .env
mkdir -p data
docker build -t sykkeldager . && docker run --rm \
  --user "$(id -u):$(id -g)" \
  --mount "type=bind,src=$(pwd)/.env,dst=/app/.env,readonly" \
  --mount "type=bind,src=$(pwd)/data,dst=/app/data" \
  sykkeldager
```

`.env` monteres skrivebeskyttet. CSV-filer, kjørestat og lås lagres i `data/`
på verten og bevares når containeren fjernes. Brukeren må kunne lese `.env`
og skrive til `data/`. Scriptet oppretter CSV- og state-filer med tilgang
kun for brukeren som kjører containeren.

### Daglig kjøring med cron

Kjør først kommandoen over manuelt. Legg deretter til denne linjen med
`crontab -e` for kjøring kl. 23:00 etter vertsmaskinens lokale tid:

```cron
0 23 * * * /usr/bin/docker run --rm --user 1000:1000 --mount type=bind,src=/path/to/sykle_til_jobben/.env,dst=/app/.env,readonly --mount type=bind,src=/path/to/sykle_til_jobben/data,dst=/app/data sykkeldager >> /path/to/sykle_til_jobben/cron.log 2>&1
```

Erstatt `/path/to/sykle_til_jobben` med prosjektmappens absolutte bane,
`/usr/bin/docker` med resultatet av `command -v docker`, og `1000:1000`
med brukerens UID/GID fra `id -u` og `id -g`. Banene i eksemplet må ikke
inneholde mellomrom. Bruk en bruker som har tilgang til Docker.
Bygg imaget på nytt når scriptet eller Dockerfile endres; cron trenger ikke
bygge ved hver kjøring. Se `cron.log` ved feil. Cron tar ikke automatisk igjen
en kjøring som ble hoppet over fordi maskinen var avslått.

## Lokal kjøring uten Docker

```sh
python3 -m venv .venv
.venv/bin/python -m pip install 'python-dotenv>=1.0,<2'
.venv/bin/python ha_commutes.py
```

Scriptet leser `.env` ved siden av scriptet uavhengig av arbeidsmappen.
En annen konfigurasjonsfil kan velges med `--config /path/to/config.env`;
en eksplisitt angitt fil må eksistere. Hvis standardfilen mangler, kan
miljøvariabler brukes i stedet.

Uten Docker lagres årsfilene i `data/` ved siden av scriptet, mens
`ha-commutes.state.json` lagres ved siden av scriptet. `--state` velger en
annen kjørestat. Med `--csv /path/to/commutes.csv` lagres alle år i én
egendefinert fil; uten `--state` brukes da samme bane med endelsen
`.state.json`.

## Registrering og lagring

`data/YYYY-sykkeldager.csv` inneholder dato, avreisetid hjemmefra, ankomst jobb,
avreise jobb og ankomst hjem. Dato og årsfil følger avreisedatoen i
`Europe/Oslo`, også når ankomst er etter midnatt.

En tur krever avgang fra én av sonene, ankomst i den andre og minst én
overlappende `Cycling`-periode. Mellomliggende soner er tillatt, men
`unknown` og `unavailable` bryter turen. Tidene er omtrentlige GPS-sonetider,
ikke nødvendigvis tidspunktet syklingen startet eller sluttet.

Kun turer med avreise mandag–fredag telles. Retningene telles separat; felt
for en retning som ikke er syklet står tomme. Ved flere turer i samme retning
samme dag beholdes den første.

Første kjøring, manglende CSV eller en kjørestat eldre enn 30 dager gir søk
i de siste 30 dagene. Ellers hentes historikk fra dagen før forrige kjøring,
eller tidligere ved en uferdig tur, begrenset til 30 dager. Eksisterende
CSV-rader beholdes og overlapp hindrer duplikater. Historikk som Home Assistant
har slettet kan ikke rekonstrueres. Tom historikk eller API-feil avbryter uten
å oppdatere kjørestaten. CSV og kjørestat skrives atomisk, og en lokal lås
hindrer samtidige kjøringer fra å overskrive hverandre.

## Tester

Installer prosjektets testavhengigheter og kjør testene:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest
```

Testene bruker falske klienter og trenger ikke Apple-innlogging eller nettverk.

## Eldre AirTag-basert app

**AirTag-løsningen fungerer ikke i dagens oppsett** på grunn av Apples endrede
sikkerhetsmodell for iCloud-nøkkelringen. FindMy.py trenger en enhetsnøkkel
for å lese AirTag-posisjoner, men på macOS 26 er nøkkelen som trengs for å
hente denne beskyttet. [FindMy.py-dokumentasjonen](https://docs.mikealmel.ooo/FindMy.py/getstarted/02-fetching.html)
beskriver støtte og begrensninger for ulike macOS-versjoner; eldre systemer
eller allerede tilgjengelige nøkler kan ha andre muligheter. Bruk
Home Assistant-løsningen over i stedet.

`bike-tracker` er en separat macOS-app som var ment å registrere høyst én sykkeldag per
ukedag når en fersk Find My-posisjon er innenfor jobbens geofence. Den beregner
ikke sykkelrute. Den krever Python 3.10–3.14, uoffisielle FindMy.py 0.10.2 og
en Find My-enhetsnøkkel for AirTag-en.
Apple kan kreve 2FA; se også [innlogging og sesjon](https://docs.mikealmel.ooo/FindMy.py/getstarted/01-account.html).

For dokumentasjon av det eldre oppsettet, med en tilgjengelig enhetsnøkkel:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
mkdir -p ~/.config/bike-tracker
cp config.example.env ~/.config/bike-tracker/config.env
```

Rediger konfigurasjonen med `AIRTAG_FILE`, arbeidsplassens koordinater,
tur/retur-distanse og øvrige innstillinger. AirTag-nøkkelen er sensitiv og
skal kun lagres lokalt. Ikke legg passord, tokens eller sesjonsfiler i
konfigurasjon eller kildekode.

Kjør første sjekk interaktivt for Apple-innlogging og eventuell 2FA:

```sh
.venv/bin/bike-tracker check
.venv/bin/bike-tracker status
```

Alternativ konfigurasjon: `.venv/bin/bike-tracker --config /path/to/config.env check`.
`status` summerer unike datoer og konfigurert tur/retur-distanse.

For planlagt kjøring: rediger banene i `launchd/no.erling.bike-tracker.plist`,
og installer den etter vellykket manuell innlogging:

```sh
mkdir -p ~/Library/LaunchAgents ~/Library/Logs
cp launchd/no.erling.bike-tracker.plist ~/Library/LaunchAgents/no.erling.bike-tracker.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/no.erling.bike-tracker.plist
```

Plisten kjører omtrent hver time mandag–fredag; macOS kan utsette kjøringer
mens maskinen sover.
