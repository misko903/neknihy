-- Library catalogue katalog.tsk.sk (Trenčín self-governing region): search
-- and e-loans. Login and loans are HTML form posts protected by a _csrf
-- token bound to the session cookie.
local Http = require("nk_http")
local Util = require("nk_util")

local Katalog = {}
Katalog.__index = Katalog

Katalog.BASE = "https://katalog.tsk.sk"

-- code in /library/<code>/, name
Katalog.LIBRARIES = {
    { "ctk", "CTK v Myjave" },
    { "gmab", "GMAB v Trenčíne" },
    { "hnkpd", "HNK v Prievidzi" },
    { "hnm", "HNM v Prievidzi" },
    { "hvezdaren", "Hvezdáreň v Partizánskom" },
    { "kniznicapb", "PK v Považskej Bystrici" },
    { "pospb", "POS v Považskej Bystrici" },
    { "rkc", "RKC v Prievidzi" },
    { "tnos", "TN osvetové stredisko" },
    { "muzeumtn", "Trenčianske múzeum" },
    { "vkmr", "VKMR v Trenčíne" },
    { "muzeumpb", "VM v Považskej Bystrici" },
}

-- Catalogue "area" holding e-books; other libraries search everything and
-- the results are filtered.
local EBOOK_AREAS = { vkmr = 701 }
local AREA_ALL = -1
local PAGE_SIZE = 100

local function isRedirect(code)
    return code == 301 or code == 302 or code == 303 or code == 307 or code == 308
end

local function isBotCheck(r)
    local loc = r.headers and r.headers["location"] or ""
    return loc:find("i-am-not-a-robot", 1, true) ~= nil or loc:find("i-am-a-robot", 1, true) ~= nil
end

function Katalog.new(library, username, password)
    local self = setmetatable({}, Katalog)
    self.library = library or "vkmr"
    self.username = username
    self.password = password
    self.session = Http.newSession()
    self.logged_in = false
    return self
end

function Katalog:_url(path)
    local rel = path:gsub("^/", "")
    return string.format("%s/library/%s/%s", Katalog.BASE, self.library, rel)
end

-- The anti-bot page only runs a tiny script: it fetches a token and keeps it
-- in the AuthToken cookie. There is no proof of work to compute.
function Katalog:_passBotCheck()
    local data, err = Http.json(self.session, { url = Katalog.BASE .. "/jsapi/pow/token" })
    if not data or not data.token then
        return nil, err or "bot check"
    end
    self.session.cookies["AuthToken"] = data.token
    return true
end

--- GET that passes the anti-bot check once if the catalogue asks for it.
function Katalog:_get(url)
    local r, err = Http.request(self.session, { url = url, redirect = false })
    if not r then
        return nil, err
    end
    if isBotCheck(r) then
        local ok, berr = self:_passBotCheck()
        if not ok then
            return nil, berr
        end
        r, err = Http.request(self.session, { url = url, redirect = false })
        if not r then
            return nil, err
        end
        if isBotCheck(r) then
            return nil, "bot check"
        end
    end
    return r
end

--- _csrf token from <input> or <meta> of a catalogue page.
function Katalog.findCsrf(html)
    for tag in html:gmatch("<input[^>]*>") do
        if Util.attr(tag, "name") == "_csrf" and (Util.attr(tag, "value") or "") ~= "" then
            return Util.attr(tag, "value")
        end
    end
    for tag in html:gmatch("<meta[^>]*>") do
        if Util.attr(tag, "name") == "_csrf" and (Util.attr(tag, "content") or "") ~= "" then
            return Util.attr(tag, "content")
        end
    end
    return nil
end

function Katalog:_csrf()
    local r, err = self:_get(self:_url("/"))
    if not r then
        return nil, err
    end
    local token = Katalog.findCsrf(r.body)
    if not token then
        return nil, "no _csrf token"
    end
    return token
end

--- true or nil, error ("login" = wrong credentials)
function Katalog:login()
    local token, err = self:_csrf()
    if not token then
        return nil, err
    end
    -- the form posts to /process-login at the site root, the library goes in "wd"
    local r, rerr = Http.request(self.session, {
        url = Katalog.BASE .. "/process-login",
        redirect = false,
        form = {
            wd = self.library, target_url = "", username = self.username,
            password = self.password, _csrf = token,
        },
    })
    if not r then
        return nil, rerr
    end
    if isBotCheck(r) then
        return nil, "bot check"
    end
    if not isRedirect(r.code) then
        return nil, "Catalogue login: HTTP " .. r.code
    end
    local loc = (r.headers["location"] or ""):lower()
    if loc:find("login", 1, true) or loc:find("error", 1, true) then
        return nil, "login"
    end
    self.logged_in = true
    return true
end

--- Borrow an e-book. Returns true or nil, error message.
function Katalog:borrow(work_id, retry)
    if not self.logged_in then
        local ok, err = self:login()
        if not ok then
            return nil, err
        end
    end
    local token, err = self:_csrf()
    if not token then
        return nil, err
    end
    local r, rerr = Http.request(self.session, {
        url = self:_url("request-eborrow"),
        redirect = false,
        form = { workId = tostring(work_id), providerId = "-1", agree = "on", _csrf = token },
    })
    if not r then
        return nil, rerr
    end
    local loc = r.headers["location"] or ""
    if isRedirect(r.code) and loc:find("eborrow-success", 1, true) then
        return true
    end
    if isBotCheck(r) then
        return nil, "bot check"
    end
    if loc:lower():find("login", 1, true) then
        self.logged_in = false
        if retry == nil then
            return self:borrow(work_id, false)
        end
        return nil, "login"
    end
    return nil, string.format("Loan failed: HTTP %d %s", r.code, loc)
