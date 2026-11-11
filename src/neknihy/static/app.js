// Neknihy NextGen web UI scripts (loaded at the end of <body>).

// --- theme: auto -> light -> dark; the saved choice is applied in <head> ---
function nkLabel() {
  var b = document.querySelector('.theme');
  if (!b) return;
  var m = document.documentElement.getAttribute('data-theme') || 'auto';
  b.title = b.getAttribute('data-' + m);
  b.setAttribute('aria-label', b.title);
}
function nkTheme() {
  var r = document.documentElement, c = r.getAttribute('data-theme') || 'auto';
  var n = {auto: 'light', light: 'dark', dark: 'auto'}[c];
  try { n === 'auto' ? localStorage.removeItem('nk-theme') : localStorage.setItem('nk-theme', n); } catch (e) {}
  n === 'auto' ? r.removeAttribute('data-theme') : r.setAttribute('data-theme', n);
  nkLabel();
}
nkLabel();

// --- ⋮ menus close on an outside click ---
document.addEventListener('click', function (e) {
  document.querySelectorAll('details.menu[open]').forEach(function (d) {
    if (!d.contains(e.target)) d.removeAttribute('open');
  });
});

// --- the search page remembers itself; "Search" in the sidebar goes back to it ---
(function () {
  if (location.pathname === '/search') {
    if (location.search) {
      try { localStorage.setItem('neknihy.lastsearch', location.pathname + location.search); } catch (e) {}
    }
    return;
  }
  var a = document.getElementById('nav-search'), s = null;
  try { s = localStorage.getItem('neknihy.lastsearch'); } catch (e) {}
  if (a && s && s.indexOf('/search?') === 0) a.href = s;
})();

// --- confirm before deleting a loan ---
document.addEventListener('submit', function (e) {
  var f = e.target;
  if (f.classList && f.classList.contains('del') && !confirm(f.getAttribute('data-confirm'))) {
    e.preventDefault();
  }
});

// --- list / shelf switch, remembered per browser and page ---
document.querySelectorAll('[data-view-for]').forEach(function (seg) {
  var ids = seg.getAttribute('data-view-for').split(' ');
  var key = 'neknihy.view.' + location.pathname;
  function set(v) {
    var r = seg.querySelector('input[value="' + v + '"]');
    if (!r) return;
    r.checked = true;
    ids.forEach(function (id) {
      var tb = document.getElementById(id);
      if (tb) tb.setAttribute('data-view', v);
    });
  }
  try { set(localStorage.getItem(key)); } catch (e) {}
  seg.addEventListener('change', function (e) {
    set(e.target.value);
    try { localStorage.setItem(key, e.target.value); } catch (x) {}
  });
});

// --- sortable, filterable tables; ratings not cached yet are loaded later ---
(function () {
  function key(tb) { return 'neknihy.sort.' + tb.id; }
  function apply(tb, col, dir) {
    var ths = tb.tHead.rows[0].cells, th = ths[col];
    if (!th || !th.classList.contains('sort')) return;
    for (var i = 0; i < ths.length; i++) ths[i].removeAttribute('aria-sort');
    th.setAttribute('aria-sort', dir > 0 ? 'ascending' : 'descending');
    var num = th.getAttribute('data-type') === 'num', body = tb.tBodies[0];
    var rows = Array.prototype.slice.call(body.rows), lang = document.documentElement.lang;
    rows.sort(function (a, b) {
      var x = a.cells[col].getAttribute('data-v') || '', y = b.cells[col].getAttribute('data-v') || '';
      var r = num ? Number(x) - Number(y) : x.localeCompare(y, lang, {sensitivity: 'base', numeric: true});
      return r * dir || a.getAttribute('data-i') - b.getAttribute('data-i');
    });
    rows.forEach(function (r) { body.appendChild(r); });
    try { localStorage.setItem(key(tb), col + ':' + dir); } catch (e) {}
  }
  function current(tb) {
    var ths = tb.tHead.rows[0].cells;
    for (var i = 0; i < ths.length; i++) {
      var s = ths[i].getAttribute('aria-sort');
      if (s) return [i, s === 'ascending' ? 1 : -1];
    }
    return null;
  }
  function norm(s) { return s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase(); }
  function filter(tb, box) {
    var words = norm(box.value).split(/\s+/).filter(Boolean), n = 0;
    var rows = tb.tBodies[0].rows, cnt = box.closest('.tfilter').querySelector('[data-count]');
    Array.prototype.forEach.call(rows, function (r) {
      var txt = norm(r.textContent);
      var ok = words.every(function (w) { return txt.indexOf(w) >= 0; });
      r.hidden = !ok;
      if (ok) n++;
    });
    cnt.hidden = !words.length;
    cnt.textContent = cnt.getAttribute('data-count').replace('{n}', n).replace('{total}', rows.length);
    try { sessionStorage.setItem('neknihy.filter.' + tb.id, box.value); } catch (e) {}
  }
  document.querySelectorAll('table.sortable').forEach(function (tb) {
    Array.prototype.forEach.call(tb.tBodies[0].rows, function (r, i) { r.setAttribute('data-i', i); });
    var box = document.querySelector('input[data-for="' + tb.id + '"]');
    if (box) {
      try { box.value = sessionStorage.getItem('neknihy.filter.' + tb.id) || ''; } catch (e) {}
      box.addEventListener('input', function () { filter(tb, box); });
      if (box.value) filter(tb, box);
    }
    Array.prototype.forEach.call(tb.tHead.rows[0].cells, function (th, col) {
      if (!th.classList.contains('sort')) return;
      th.querySelector('button').addEventListener('click', function () {
        var c = current(tb);
        apply(tb, col, c && c[0] === col ? -c[1] : (th.getAttribute('data-first') === 'desc' ? -1 : 1));
      });
    });
    try {
      var s = (localStorage.getItem(key(tb)) || '').split(':');
      if (s.length === 2) apply(tb, +s[0], +s[1]);
    } catch (e) {}
    var cells = tb.querySelectorAll('td[data-rid]'), ids = [];
    cells.forEach(function (td) {
      var id = td.getAttribute('data-rid');
      if (ids.indexOf(id) < 0) ids.push(id);
    });
    function fill(data, all) {
      cells.forEach(function (td) {
        var id = td.getAttribute('data-rid');
        if (id === null || !(all || id in data)) return;
        var r = data[id];
        td.removeAttribute('data-rid');
        if (!r) { td.textContent = '–'; return; }
        if (r.percent === null) { td.textContent = '–'; td.title = tb.getAttribute('data-none'); return; }
        td.classList.remove('mut');
        td.setAttribute('data-v', r.percent);
        td.title = tb.getAttribute('data-count').replace('{n}', r.count);
        var b = document.createElement('b');
        b.textContent = tb.getAttribute('data-fmt').replace('{p}', r.percent);
        td.textContent = '';
        td.appendChild(b);
      });
      var c = current(tb);
      if (c) apply(tb, c[0], c[1]);
      document.dispatchEvent(new CustomEvent('nk-ratings'));
    }
    (function next() {
      if (!ids.length) return;
      var part = ids.splice(0, 16);
      fetch('/ratings?ids=' + part.join(','), {credentials: 'same-origin'})
        .then(function (r) { if (!r.ok) throw r; return r.json(); })
        .then(function (d) { fill(d, false); next(); })
        .catch(function () { fill({}, true); });
    })();
  });
})();

