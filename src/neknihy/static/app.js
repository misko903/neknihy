// Neknihy NextGen web UI scripts (loaded at the end of <body>).
// Theme toggle; the saved theme itself is applied by an inline script in <head>.
function nkLabel(){var b=document.querySelector('.theme'),m=document.documentElement.getAttribute('data-theme')||'auto';b.title=b.getAttribute('data-'+m);b.setAttribute('aria-label',b.title)}document.addEventListener('DOMContentLoaded',nkLabel);function nkTheme(){var r=document.documentElement,c=r.getAttribute('data-theme')||'auto',n={auto:'light',light:'dark',dark:'auto'}[c];try{n=='auto'?localStorage.removeItem('nk-theme'):localStorage.setItem('nk-theme',n)}catch(e){}n=='auto'?r.removeAttribute('data-theme'):r.setAttribute('data-theme',n);nkLabel()}
// Close ⋮ menus on an outside click; remember / link the last search.
document.addEventListener('click',function(e){
document.querySelectorAll('details.menu[open]').forEach(function(d){
if(!d.contains(e.target))d.removeAttribute('open');});});
(function(){var a=document.getElementById('found');
if(location.pathname==='/search'){try{localStorage.setItem('neknihy.lastsearch',
location.pathname+location.search);}catch(e){}return;}
if(!a)return;var s=null;
try{s=localStorage.getItem('neknihy.lastsearch');}catch(x){}
if(s&&s.indexOf('/search?')===0){a.href=s;a.hidden=false;}})();
// Confirm before deleting a loan.
document.querySelectorAll('form.del').forEach(function(f){
f.addEventListener('submit',function(e){if(!confirm(f.dataset.confirm))e.preventDefault();});
});
// Sortable, filterable tables with views; ratings loaded later.
(function(){
function key(tb){return 'neknihy.sort.'+tb.id;}
function apply(tb,col,dir){var ths=tb.tHead.rows[0].cells,th=ths[col];
if(!th||!th.classList.contains('sort'))return;
for(var i=0;i<ths.length;i++)ths[i].removeAttribute('aria-sort');
th.setAttribute('aria-sort',dir>0?'ascending':'descending');
var num=th.getAttribute('data-type')==='num',body=tb.tBodies[0],
rows=Array.prototype.slice.call(body.rows),lang=document.documentElement.lang;
rows.sort(function(a,b){var x=a.cells[col].getAttribute('data-v')||'',
y=b.cells[col].getAttribute('data-v')||'',r=num?(Number(x)-Number(y)):
x.localeCompare(y,lang,{sensitivity:'base',numeric:true});
return r*dir||(a.getAttribute('data-i')-b.getAttribute('data-i'));});
rows.forEach(function(r){body.appendChild(r);});
try{localStorage.setItem(key(tb),col+':'+dir);}catch(e){}}
function current(tb){var ths=tb.tHead.rows[0].cells;for(var i=0;i<ths.length;i++){
var s=ths[i].getAttribute('aria-sort');if(s)return [i,s==='ascending'?1:-1];}return null;}
function norm(s){return s.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase();}
function filter(tb,box){var words=norm(box.value).split(/\s+/).filter(Boolean),n=0,
rows=tb.tBodies[0].rows,cnt=box.parentNode.querySelector('[data-count]');
Array.prototype.forEach.call(rows,function(r){var txt=norm(r.textContent),
ok=words.every(function(w){return txt.indexOf(w)>=0;});r.hidden=!ok;if(ok)n++;});
cnt.hidden=!words.length;cnt.textContent=cnt.getAttribute('data-count')
.replace('{n}',n).replace('{total}',rows.length);
try{sessionStorage.setItem('neknihy.filter.'+tb.id,box.value);}catch(e){}}
document.querySelectorAll('table.sortable').forEach(function(tb){
Array.prototype.forEach.call(tb.tBodies[0].rows,function(r,i){r.setAttribute('data-i',i);});
var vw=document.querySelector('[data-view-for="'+tb.id+'"]');
if(vw){var setView=function(v){var r=vw.querySelector('input[value="'+v+'"]');
if(!r)return;r.checked=true;tb.setAttribute('data-view',v);};
try{setView(localStorage.getItem('neknihy.view.'+tb.id));}catch(e){}
vw.addEventListener('change',function(e){setView(e.target.value);
try{localStorage.setItem('neknihy.view.'+tb.id,e.target.value);}catch(x){}});}
var box=document.querySelector('input[data-for="'+tb.id+'"]');
if(box){try{box.value=sessionStorage.getItem('neknihy.filter.'+tb.id)||'';}catch(e){}
box.addEventListener('input',function(){filter(tb,box);});if(box.value)filter(tb,box);}
Array.prototype.forEach.call(tb.tHead.rows[0].cells,function(th,col){
if(!th.classList.contains('sort'))return;
th.querySelector('button').addEventListener('click',function(){var c=current(tb);
apply(tb,col,c&&c[0]===col?-c[1]:(th.getAttribute('data-first')==='desc'?-1:1));});});
try{var s=(localStorage.getItem(key(tb))||'').split(':');
if(s.length===2)apply(tb,+s[0],+s[1]);}catch(e){}
var cells=tb.querySelectorAll('td[data-rid]'),ids=[];
cells.forEach(function(td){var id=td.getAttribute('data-rid');if(ids.indexOf(id)<0)ids.push(id);});
function fill(data,all){cells.forEach(function(td){var id=td.getAttribute('data-rid');
if(id===null||!(all||id in data))return;var r=data[id];td.removeAttribute('data-rid');
if(!r){td.textContent='–';return;}
if(r.percent===null){td.textContent='–';td.title=tb.getAttribute('data-none');return;}
td.classList.remove('mut');td.setAttribute('data-v',r.percent);
td.title=tb.getAttribute('data-count').replace('{n}',r.count);
var b=document.createElement('b');b.textContent=tb.getAttribute('data-fmt')
.replace('{p}',r.percent);td.textContent='';td.appendChild(b);});
var c=current(tb);if(c)apply(tb,c[0],c[1]);}
(function next(){if(!ids.length)return;var part=ids.splice(0,16);
fetch('/ratings?ids='+part.join(','),{credentials:'same-origin'}).then(function(r){
if(!r.ok)throw r;return r.json();}).then(function(d){fill(d,false);next();})
.catch(function(){fill({},true);});})();});})();