end

-- --- search ---------------------------------------------------------------

local function totalOf(html)
    local info = html:match('class="pagination%-info"[^>]*>(.-)</div>')
    if not info then
        return nil
    end
    local text = Util.text(info)
    local num = text:match("celkom%s+(%d[%d%s%.,]*)") or text:match("celkem%s+(%d[%d%s%.,]*)")
        or text:match("of%s+(%d[%d%s%.,]*)")
    return num and tonumber((num:gsub("%D", ""))) or nil
end

local FIELDS = { Title = "title", Authors = "authors", Publish_info = "publish_info",
    Keywords = "keywords" }

local function contents(segment)
    local items = {}
    for inner in segment:gmatch('class="[^"]*showitem%-content[^"]*"[^>]*>(.-)</span>') do
        local t = Util.text(inner)
        if t ~= "" then
            items[#items + 1] = t
        end
    end
    return items
end

local function isAudio(work_type, keywords)
    local function audio(t)
        t = t:lower()
        return t:find("audio", 1, true) ~= nil and t:find("vizu", 1, true) == nil
    end
    if work_type ~= "" then
        return audio(work_type)
    end
    for _, k in ipairs(keywords) do
        if audio(k) then
            return true
        end
    end
    return false
end

local function parseRecord(block, tag, work_id)
    local r = { work_id = work_id, title = "", authors = {}, publish_info = "", keywords = {},
        work_type = "", ebook = false, audiobook = false, palm_id = nil, source = "" }
    local wt = block:match("<span[^>]*work%-type[^>]*>")
    if wt then
        r.work_type = Util.trim((Util.attr(wt, "data-original-title") or Util.attr(wt, "title")
            or ""):gsub("%-%s*$", ""))
    end
    local avail = block:match('<div[^>]*work%-availability[^>]*>')
    if avail then
        local title = Util.attr(avail, "data-original-title") or Util.attr(avail, "title") or ""
        r.source = Util.trim(title:match(":(.*)$") or "")
    end
    r.ebook = block:find('href="[^"]*/eborrow/') ~= nil
    local palm = block:match("palmknihy%.cz/kniha/(%d+)")
    r.palm_id = palm and tonumber(palm) or nil

    -- field segments: from <div class="Title|Authors|..."> to the next field
    local marks = {}
    for pos, class in block:gmatch('()<div[^>]*class="(%a[%w_]*)"') do
        if FIELDS[class] then
            marks[#marks + 1] = { pos = pos, name = FIELDS[class] }
        end
    end
    local avail_pos = block:find("work%-availability")
    for i, m in ipairs(marks) do
        local stop = marks[i + 1] and marks[i + 1].pos or (avail_pos or #block + 1)
        if avail_pos and avail_pos < m.pos then
            stop = #block + 1
        end
        local segment = block:sub(m.pos, stop - 1)
        if m.name == "title" then
            if r.title == "" then
                r.title = Util.text(segment)
            end
        elseif m.name == "publish_info" then
            r.publish_info = r.publish_info ~= "" and r.publish_info or (contents(segment)[1] or "")
        else
            for _, t in ipairs(contents(segment)) do
                local list = r[m.name]
                local dup = false
                for _, x in ipairs(list) do
                    dup = dup or x == t
                end
                if not dup then
                    list[#list + 1] = t
                end
            end
        end
    end
    r.audiobook = isAudio(r.work_type, r.keywords)
    return r
end

--- Parse a search result page: { total = n, results = { ... } }
function Katalog.parseSearch(html)
    local starts = {}
    local pos = 1
    while true do
        local s, e = html:find('<div[^>]*data%-work%-id="%d+"[^>]*>', pos)
        if not s then
            break
        end
        local tag = html:sub(s, e)
        if Util.hasClass(tag, "result") then
            starts[#starts + 1] = { s = s, tag = tag, id = tonumber(Util.attr(tag, "data-work-id")) }
        end
        pos = e + 1
    end
    local results = {}
    for i, st in ipairs(starts) do
        local stop = starts[i + 1] and starts[i + 1].s - 1 or #html
        results[#results + 1] = parseRecord(html:sub(st.s, stop), st.tag, st.id)
    end
    return { total = totalOf(html) or #results, results = results }
end

--- Search e-books (no login needed). Returns { total, results, pages, page } or nil, error.
function Katalog:search(query, page)
    page = page or 1
    local area = EBOOK_AREAS[self.library] or AREA_ALL
    local url = self:_url("search") .. "?" .. Util.encodeForm({
        field = 0, q = query, area = area, allsrc = "on", pageSize = PAGE_SIZE, page = page,
    })
    local r, err = self:_get(url)
    if not r then
        return nil, err
    end
    if r.code >= 300 then
        return nil, "HTTP " .. r.code
    end
    local data = Katalog.parseSearch(r.body)
    local results = {}
    for _, x in ipairs(data.results) do
        -- the whole collection holds printed books: keep only what can be e-borrowed
        if not x.audiobook and (x.ebook or area ~= AREA_ALL) then
            results[#results + 1] = x
        end
    end
    local pages = math.max(1, math.ceil(data.total / PAGE_SIZE))
    return { total = pages == 1 and #results or data.total, results = results,
        pages = pages, page = page, query = query }
end

return Katalog
