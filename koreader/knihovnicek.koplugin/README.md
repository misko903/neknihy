# Knihovníček pre KOReader

Knihovníček je samostatný plugin pre [KOReader](https://koreader.rocks/). Nepotrebuje
server ani desktopovú aplikáciu [Neknihy NextGen](https://github.com/misko903/neknihy): s Palmknihy a katalógom knižnice
komunikuje priamo z čítačky.

Čo vie:

- **Moje výpožičky** – zoznam aktuálnych výpožičiek z Palmknihy (s dátumom
  vypršania); ťuknutím knihu stiahnete, otvoríte alebo zmažete,
- **Stiahnuť nové výpožičky** – stiahne všetky e-knihy, ktoré ešte nemáte,
- **Zmazať prepadnuté knihy** – zmaže súbory výpožičiek, ktorým skončila lehota,
- **Hľadať v katalógu knižnice** – vyhľadá e-knihy (podľa názvu aj autora)
  v katalógu katalog.tsk.sk a jedným ťuknutím ich vypožičia.

Rozhranie je v slovenčine, češtine a angličtine podľa jazyka KOReaderu.

## Inštalácia

1. Stiahnite `knihovnicek.koplugin.zip` z [Releases](../../releases), rozbaľte ho a priečinok `knihovnicek.koplugin` skopírujte do priečinka `plugins` v KOReaderi
   (na Kobo `.adds/koreader/plugins/`, na Kindle `koreader/plugins/`,
   na PocketBook `applications/koreader/plugins/`, v Androide
   `koreader/plugins/` v úložisku).
2. Reštartujte KOReader. Plugin sa objaví v hornom menu
   (ikona ozubeného kolesa → **Nástroje** → **Knihovníček**).

## Nastavenie

V menu **Knihovníček → Nastavenia** zadajte:

| Položka | Význam |
| --- | --- |
| E-mail / heslo Palmknihy | prihlásenie do účtu Palmknihy (potrebné pre výpožičky a sťahovanie) |
| Priečinok pre stiahnuté knihy | predvolene `Knihovnicek` v domovskom priečinku KOReaderu |
| Knižnica | katalóg, v ktorom sa hľadá a požičiava (predvolene VKMR v Trenčíne) |
| Meno / heslo do katalógu | prihlásenie do katalógu knižnice (potrebné len na požičiavanie) |

Údaje sa ukladajú v súbore `knihovnicek.lua` v priečinku nastavení KOReaderu
ako obyčajný text.

## Obmedzenia

Oproti aplikácii [Neknihy NextGen](https://github.com/misko903/neknihy) plugin zatiaľ:

- pracuje len s e-knihami (audioknihy KOReader neprehráva),
- z EPUB neodstraňuje stranu so sociálnym DRM,
- hľadá a požičiava len v katalógoch na katalog.tsk.sk (nie v Mestskej
  knižnici v Bratislave),
- aktívnu výpožičku, ktorej súbor zmažete, skryje až do vypršania lehoty
  (rovnako ako aplikácia), takže sa už nesťahuje znova.

## Vývoj

Čisté moduly (`nk_util`, `nk_http`, `nk_katalog`, `nk_palmknihy`) sa dajú
testovať obyčajným Lua bez KOReaderu:

```bash
lua tests/run.lua
```

Používateľské rozhranie (`main.lua`) beží len v KOReaderi.
