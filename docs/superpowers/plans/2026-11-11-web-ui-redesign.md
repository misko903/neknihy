# Web UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the cluttered web UI with a sidebar + list/shelf + detail-panel layout that shows deadlines and sync state at a glance.

**Architecture:** Rendering moves out of `src/neknihy_web.py` into `src/neknihy/webui.py`; CSS and JS move into static files served by the existing static handler. Pages stay server-rendered; the detail panel is filled client-side from `data-*` attributes on each row. `Service`, HTTP routes and POST actions keep their behaviour (one addition: `GET /search` without `q` renders an empty search page).

**Tech Stack:** Python 3 standard library (`http.server`, `html`), plain CSS, vanilla JS, `unittest`.

**Spec:** `docs/superpowers/specs/2026-11-11-web-ui-redesign-design.md`

## Global Constraints

- No new dependencies, no build step, no SPA.
- Routes, form field names and POST actions (`/action` with `a=run|refresh|download|sync|return`, `/delete`, `/borrow`, `/settings`) are unchanged.
- Every new or changed string exists in `cs`, `sk`, `en`, `de` (`tests/test_i18n.py` enforces equal key sets and placeholders).
- flake8 with `max-line-length: 100` over `src` (CI, `.github/workflows/check-pr.yml`).
- Status texts (sk, the other languages translate them): `new` → „Stiahne sa pri synchronizácii“, `pending` → „Čaká na Palmknihy“, `ok` → „Stiahnutá“, `expired` → „Prepadnutá“, awaiting catalogue loan → „Požičiava sa…“.
- Deadline urgency: 3 days or fewer left is “soon” (orange).
- Filter box shows only with more than 8 loans.
- Narrow layout below 900 px.
- Desktop Tk app (`src/neknihy.py`) is out of scope.
- Work on a branch (`web-redesign`), not on `main`.

## Review Focus

1. A loan with no or unparseable `end_time` — renders “–” in the days column, sorts last, never raises.
2. An e-book and an audiobook with the same `palm_id` — distinct detail keys (`<palm_id>-e` / `<palm_id>-a`), deleting one never targets the other.
3. Titles/authors containing `'`, `"`, `<`, `&` — escaped in text and in `data-*` attributes.
4. A loan or search result without a cover — placeholder in the shelf, no broken `<img>`.
5. A long multi-line `svc.last_error` — sidebar shows the first line cut to 80 characters, the full text in `title`.

---

## File structure

- Create `src/neknihy/webui.py` — all HTML rendering: `icon`, `page`, `render_index`, `render_search`, `render_settings`, `render_log`, helpers.
- Create `src/neknihy/static/app.css` — all styles (today `CSS` in `neknihy_web.py`).
- Create `src/neknihy/static/app.js` — sorting, filter, view switch, detail panel, menus, theme toggle, delete confirm, ratings loading.
- Modify `src/neknihy_web.py` — keeps `Service`, `Handler`, `main`; imports renderers from `neknihy.webui`.
- Modify `src/neknihy/locales/{cs,sk,en,de}.json`.
- Create `tests/test_webui.py`.

Shared test fixture (Task 1 creates it, later tasks extend it) in `tests/test_webui.py`:

```python
def make_svc(books=(), configured=True, katalog=True, running=False,
             awaiting=(), last_run=None, last_error=None, next_run=None):
    """Fake Service: .app (books, settings, bookAuthors, dateFromString),
    .ratings.get -> None, .web {"interval": 3600, ...}, .log.lines, plus the
    run-state fields given."""
```

`app.bookAuthors` returns `", ".join(book["neknihy"]["authors"])`; `app.dateFromString` is `App.dateFromString` bound to the fake (reuse the real method). Translator: `i18n.Translator("sk")`.

Run all tests: `python -m unittest discover tests` (pytest is not installed).

---

### Task 1: Move rendering, CSS and JS out of `neknihy_web.py`

Pure move; pages look the same afterwards.

**Files:**
- Create: `src/neknihy/webui.py`, `src/neknihy/static/app.css`, `src/neknihy/static/app.js`
- Modify: `src/neknihy_web.py` (lines ~380–1537 move out; `_send_static` at ~1585)
- Test: `tests/test_webui.py`