// --- detail panel: filled from the clicked item's data-* attributes ---
// In the template, data-f="x" gets the text of data-x, data-src / data-href /
// data-val set src / href / value (hidden when empty), data-st sets the pill
// colour, data-show="x" hides an element when data-x is empty and data-eq="x:v"
// shows it only when data-x is v. The open item is kept in the URL hash, so the
// auto-refresh while syncing opens it again.
(function () {
  var panel = document.getElementById('detail'), tpl = document.querySelector('template[id$="-tpl"]');
  if (!panel || !tpl) return;
  var shell = document.querySelector('.shell'), open = null;
  function rating(row) {
    var td = row.querySelector('td.rt');
    return td ? td.textContent.trim() : '';
  }
  function fill(row) {
    var d = row.dataset, n = tpl.content.cloneNode(true);
    d.rating = rating(row);
    n.querySelectorAll('[data-f]').forEach(function (e) { e.textContent = d[e.getAttribute('data-f')] || '–'; });
    n.querySelectorAll('[data-src]').forEach(function (e) {
      var v = d[e.getAttribute('data-src')];
      if (v) e.src = v; else e.hidden = true;
    });
    n.querySelectorAll('[data-href]').forEach(function (e) {
      var v = d[e.getAttribute('data-href')];
      if (v) e.href = v; else e.hidden = true;
    });
    n.querySelectorAll('[data-val]').forEach(function (e) { e.value = d[e.getAttribute('data-val')] || ''; });
    n.querySelectorAll('[data-st]').forEach(function (e) { e.classList.add('st-' + (d[e.getAttribute('data-st')] || '')); });
    n.querySelectorAll('[data-show]').forEach(function (e) { if (!d[e.getAttribute('data-show')]) e.hidden = true; });
    n.querySelectorAll('[data-eq]').forEach(function (e) {
      var p = e.getAttribute('data-eq').split(':');
      if (d[p[0]] !== p[1]) e.hidden = true;
    });
    n.querySelectorAll('[data-confirm-tpl]').forEach(function (e) {
      e.setAttribute('data-confirm', e.getAttribute('data-confirm-tpl').replace('{title}', d.title || ''));
    });
    panel.textContent = '';
    panel.appendChild(n);
  }
  function show(row) {
    if (open) open.removeAttribute('aria-selected');
    open = row;
    row.setAttribute('aria-selected', 'true');
    fill(row);
    panel.hidden = false;
    shell.classList.add('has-detail');
    history.replaceState(null, '', '#kniha-' + row.getAttribute('data-key'));
  }
  function hide() {
    if (open) { open.removeAttribute('aria-selected'); open.focus(); }
    open = null;
    panel.hidden = true;
    shell.classList.remove('has-detail');
    history.replaceState(null, '', location.pathname + location.search);
  }
  document.addEventListener('click', function (e) {
    if (e.target.closest('[data-close]')) { hide(); return; }
    var row = e.target.closest('tr.item');
    if (!row || e.target.closest('a, button, form, input')) return;
    row === open ? hide() : show(row);
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && open) { hide(); return; }
    var row = e.target.closest && e.target.closest('tr.item');
    if (row && (e.key === 'Enter' || e.key === ' ') && e.target === row) {
      e.preventDefault();
      show(row);
    }
  });
  document.addEventListener('nk-ratings', function () { if (open) fill(open); });
  var m = /^#kniha-(.+)$/.exec(location.hash);
  if (m) {
    var row = document.querySelector('tr.item[data-key="' + CSS.escape(decodeURIComponent(m[1])) + '"]');
    if (row) show(row);
  }
})();

// --- reload while something is going on (keeps the #book of the open detail) ---
(function () {
  var m = document.querySelector('meta[name="nk-refresh"]');
  if (m) setTimeout(function () { location.reload(); }, (+m.content || 10) * 1000);
})();
