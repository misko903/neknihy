# Návrh: prerobenie webového rozhrania

Dátum: 2026-11-11 · Stav: na schválenie

## Prečo

Súčasné webové rozhranie je neprehľadné:

- päť akčných tlačidiel (Spustiť všetko, Obnoviť, Stiahnuť, Synchronizovať,
  Zmazať prepadnuté) ukazuje vnútorné kroky programu, hoci „Spustiť všetko“
  robí ostatné štyri,
- hlavička je plná ikon bez popisu a záložka mení názov podľa stránky
  („Nájdené“ / „Výpožičky“),
- tabuľka má 8 stĺpcov vrátane názvu súboru, termín je len dátum
  a prepadnuté knihy sú pomiešané s aktívnymi,
- stavy sú technické („nová“, „čaká na sprístupnenie“),
- filter, triedenie každého stĺpca a tri pohľady sú na pár kníh priveľa,
- stav synchronizácie a chyby sú malým šedým textom.

## Cieľ a hranice

- Rozhranie sa používa hlavne na počítači; mobil musí fungovať, ale nie je
  hlavný cieľ.
- Mení sa len vykresľovanie (HTML, CSS, JS) webu. Logika (`App`, `Service`,
  katalóg, plánovač), URL adresy a POST akcie zostávajú rovnaké.
- Bez nových závislostí, bez buildovacieho kroku, bez SPA – stránky sa
  naďalej vykresľujú na serveri.
- Všetky štyri jazyky (cs, sk, en, de) dostanú nové texty.

Úspech: na úvodnej stránke je bez hľadania jasné, čo končí čoskoro, čo je
stiahnuté, či posledná synchronizácia prebehla v poriadku a kde sa dá
synchronizovať, hľadať a meniť nastavenia.

## Rozloženie

Tri stĺpce na šírku (spojenie návrhov A, B a C):

```
┌────────────┬──────────────────────────────┬──────────────┐
│ Neknihy    │ Výpožičky  [Zoznam|Polica] ⋮ │  (detail     │
│            │                              │   vybranej   │
│ Výpožičky 4│  zoznam alebo polica         │   knihy)     │
│ Hľadať     │                              │              │
│ Nastavenia │  Prepadnuté · zmažú sa…      │              │
│            │                              │              │
│ ✓ Synchr.  │                              │              │
│ 18:00/00:00│                              │              │
│ [Synchr.]  │                              │              │
│ Záznam ◐   │                              │              │
└────────────┴──────────────────────────────┴──────────────┘
```

### Ľavý panel (na každej stránke)

- Logo a názov (odkaz na Výpožičky).
- Navigácia s textom aj ikonou: **Výpožičky** (počet aktívnych),
  **Hľadať**, **Nastavenia**. Aktuálna stránka je zvýraznená
  (`aria-current="page"`).
- Dole stavový blok:
  - stav poslednej synchronizácie: „Synchronizované“ zelenou, „Prebieha…“,
    alebo „Chyba“ červenou s textom chyby (skrátený, celý v `title`),
  - čas poslednej a ďalšej synchronizácie,
  - jediné tlačidlo **Synchronizovať** (POST `/action`, `a=run`),
  - ak nie sú vyplnené nastavenia: upozornenie s odkazom na Nastavenia
    namiesto tlačidla,
  - malý odkaz **Záznam** (`/log`) a prepínač témy (auto/svetlá/tmavá).
- Ikona ⋮ s požičaním cez odkaz z hlavičky odpadá (presúva sa do menu ⋮
  na stránke Výpožičky).

### Stránka Výpožičky (`/`)

- Hlavička obsahu: nadpis „Výpožičky“, prepínač **Zoznam / Polica**
  (voľba sa pamätá v `localStorage`), menu ⋮ s pokročilými akciami:
  Obnoviť zoznam, Stiahnuť, Synchronizovať čítačku, Zmazať prepadnuté
  (existujúce `a=refresh|download|sync|return`) a formulár
  **Požičať cez odkaz** (POST `/borrow`).
- Karty „čaká sa na výpožičku z katalógu“ (`svc.awaiting`) sa zobrazia ako
  riadky na začiatku zoznamu so stavom „Požičiava sa…“.
- Filter sa zobrazí len pri viac ako 8 knihách.
- **Zoznam** – riadky so 4 stĺpcami:
  1. obal (malý) + názov, pod ním autor; audiokniha má ikonu slúchadiel,
  2. **Zostáva** – „3 dni“, „zajtra“, „dnes“; do 3 dní oranžovou,
  3. **Hodnotenie** – ako doteraz, načítava sa dodatočne,
  4. **Stav** ako štítok ľudskou rečou (pozri nižšie).
  Predvolené poradie podľa termínu; kliknutím na hlavičku Názov / Zostáva /
  Hodnotenie sa dá preradiť (existujúci `TABLE_JS`, pamätá si voľbu).
- **Polica** – mriežka obalov (cca 140 px), pod obalom názov a „zostáva X“.
  Kniha bez obalu má zástupný obdĺžnik s názvom.
- **Prepadnuté** – samostatná sekcia pod aktívnymi s nadpisom
  „Prepadnuté · pri synchronizácii sa zmažú z čítačky“, v rovnakom pohľade.
- Prázdny stav: „Zatiaľ nemáte žiadne výpožičky“ + odkaz na Hľadať.
- Kôš a názov súboru sa zo zoznamu presúvajú do detailu.