**Interfaces:**
- Produces: `neknihy.webui` exposing `icon(name, cls="ic") -> str`, `esc(v) -> str`, `page(t, title, body, path="/", refresh=None, svc=None, **kw) -> str`, `render_index(svc, t) -> str`, `render_search(svc, t, query, page_no, kind="ebook") -> str`, `render_settings(svc, t, lang_choice="auto") -> str`, `render_log(svc, t) -> str`, constants `STATUSES`, `PAGE_REFRESH`, `WAIT_STEP`, `STATIC`, `STATIC_DIR`, `STATIC_VER`.
- `STATIC` gains `"/app.css": "app.css"` and `"/app.js": "app.js"`; `STATIC_VER` hashes them too.
- `webui` must not import `neknihy_web` (import cycle). It may import `neknihy_cli.env` for `WAIT_STEP`.

- [ ] **Step 1: Create branch** — `git switch -c web-redesign`
- [ ] **Step 2: Write failing tests** in `tests/test_webui.py` (with `make_svc`):
  - `test_pages_render` — `render_index`, `render_log`, `render_settings` on `make_svc(books=[SAMPLE_OK])` each return a string containing `<link rel='stylesheet' href='/app.css?v=` and `<script src='/app.js?v=`.
  - `test_static_files_registered` — `webui.STATIC["/app.css"] == "app.css"` and both files exist in `webui.STATIC_DIR`.
- [ ] **Step 3: Run** `python -m unittest tests.test_webui -v` — Expected: FAIL (`No module named 'neknihy.webui'`).
- [ ] **Step 4: Move code.** Cut the HTML section of `neknihy_web.py` into `webui.py`; write `CSS` to `app.css` and `TABLE_JS`, `BRAND_JS`, `DELETE_JS`, `nkTheme`/`nkLabel` to `app.js` (wrapped so each part runs on `DOMContentLoaded`). Keep the one-line theme bootstrap (reads `nk-theme`) inline in `<head>`. `page()` links both files with `?v=STATIC_VER`. `_send_static` picks `Content-Type` by extension: `.png` → `image/png`, `.css` → `text/css; charset=utf-8`, `.js` → `text/javascript; charset=utf-8`.
- [ ] **Step 5: Run** `python -m unittest discover tests` — Expected: all pass. Then `python -m flake8 --max-line-length 100 src` if flake8 is installed.
- [ ] **Step 6: Manual check** — start the server with sample data (see Task 8 setup), confirm `/`, `/search?q=christie`, `/settings`, `/log` look unchanged.
- [ ] **Step 7: Commit** — `git commit -m "Web: rendering, CSS and JS in their own files"`

---

### Task 2: Loan helpers — deadlines, statuses, grouping

**Files:**
- Modify: `src/neknihy/webui.py`, locales
- Test: `tests/test_webui.py`

**Interfaces:**
- Produces:
  - `days_left(end_time: str, now: datetime, parse) -> int | None` — whole local calendar days from `now` to the deadline (0 = today, negative = past); `None` when missing/unparseable. `parse` is `app.dateFromString`.
  - `due_text(t, days: int | None) -> str` — `"–"` for `None`; keys `due.today`, `due.tomorrow`, `due.days_few` (2–4), `due.days_many` (5+), all with `{n}` where relevant.
  - `is_soon(days) -> bool` — `days is not None and 0 <= days <= 3`.
  - `status_key(book) -> str` — one of `STATUSES` or `"other"`.
  - `split_loans(books, now, parse) -> (active, expired)` — drops `status == "deleted"`; `expired` = status `expired`; `active` sorted by `days_left` ascending, `None` last, ties keep input order.
  - `loan_key(book) -> str` — `"%s-%s" % (palm_id, "a" if isAudiobook(book) else "e")`.
- Locale changes: rewrite `status.new|pending|ok|expired` to the Global Constraints texts; add `status.awaiting`, `due.today`, `due.tomorrow`, `due.days_few`, `due.days_many`. sk: „dnes“, „zajtra“, „{n} dni“, „{n} dní“; cs: „dnes“, „zítra“, „{n} dny“, „{n} dní“; en: “today”, “tomorrow”, “{n} days”, “{n} days”; de: „heute“, „morgen“, „{n} Tage“, „{n} Tage“.

