# Bike Tracker

Lokal macOS-app som bruker Find My-posisjon fra en AirTag til å registrere høyst én sykkeldag per ukedag. Posisjonen må være fersk og innenfor geofence ved jobb. CSV-filen er sannhetskilden; AirTag-posisjoner brukes ikke til å beregne sykkelrute.

## Krav og begrensninger

- Python 3.10–3.14 og macOS.
- Apple Find My-integrasjonen bruker uoffisielle `FindMy.py` 0.10.2.
- FindMy.py krever en Find My-enhetsnøkkel. Offisielle AirTags krever at nøkkelen først hentes fra en kompatibel Mac og lagres som FindMy.py JSON.
- FindMy.py-dokumentasjonen beskriver innebygd nøkkeluthenting for macOS 14 eller eldre. macOS 15 kan kreve et separat beaconstorekey-verktøy. Gjeldende dokumentasjon sier at macOS 26 beskytter nøkkelen, uten kjent støttet uttrekksmetode. Kontroller dokumentasjonen for din macOS-versjon før oppsett.
- Apple kan kreve 2FA. FindMy.py støtter SMS og betrodd enhet, men ikke passkeys per sin gjeldende dokumentasjon.

FindMy.py dokumentasjon:

- [Logg inn og lagre sesjon](https://docs.mikealmel.ooo/FindMy.py/getstarted/01-account.html)
- [Hent enhetsposisjon og klargjør offisiell AirTag](https://docs.mikealmel.ooo/FindMy.py/getstarted/02-fetching.html)

## Installasjon

```sh
cd /path/to/sykle_til_jobben
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
mkdir -p ~/.config/bike-tracker
cp config.example.env ~/.config/bike-tracker/config.env
```

Rediger `~/.config/bike-tracker/config.env`:

- `AIRTAG_FILE`: bane til FindMy.py JSON for sykkel-AirTag-en. JSON-filen inneholder sensitiv nøkkeldata; lagre den kun lokalt.
- `WORK_LATITUDE` og `WORK_LONGITUDE`: arbeidsplassens koordinater.
- `ROUND_TRIP_DISTANCE_KM`: vanlig tur/retur-distanse.
- Radius, maks posisjonsalder, tidssone og CSV-bane kan tilpasses.

Bytt eksempelverdier med faktiske data. Ikke legg Apple-passord, tokens eller sesjonsfiler i env-fil eller kildekode. Kontoøkt og Anisette-bibliotek lagres som standard under `~/.config/bike-tracker/`; appen setter kontosesjon, AirTag-nøkkeldata og CSV-fil til eierlesbar/-skrivbar tilgang når de skrives.

Første `check` må kjøres fra terminal. Den ber om Apple Account og eventuelt 2FA, og lagrer økt lokalt. Ikke kjør første autentisering via launchd, siden launchd ikke har en interaktiv terminal.

## Bruk

```sh
source .venv/bin/activate
bike-tracker check
bike-tracker status
```

Alternativ config-fil:

```sh
bike-tracker --config /path/to/config.env check
```

`check` ignorerer helger, avviser framtidige/for gamle observasjoner og registrerer kun første treff for lokal dato. `status` summerer unike datoer og ganger med konfigurert tur/retur-distanse.

## launchd

Rediger `launchd/no.erling.bike-tracker.plist`: erstatt `/Users/USERNAME` med hjemmekatalogen din, og bytt `/PATH/TO/sykle_til_jobben/.venv/bin/bike-tracker` til absolutt bane for prosjektets `.venv`-binær.

Installer og start etter vellykket manuell autentisering:

```sh
mkdir -p ~/Library/LaunchAgents ~/Library/Logs
cp launchd/no.erling.bike-tracker.plist ~/Library/LaunchAgents/no.erling.bike-tracker.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/no.erling.bike-tracker.plist
```

Plisten kjører hvert helt klokkeslett mandag–fredag. Programmet har eget helgefilter. macOS kan slå sammen eller utsette kjøringer mens maskinen sover; kjøringen er «omtrent hver time», ikke en sanntidsgaranti.

## Tester

```sh
python -m pytest
```

Enhetstestene bruker falsk Find My-klient og trenger ikke Apple-innlogging eller nettverk.

## Sykkelpendling fra Home Assistant

`ha_commutes.py` er et separat script for Python 3.10+ på macOS/Linux. Det bruker
prosjektets eksisterende `python-dotenv`-avhengighet; kjør med prosjektets virtuelle miljø.
Det korrelerer aktivitet fra `ACTIVITY_ENTITY` med sonehistorikken til `TRACKER_ENTITY`.
Begge må være inkludert i Home Assistants `recorder`. Standardverdiene er
`sensor.erlings_iphone_3_activity` og `device_tracker.erlings_iphone_3`.
Hjemmesonen er `home`, og jobbsonen settes med `ZONE_WORK_NAME` (standard: `Å Energi`).

Scriptet leser automatisk `.env` ved siden av scriptet, uavhengig av arbeidsmappen.
Filen tolkes som konfigurasjon, ikke skallkode. Eksempel:

```dotenv
HA_URL=http://homeassistant.localdomain:8123
HA_TOKEN=sett_tokenet_ditt_her
ZONE_WORK_NAME="Å Energi"
ACTIVITY_ENTITY=sensor.erlings_iphone_3_activity
TRACKER_ENTITY=device_tracker.erlings_iphone_3
```

Tokenfilen skal ikke legges i kildekontroll. `.env` er allerede ignorert av Git;
beskytt filen med `chmod 600 .env`. Miljøvariabler overstyrer verdiene i filen.
En annen fil kan velges med `--config /path/to/config.env`; en eksplisitt valgt,
manglende fil gir feil. Hvis standardfilen mangler, kan miljøvariabler fortsatt brukes.
Verdier leses bokstavelig, uten `${...}`-utvidelser.

```sh
/path/to/sykle_til_jobben/.venv/bin/python /path/to/sykle_til_jobben/ha_commutes.py
```

Scriptet krever ingen interaktiv terminal, og kan lese samme fil ved planlagt kjøring.
Standardfiler lagres ved siden av scriptet,
uavhengig av arbeidsmappen:

- `data/YYYY-sykkeldager.csv`: én fil per år med dato, avreisetid hjemmefra, ankomst jobb, avreise jobb, ankomst hjem.
- `ha-commutes.state.json`: siste vellykkede kjøretidspunkt og eventuell uferdig tur.

Alternative filer: `python3 ha_commutes.py --csv /path/to/commutes.csv --state /path/to/state.json`.
Med `--csv` lagres alle år i den angitte filen; uten `--state` brukes da samme bane
med endelsen `.state.json`. Standard kjørestat deles mellom årsfilene.
CSV og kjørestat lagres atomisk med tilgang kun for eier. En lokal lås hindrer parallelle
kjøringer fra å overskrive hverandre.

En tur krever avgang fra én av sonene, ankomst i den andre og minst én overlappende
`Cycling`-periode. Mellomliggende soner er tillatt. Manglende soneobservasjoner
(`unknown`/`unavailable`) bryter turen. Tidene er omtrentlige GPS-sonetidspunkter i
`Europe/Oslo`, ikke nødvendigvis tidspunktet da syklingen startet eller sluttet.

Kun turer med avreise mandag–fredag telles, uten begrensninger på klokkeslett.
Hver retning telles separat; den andre retningens felt står tomme hvis den ikke ble
syklet. Ved flere turer i samme retning samme dag beholdes den første. Datoen er
avreisedatoen, også når ankomsten skjer etter midnatt. Årsfilen velges ut fra
avreisedatoen i lokal tid, også ved søk som krysser nyttår.

Første kjøring, manglende CSV eller en kjørestat eldre enn 30 dager gir et søk i de
siste 30 dagene. Ellers hentes historikk fra dagen før forrige kjøring, eller tidligere
hvis en tur fortsatt er uferdig, alltid begrenset til 30 dager. Overlappen gjør at
ettermiddagsturen kan fylle ut en eksisterende morgenrad uten duplikater. Eldre
CSV-rader beholdes. Historikk som allerede er slettet av Home Assistant kan ikke
rekonstrueres. Tom historikk eller API-feil avbryter uten å flytte kjøretidspunktet.

### Docker og cron

På en maskin med Docker: kopier `ha_commutes.py`, `Dockerfile`, `.dockerignore`
og `.env` til samme mappe. `.env` kopieres ikke inn i imaget. Bruk en Home
Assistant-adresse som er tilgjengelig fra containeren; `localhost` viser til
containeren, ikke til Home Assistant-maskinen.

Bygg imaget og opprett en datamappe:

```sh
cd /path/to/sykle_til_jobben
chmod 600 .env
mkdir -p data
docker build -t sykkeldager .
```

Kopier eksisterende årsfiler til `data/` før første kjøring. Kopier også
eksisterende `ha-commutes.state.json` dit hvis kjørestaten skal videreføres.
Containeren lagrer både CSV-filer, kjørestat og lås i denne mappen.
Kjør manuelt først:

```sh
docker run --rm --user "$(id -u):$(id -g)" \
  --mount "type=bind,src=$(pwd)/.env,dst=/app/.env,readonly" \
  --mount "type=bind,src=$(pwd)/data,dst=/app/data" \
  sykkeldager
```

Brukeren må kunne lese `.env` og lese og skrive i `data/`. Opprettede CSV- og
state-filer er kun tilgjengelige for brukeren som kjører containeren.

Legg deretter til en daglig kjøring i `crontab -e`. Eksemplet kjører kl. 23:00
etter maskinens lokale tid. Bytt ut `/path/to/sykle_til_jobben`, `/usr/bin/docker`
og `1000:1000` med absolutte baner og brukerens UID/GID (`id -u` og `id -g`).
Hele oppføringen skal stå på én linje:

```cron
0 23 * * * /usr/bin/docker run --rm --user 1000:1000 --mount type=bind,src=/path/to/sykle_til_jobben/.env,dst=/app/.env,readonly --mount type=bind,src=/path/to/sykle_til_jobben/data,dst=/app/data sykkeldager >> /path/to/sykle_til_jobben/cron.log 2>&1
```

Se `cron.log` ved feil. Cron starter en ny container hver gang og starter ikke
automatisk jobben på nytt hvis maskinen var avslått på kjøretidspunktet.
