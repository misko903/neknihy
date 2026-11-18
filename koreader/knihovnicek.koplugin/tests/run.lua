-- Tests of the plugin's pure modules. Run from the plugin directory with
-- plain Lua / LuaJIT (no KOReader needed):
--
--     lua tests/run.lua
--
package.path = "?.lua;" .. package.path

-- minimal stand-in for LuaSocket's ltn12 (the real one is part of KOReader)
package.preload["ltn12"] = function()
    return {
        sink = { table = function(t) return function(chunk) if chunk then t[#t + 1] = chunk end return 1 end end },
        source = { string = function(s) return function() local r = s; s = nil; return r end end },
    }
end

local Util = require("nk_util")
local Http = require("nk_http")
local Katalog = require("nk_katalog")
local Palm = require("nk_palmknihy")

local failed = 0
local function eq(actual, expected, name)
    if actual ~= expected then
        failed = failed + 1
        print(string.format("FAIL %s: expected %s, got %s", name, tostring(expected), tostring(actual)))
    end
end

-- util
eq(Util.parseTime("2026-01-31T12:00:00Z"), 1769860800, "parseTime Z")
eq(Util.parseTime("2026-01-31T14:00:00+02:00"), 1769860800, "parseTime offset")
eq(Util.parseTime("2026-01-31"), 1769817600, "parseTime date")
eq(Util.parseTime("20260131"), 1769817600, "parseTime compact")
eq(Util.parseTime("nonsense"), nil, "parseTime junk")
eq(Util.urlencode("a b&č"), "a%20b%26%C4%8D", "urlencode")
eq(Util.parseQuery("filename=Kniha%20X&url=https%3A%2F%2Fcdn.palmknihy.cz%2Fa").filename, "Kniha X", "parseQuery")
eq(Util.parseQuery("filename=Kniha%20X&url=https%3A%2F%2Fcdn.palmknihy.cz%2Fa").url,
    "https://cdn.palmknihy.cz/a", "parseQuery url")
eq(Util.encodeForm({ b = "2", a = "1 1" }), "a=1%201&b=2", "encodeForm")
eq(Util.text("<b>Tom &amp; Jerry</b>&nbsp;&#269;"), "Tom & Jerry č", "text")
eq(Util.safeName("a/b:c?"), "a_b_c_", "safeName")

-- catalogue search page
local PAGE = [[
<div class="pagination-info">Zobrazené záznamy 1 - 2 z celkom 2.</div>
<div data-isbn="[9788024293097]" data-work-id="1481396" class="result harvested">
  <div class="cover">
    <a class="thumbnail" data-large-cover="https://core.palmknihy.cz/api/books/390655/cover/400"
       href="/library/vkmr/detail/1481396?search=x&amp;si=1"><img src="https://x/c.jpg"></a>
    <span class="work-type tt icon" title="" data-original-title="E-knihy - "></span>
  </div>
  <div class="showitems">
    <div class="Title"><span class="showitem"><span class="showitem-content"><a href="/d">
      <span class="showitem"><span class="showitem-content"><span class="highlight">Poirot</span>
      - Největší detektiv na světě</span></span></a></span></span><br></div>
    <div class="Authors"><span class="showitem"><span class="showitem-content">Aldridge, Mark</span>
      </span><br></div>
    <div class="Publish_info"><span class="showitem"><span class="showitem-label">Vydavateľské
      údaje: </span><span class="showitem-content">Euromedia 2023 </span></span><br></div>
    <div class="Keywords"><span class="showitem"><span class="showitem-label">Kľúčové slová:
      </span><span class="showitem-content">E-knihy</span></span></div>
  </div>
  <div class="work-availability btn-group" data-original-title="Dostupné v databázi: Palmknihy">
    <ul><li><a href="https://www.palmknihy.cz/kniha/390655">kniha</a></li></ul>
  </div>
  <a class="btn" href="/library/vkmr/eborrow/1481396?search=x&amp;si=1">Požičať E-knihu</a>
</div>
<div data-work-id="42" class="result">
  <div class="showitems"><div class="Title"><span class="showitem-content">Tlačená kniha</span>
  </div><div class="Authors"><span class="showitem-content">A, B</span>
  <span class="showitem-content">C, D</span></div></div>
</div>
]]
local data = Katalog.parseSearch(PAGE)
eq(data.total, 2, "total")
eq(#data.results, 2, "records")
local first, second = data.results[1], data.results[2]
eq(first.work_id, 1481396, "work_id")
eq(first.title, "Poirot - Největší detektiv na světě", "title")
eq(first.authors[1], "Aldridge, Mark", "author")
eq(first.publish_info, "Euromedia 2023", "publish_info")
eq(first.palm_id, 390655, "palm_id")
eq(first.ebook, true, "ebook")
eq(first.audiobook, false, "audiobook")
eq(first.work_type, "E-knihy", "work_type")
eq(first.source, "Palmknihy", "source")
eq(second.ebook, false, "printed book is not an e-book")
eq(#second.authors, 2, "two authors")

eq(Katalog.findCsrf("<meta name='_csrf' content='TOKEN-1'>"), "TOKEN-1", "csrf meta")
eq(Katalog.findCsrf("<form><input type='hidden' name='_csrf' value='TOKEN-2'></form>"), "TOKEN-2", "csrf input")

-- login + loan over a fake transport (cookies are kept, redirects are not followed)
local calls = {}
Http.transport = function(reqt)
    calls[#calls + 1] = reqt.method .. " " .. reqt.url
    local body, code, headers = "", 200, {}
    if reqt.url:find("process%-login") then
        code, headers = 302, { location = "https://katalog.tsk.sk/library/vkmr/", ["set-cookie"] =
            "JSESSIONID=abc; Path=/; Expires=Wed, 21 Oct 2035 07:28:00 GMT, other=1; Path=/" }
    elseif reqt.url:find("request%-eborrow") then
        eq(reqt.headers["Cookie"], "JSESSIONID=abc; other=1", "cookies are sent")
        code, headers = 302, { location = "https://katalog.tsk.sk/library/vkmr/eborrow-success/5/-1" }
    else
        body = "<meta name='_csrf' content='T'>"
    end
    reqt.sink(body)
    return 1, code, headers
end
local k = Katalog.new("vkmr", "user", "pw")
eq(k:borrow(5), true, "borrow")

-- authors of a loan
eq(Palm.authors({ persons = { { type = "author", author = { firstname = "Mark", surname = "Aldridge" } },
    { type = "translator", author = { firstname = "X", surname = "Y" } } } }), "Mark Aldridge", "authors")

if failed == 0 then
    print("all tests passed")
else
    os.exit(1)
end