- [ ] **Step 1: Write failing tests:**
  - `test_days_left` — with `now = datetime(2026, 11, 11, 18, 0, tzinfo=timezone.utc)`: `"2026-11-11T23:00:00+00:00"` → 0, `"2026-11-12T01:00:00+00:00"` → 1, `"2026-11-25T10:00:00+00:00"` → 14, `""` → None, `"nonsense"` → None, missing key → None.
  - `test_due_text_sk` — `None` → `"–"`, 0 → `"dnes"`, 1 → `"zajtra"`, 3 → `"3 dni"`, 7 → `"7 dní"`.
  - `test_is_soon` — 0, 3 True; 4, -1, None False.
  - `test_split_loans` — books with deadlines in 10, 2, none, expired, deleted → active order `[2-day, 10-day, none]`, expired has 1, deleted gone.
  - `test_loan_key_distinguishes_audio` — same `palm_id` 7, ebook → `"7-e"`, audiobook → `"7-a"`.
- [ ] **Step 2: Run** `python -m unittest tests.test_webui -v` — Expected: FAIL (names not defined).
- [ ] **Step 3: Implement** the helpers above; compare `.date()` of both datetimes after converting the deadline to local time (`astimezone()`); naive datetimes are treated as local.
- [ ] **Step 4: Run** `python -m unittest discover tests` — Expected: PASS (including `test_i18n`).
- [ ] **Step 5: Commit** — `git commit -m "Web: deadline and status helpers for the new layout"`

---

### Task 3: Page shell — sidebar with navigation and sync status

**Files:**
- Modify: `src/neknihy/webui.py` (`page`, remove `header_search`, `header_menu`, `nav_tabs`, `icon_link`), `app.css`, `app.js`, locales
- Test: `tests/test_webui.py`

**Interfaces:**
- Consumes: Task 1 `page`.
- Produces: `page(t, title, body, path="/", refresh=None, svc=None, detail=False) -> str`. Layout: `<div class='shell'><aside class='side'>…</aside><main>…</main>` plus, when `detail=True`, `<aside id='detail' class='detail' hidden></aside>`. `sidebar(svc, t, path) -> str`.
- Sidebar content (in order): brand link to `/`; `<nav>` with links `/` (label `nav.loans` + count of active loans in `<span class='count'>`), `/search` (`nav.search`, `id='nav-search'`), `/settings` (`nav.settings`) — current one has `aria-current='page'`; status block `<div class='sync'>`; footer with `/log` link (`log.title`) and the theme button.
- Status block states: not configured → warning + link to `/settings`, no run button; `svc.running` → `sync.running`; `last_error` → `sync.error` + first line of the error cut to 80 chars, full text in `title`; else `sync.ok`. Below: `sync.last` / `sync.next` times (only when known); then `<form method='post' action='/action'><button name='a' value='run' class='primary'>` labelled `btn.sync_now`.
- New keys: `nav.search`, `sync.ok`, `sync.running`, `sync.error`, `sync.last` (`{time}`), `sync.next` (`{time}`), `btn.sync_now`. sk: „Hľadať“, „Synchronizované“, „Synchronizuje sa…“, „Chyba synchronizácie“, „naposledy {time}“, „ďalšia {time}“, „Synchronizovať“. Remove keys no longer used (`nav.found`, `nav.more`, `nav.brand` if unused) from all four files.
- `app.js`: the `nav-search` link points to the last search from `localStorage["neknihy.lastsearch"]` when present (search page saves it, as today).

- [ ] **Step 1: Write failing tests:**
  - `test_sidebar_nav_marks_current` — `render_settings` output has exactly one `aria-current='page'`, on the `/settings` link.
  - `test_sidebar_one_sync_button` — `render_log` (no loans-page actions) contains exactly one `value='run'`, inside `class='sync'`, and no `value='refresh'`.
  - `test_sidebar_not_configured` — `make_svc(configured=False)`: no `value='run'`, has `href='/settings'` inside `class='sync'`.
  - `test_sidebar_error_truncated` — `last_error = "x" * 200 + "\nsecond"`: visible text contains 80 `x` then `…`, `title=` contains the full first line, `second` only inside the title attribute.
  - `test_loans_count` — 2 active + 1 expired book → `<span class='count'>2</span>`.
- [ ] **Step 2: Run** `python -m unittest tests.test_webui -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `sidebar` and the new `page`; CSS grid `grid-template-columns: 220px minmax(0,1fr)` and `220px minmax(0,1fr) 340px` when the detail panel is open (`.shell.has-detail`). Reuse existing colour variables in `app.css`.
- [ ] **Step 4: Run** `python -m unittest discover tests` — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "Web: sidebar with navigation and sync status"`