### Stavy ľudskou rečou

| interný stav | text (sk)                     | farba     |
| ------------ | ----------------------------- | --------- |
| `new`        | Stiahne sa pri synchronizácii | neutrálna |
| `pending`    | Čaká na Palmknihy             | oranžová  |
| `ok`         | Stiahnutá                     | zelená    |
| `expired`    | Prepadnutá                    | červená   |
| (awaiting)   | Požičiava sa…                 | oranžová  |

Pozn.: „Na čítačke“ z náčrtu sa nepoužije, lebo aplikácia si stav
kopírovania na čítačku pre jednotlivé knihy neukladá.

### Panel s detailom (pravý stĺpec)

- Otvorí sa kliknutím na riadok alebo obal (aj Enter na zameranom riadku),
  zavrie sa krížikom alebo Esc. Kým nie je nič vybrané, stĺpec je skrytý
  a zoznam zaberá celú šírku.
- Obsah: väčší obal, názov, autor, štítok stavu, druh (E-kniha /
  Audiokniha), termín vrátenia (dátum aj „o X dní“), hodnotenie, názov
  súboru, odkaz **Otvoriť na Palmknihy** a **Vymazať** (POST `/delete`
  s potvrdením ako doteraz).
- Dáta sa berú z `data-*` atribútov riadku (bez nového API). Panel vypĺňa
  malý skript.
- Vybraná kniha sa uloží do `location.hash` (`#kniha-<palm_id>-<a|e>`),
  takže automatické obnovenie stránky počas synchronizácie (meta refresh)
  panel znovu otvorí.

### Stránka Hľadať (`/search`)

- Bez dopytu (`/search` bez `q`) sa zobrazí prázdna stránka s veľkým poľom
  namiesto presmerovania na `/`.
- Veľké vyhľadávacie pole na vrchu obsahu, prepínač druhu **slovami**:
  E-knihy · Audioknihy · Všetko.
- Výsledky v rovnakom Zozname / Polici (stĺpce: obal + názov/autor/vydanie,
  druh, hodnotenie, akcia). Akcia: tlačidlo **Požičať**, alebo štítok
  „Už máte požičané“ / „Požičiava sa…“.
- Detail výsledku: obal, autor, vydanie, hodnotenie, odkaz do katalógu
  knižnice a **Požičať**.
- Stránkovanie a hlášky (nepodporovaná knižnica, chýbajúce prihlásenie)
  zostávajú.
- Posledné hľadanie sa pamätá (`localStorage`) – položka Hľadať v ľavom
  paneli naň vedie.

### Nastavenia a Záznam

- Obsah zostáva (sekcie nastavení, výpis záznamu), len v novom rámci.
- Nastavenia dostanú navigáciu sekcií v hornej časti (kotvy), keďže sú dlhé.

## Úzke okno (pod ~900 px)

- Ľavý panel sa zmení na hornú lištu: logo, tri položky navigácie a ikonka
  stavu (farebná bodka); tlačidlo Synchronizovať zostane v lište.
- Detail sa otvorí cez celú obrazovku s tlačidlom Späť.
- Zoznam skryje stĺpec Hodnotenie; polica má 2–3 stĺpce.

## Organizácia kódu

`neknihy_web.py` má ~1750 riadkov, väčšina je HTML, CSS a JS v reťazcoch.
Pri prerobení sa rozdelí:

- `src/neknihy/static/app.css` – všetky štýly (dnes `CSS`),
- `src/neknihy/static/app.js` – triedenie, filter, pohľady, detail, téma,
  menu (dnes `TABLE_JS`, `BRAND_JS`, `DELETE_JS`, časť `THEME_JS`; skript,
  ktorý nastaví tému pred prvým vykreslením, zostáva inline v `<head>`),
- `src/neknihy/webui.py` – vykresľovanie stránok (`page`, `render_*`,
  ikony, pomocné funkcie),
- `neknihy_web.py` – `Service`, HTTP handler, `main`.

Statické súbory pôjdu cez existujúci `STATIC` / `_send_static` s verziou
v URL (`STATIC_VER` sa rozšíri o nové súbory). `Content-Type` sa doplní
pre `.css` a `.js`.

## Testovanie

- Nový `tests/test_webui.py`: vykreslí Výpožičky, Hľadať (s podvrhnutým
  výsledkom katalógu), Nastavenia a Záznam s falošnou `Service` a overí:
  - prepadnuté sú v samostatnej sekcii, aktívne zoradené podľa termínu,
  - texty stavov podľa tabuľky vyššie,
  - jediné hlavné tlačidlo Synchronizovať, pokročilé akcie v menu,
  - `data-*` atribúty pre detail (aj pre audioknihy s rovnakým `palm_id`),
  - neprihlásený stav ukazuje odkaz na Nastavenia,
  - všetky nové kľúče existujú vo všetkých jazykoch (rozšírenie
    `test_i18n.py`).
- Ručná kontrola v prehliadači so vzorovými dátami: svetlá aj tmavá téma,
  šírka 1200 px a 375 px, prepínanie pohľadov, detail, obnovenie počas
  synchronizácie.

## Mimo rozsahu

- Desktopová aplikácia (`neknihy.py`, Tk) sa nemení.
- Žiadne nové funkcie (napr. predĺženie výpožičky, popis knihy z katalógu).
- Sledovanie, či je kniha skutočne na čítačke.