---

### Task 4: Loans page — list, shelf, expired section, actions menu

**Files:**
- Modify: `src/neknihy/webui.py` (`render_index`, `filter_box`), `app.css`, `app.js`, locales
- Test: `tests/test_webui.py`

**Interfaces:**
- Consumes: Task 2 helpers, Task 3 `page(..., detail=True)`.
- Produces: `loan_row(svc, t, book, now) -> str` and `awaiting_row(t, aw) -> str` (both `<tr>`), the item markup Task 5 reads:
  - `<tr class='item' tabindex='0' data-key data-title data-authors data-kind ('ebook'|'audio') data-due (formatted date or '') data-days (due_text) data-status (label) data-st (status key) data-rid (web id or '') data-file data-cover data-link (palmknihy URL or '') data-palm data-audio ('0'|'1')>`.
  - Columns: cover + title/authors (audio icon before the title), days (`class='due soon'` when `is_soon`), rating (`rating_cell`), status pill `<span class='st st-<key>'>`.
- Content header: `<h1>` `nav.loans`, view switch `<span class='seg' data-view-for='loans'>` with `list` / `shelf` (labels `view.list`, `view.shelf`; replaces `compact/details/grid`), and `<details class='menu'>` containing the `/action` form with buttons `refresh`, `download`, `sync`, `return` (existing `btn.*` texts) and the `/borrow` form (existing `borrow.*` texts / unsupported / not-configured hints).
- Active loans in `<table class='sortable loans' id='loans'>`, then `<h2>` `loans.expired_title` and `<table class='sortable loans' id='expired'>` only when there are expired loans. Awaiting rows go first in the active table with status `status.awaiting`.
- Sortable headers: `th.book` (text), `th.left` (num on `data-v` = days, `None` → 99999), `th.rating` (num, first desc). No status/type/author/file columns.
- `filter_box` only when total loans > 8.
- Empty state (no active, no expired, no awaiting): `loans.empty` text + link to `/search` (`loans.empty_link`).
- Shelf view (CSS, `table[data-view='shelf']`): `tbody` becomes a grid of 140 px tiles: cover (or `.ph` placeholder with title), title, days; other cells hidden.
- New keys: `view.list`, `view.shelf`, `th.left`, `loans.expired_title`, `loans.empty`, `loans.empty_link`, `nav.actions`. sk: „Zoznam“, „Polica“, „Zostáva“, „Prepadnuté · pri synchronizácii sa zmažú z čítačky“, „Zatiaľ nemáte žiadne výpožičky.“, „Nájsť knihu“, „Ďalšie akcie“. Remove `view.compact|details|grid`, `th.type`, `th.file`, `th.author` only if no other page uses them (search does use author/type — keep those).

- [ ] **Step 1: Write failing tests:**
  - `test_expired_in_own_section` — 1 active + 1 expired: expired title appears after `id='expired'`, active before it.
  - `test_active_sorted_by_deadline` — deadlines 10 and 2 days: 2-day title comes first in the HTML.
  - `test_status_labels_sk` — book with status `pending` renders `Čaká na Palmknihy`; `new` renders `Stiahne sa pri synchronizácii`.
  - `test_soon_marked` — 2-day loan row has `class='due soon'`; 10-day does not.
  - `test_actions_in_menu` — `value='refresh'`, `value='download'`, `value='sync'`, `value='return'` each appear exactly once, all inside `<details class='menu'>`.
  - `test_no_file_column` — filename `Duna.epub` appears only as `data-file='Duna.epub'`.
  - `test_filter_only_over_8` — 8 loans: no `data-for='loans'`; 9 loans: present.
  - `test_awaiting_first` — `awaiting=[{"work_id": 5, "title": "Nová", "state": "waiting"}]` → `Požičiava sa…` row before the first loan.
  - `test_escaping` — title `"<b>A'b\"&"` appears only escaped (no raw `<b>` in output; `data-title` attribute value is escaped).
  - `test_same_palm_id_audio_and_ebook` — both rows present with `data-key='7-e'` and `data-key='7-a'`.
  - `test_no_cover_placeholder` — loan without cover has `class='ph'` and no `<img`.
  - `test_empty_state` — no books → `href='/search'` with `Nájsť knihu`.
- [ ] **Step 2: Run** `python -m unittest tests.test_webui -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `render_index`, `loan_row`, `awaiting_row`, shelf CSS, view switch (in `app.js`, `localStorage["neknihy.view.<table id>"]`, default `list`; applies the same view to `#loans` and `#expired`).
- [ ] **Step 4: Run** `python -m unittest discover tests` — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "Web: loans as list or shelf, expired apart, actions in a menu"`

---

### Task 5: Detail panel

**Files:**
- Modify: `src/neknihy/static/app.js`, `app.css`, `src/neknihy/webui.py` (panel templates), locales
- Test: `tests/test_webui.py`

**Interfaces:**
- Consumes: Task 4 row `data-*` attributes; Task 3 `#detail` aside and `.shell.has-detail`.
- Produces: server-rendered `<template id='detail-tpl'>` inside `#detail` holding translated labels and the action markup with empty slots (`[data-f='title']`, `[data-f='authors']`, …); the delete form `<form method='post' action='/delete' class='del'>` with hidden `palm_id`, `audio` and `data-confirm` (existing `delete.confirm` text with `{title}` filled in by JS). Search (Task 6) uses the same template mechanism with a borrow form instead.
- Behaviour (`app.js`): click or Enter on `tr.item` fills a clone of the template, shows `#detail`, adds `has-detail` to `.shell`, marks the row `aria-selected='true'`, sets `location.hash = 'kniha-' + data-key`. Close button (`aria-label` `detail.close`) or Esc hides it and clears the hash (`history.replaceState`). On load, if the hash matches a row, open it. Rating in the panel copies the row’s rating cell text after it loads.
- Panel fields: cover (or placeholder), title, authors, status pill, `detail.kind` (E-kniha/Audiokniha via existing `kind.ebook_tag` / `kind.audio_tag`), `detail.due` (date + days text), `detail.rating`, `detail.file` (hidden when empty), link `detail.open_palm` (hidden when `data-link` empty), button `btn.delete`.
- New keys: `detail.close`, `detail.kind`, `detail.due`, `detail.rating`, `detail.file`, `detail.open_palm`. sk: „Zavrieť“, „Druh“, „Vrátiť do“, „Hodnotenie“, „Súbor“, „Otvoriť na Palmknihy“.

- [ ] **Step 1: Write failing test** `test_detail_template_present` — `render_index` output contains `id='detail'`, `<template id='detail-tpl'>`, `action='/delete'` inside the template, and `Otvoriť na Palmknihy`.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement** the template and the JS behaviour above.
- [ ] **Step 4: Run** `python -m unittest discover tests` — Expected: PASS.
- [ ] **Step 5: Manual check in the browser** (Task 8 setup): click a row → panel shows its data; Esc closes; select a row, reload → panel reopens; delete from the panel asks for confirmation and deletes the right one of two loans sharing a `palm_id`.
- [ ] **Step 6: Commit** — `git commit -m "Web: detail panel for the selected book"`

---

### Task 6: Search page

**Files:**
- Modify: `src/neknihy/webui.py` (`render_search`), `src/neknihy_web.py` (`/search` route), `app.js`, locales
- Test: `tests/test_webui.py`

**Interfaces:**
- Consumes: Task 4 view switch and row conventions, Task 5 template mechanism.
- Produces: `render_search(svc, t, query, page_no, kind="ebook") -> str` handling `query == ""` (form only, no catalogue call). Route: `GET /search` with empty `q` renders it instead of redirecting.
- Form at the top of content: `<form method='get' action='/search' class='bigsearch'>` with `q` (autofocus when empty), kind radio group showing text labels `kind.ebook` / `kind.audiobook` / `kind.all` (existing keys) plus icon, submit button `btn.search`.
- Result rows `tr.item` with `data-key` (`'w' + work_id`), `data-title`, `data-authors`, `data-kind`, `data-pub` (publish_info), `data-rid`, `data-cover`, `data-link` (catalogue `detail_url`), `data-action` (`borrow` | `loaned` | `waiting` | `none`), `data-work`, `data-palm`. Columns: cover + title/authors/publish info, kind icon, rating, action (borrow button / `search.loaned` / `search.borrowed_wait` pill).
- Detail template `search-tpl`: cover, title, authors, publish info, rating, link `detail.open_catalog`, and the `/borrow` form (hidden `work`, `palm_id`, `title`) shown only for `data-action='borrow'`.
- Results count, pagination, `borrow.unsupported` and not-configured hints stay.
- New keys: `detail.open_catalog`, `search.title`. sk: „Otvoriť v katalógu“, „Hľadať v katalógu knižnice“. Remove the header-search-only keys if unused (`search.placeholder` stays for the big field).

- [ ] **Step 1: Write failing tests** (patch `neknihy.webui.katalog.search` with `unittest.mock.patch`):
  - `test_search_empty_query_no_catalogue_call` — `render_search(svc, t, "", 1)` does not call the mock and contains `class='bigsearch'` and the text `E-knihy`.
  - `test_search_results_rows` — one result → `data-action='borrow'`, borrow form with `name='work' value='<id>'`; result whose `palm_id` is in active loans → `data-action='loaned'`.
  - `test_search_kind_checked` — `kind="audiobook"` → the audiobook radio is `checked`.
  - Handler test `test_search_route_without_query` — call the `/search` branch through a minimal handler (or refactor the branch into `search_response(svc, t, query_string) -> str`) and assert it renders instead of redirecting.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement** the page and route change; save the last search to `localStorage` as today.
- [ ] **Step 4: Run** `python -m unittest discover tests` — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "Web: search page with a big search box, list/shelf and detail"`

---

### Task 7: Settings and log in the new shell

**Files:**
- Modify: `src/neknihy/webui.py` (`render_settings`, `render_log`), `app.css`, locales
- Test: `tests/test_webui.py`

**Interfaces:**
- Consumes: Task 3 `page`.
- Settings: `<h1>` `title.settings`, then a row of anchor links to each section (`#set-general`, `#set-account`, `#set-katalog`, `#set-folders`, `#set-convert`, `#set-auto`) using the existing section titles; each section card gets the matching `id`. Language moves into the general section unchanged; the theme stays in the sidebar.
- Log: `<h1>` `log.title` (text, not just an icon), the `<pre>` log, newest first as today. Run status lines are no longer repeated here (sidebar shows them).

- [ ] **Step 1: Write failing tests:**
  - `test_settings_section_anchors` — every `href='#set-…'` has a matching `id='set-…'`.
  - `test_log_has_heading` — `render_log` contains `<h1>` with `Záznam` (the sk `log.title`).
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `python -m unittest discover tests` — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "Web: settings with section links, log with a heading"`

---

### Task 8: Narrow layout and browser verification

**Files:**
- Modify: `src/neknihy/static/app.css`, possibly `app.js`
- Modify: `contrib/screenshot.png` (new screenshot of the loans page, 1200×800, dark theme)

- [ ] **Step 1: Narrow CSS** (`@media (max-width: 900px)`): `.side` becomes a top bar (brand, three nav links, status dot coloured by state, sync button; `sync.last/next` lines and footer hidden); `.detail` becomes a full-screen layer over `main` with the close button labelled `detail.back` (new key; sk „Späť“) at the top; list hides the rating column; shelf tiles `minmax(110px, 1fr)`.
- [ ] **Step 2: Sample data setup** — config dir and work dir in a scratch folder; `<workdir>/.data` with loans covering: `ok` 14 days, `new` audiobook 2 days, `pending` 20 days, `expired`, one with no `end_time`, an e-book and audiobook sharing a `palm_id`, a title with `'"<&`. Numeric `palm_id`s. Write the file as UTF-8. Start: `NEKNIHY_CONFIG_DIR=<cfg> NEKNIHY_WORKDIR=<books> NEKNIHY_EMAIL=demo@example.com NEKNIHY_PASSWORD=x NEKNIHY_INTERVAL=0 NEKNIHY_WEB_PORT=8479 NEKNIHY_LANG=sk python src/neknihy_web.py`.
- [ ] **Step 3: Check at 1200×800 and 375×812, light and dark:** sidebar/top bar, list ↔ shelf (remembered after reload), detail open/close/reload, ⋮ menu, empty search page, a real search (`christie`), settings anchors, log. Expected: no horizontal page scroll, no console errors, every Review Focus case renders as described.
- [ ] **Step 4: Run** `python -m unittest discover tests` and flake8 — Expected: PASS / no output.
- [ ] **Step 5: Replace** `contrib/screenshot.png`.
- [ ] **Step 6: Commit** — `git commit -m "Web: narrow layout, new screenshot"`
