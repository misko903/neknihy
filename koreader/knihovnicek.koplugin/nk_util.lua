-- Pure helpers (no KOReader dependencies, so they can be tested with plain Lua).
local Util = {}

function Util.trim(s)
    return (tostring(s or ""):gsub("^%s+", ""):gsub("%s+$", ""))
end

function Util.urlencode(s)
    return (tostring(s):gsub("[^%w%-_%.~]", function(c)
        return string.format("%%%02X", c:byte())
    end))
end

function Util.urldecode(s)
    s = s:gsub("%+", " ")
    return (s:gsub("%%(%x%x)", function(h)
        return string.char(tonumber(h, 16))
    end))
end

--- "a=1&b=x%20y" -> {a = "1", b = "x y"} (the first value of a key wins).
function Util.parseQuery(query)
    local t = {}
    for pair in (query or ""):gmatch("[^&]+") do
        local k, v = pair:match("^([^=]*)=?(.*)$")
        k = Util.urldecode(k)
        if t[k] == nil then
            t[k] = Util.urldecode(v)
        end
    end
    return t
end

--- {b = "2", a = "1"} -> "a=1&b=2" (sorted, so the result is stable).
function Util.encodeForm(form)
    local keys = {}
    for k in pairs(form) do
        keys[#keys + 1] = k
    end
    table.sort(keys)
    local parts = {}
    for _, k in ipairs(keys) do
        parts[#parts + 1] = Util.urlencode(k) .. "=" .. Util.urlencode(form[k])
    end
    return table.concat(parts, "&")
end

local function utf8char(c)
    if c < 0x80 then
        return string.char(c)
    elseif c < 0x800 then
        return string.char(0xC0 + math.floor(c / 0x40), 0x80 + c % 0x40)
    elseif c < 0x10000 then
        return string.char(0xE0 + math.floor(c / 0x1000),
            0x80 + math.floor(c / 0x40) % 0x40, 0x80 + c % 0x40)
    end
    return string.char(0xF0 + math.floor(c / 0x40000),
        0x80 + math.floor(c / 0x1000) % 0x40,
        0x80 + math.floor(c / 0x40) % 0x40, 0x80 + c % 0x40)
end

local NAMED = { amp = "&", lt = "<", gt = ">", quot = '"', apos = "'", nbsp = " " }

function Util.decodeEntities(s)
    s = s:gsub("&#(%d+);", function(n) return utf8char(tonumber(n)) end)
    s = s:gsub("&#[xX](%x+);", function(n) return utf8char(tonumber(n, 16)) end)
    s = s:gsub("&(%a+);", function(n) return NAMED[n] end)
    return s
end

--- HTML fragment -> plain text (tags dropped, entities decoded, spaces squeezed).
function Util.text(fragment)
    local s = (fragment or ""):gsub("<[^>]*>", " ")
    s = Util.decodeEntities(s)
    s = s:gsub("\194\160", " ")
    s = s:gsub("%s+", " ")
    return Util.trim(s)
end

--- Value of an HTML attribute in a single tag, or nil.
function Util.attr(tag, name)
    name = name:gsub("%-", "%%-")
    local v = tag:match("%s" .. name .. '%s*=%s*"([^"]*)"')
        or tag:match("%s" .. name .. "%s*=%s*'([^']*)'")
    return v and Util.decodeEntities(v) or nil
end

function Util.hasClass(tag, class)
    local classes = Util.attr(tag, "class")
    return classes ~= nil and (" " .. classes .. " "):find(" " .. class .. " ", 1, true) ~= nil
end

-- days since 1970-01-01 of a civil date (proleptic Gregorian)
local function daysFromCivil(y, m, d)
    if m <= 2 then
        y = y - 1
    end
    local era = math.floor(y / 400)
    local yoe = y - era * 400
    local mp = (m + 9) % 12
    local doy = math.floor((153 * mp + 2) / 5) + d - 1
    local doe = yoe * 365 + math.floor(yoe / 4) - math.floor(yoe / 100) + doy
    return era * 146097 + doe - 719468
end

--- "2026-01-31T12:00:00Z", "2026-01-31" or "20260131" -> Unix time (UTC), or nil.
function Util.parseTime(s)
    if type(s) ~= "string" then
        return nil
    end
    local y, m, d, rest = s:match("^(%d%d%d%d)%-(%d%d)%-(%d%d)(.*)$")
    if not y then
        y, m, d, rest = s:match("^(%d%d%d%d)(%d%d)(%d%d)(.*)$")
    end
    if not y then
        return nil
    end
    local H, M, S = rest:match("[T ](%d%d):(%d%d):?(%d*)")
    local t = daysFromCivil(tonumber(y), tonumber(m), tonumber(d)) * 86400
        + (tonumber(H) or 0) * 3600 + (tonumber(M) or 0) * 60 + (tonumber(S) or 0)
    local sign, oh, om = rest:match("([+-])(%d%d):?(%d%d)$")
    if sign then
        local offset = tonumber(oh) * 3600 + tonumber(om) * 60
        t = (sign == "+") and (t - offset) or (t + offset)
    end
    return t
end

--- File-system safe name.
function Util.safeName(s, fallback)
    s = Util.trim((s or ""):gsub('[/\\:%*%?"<>|%c]', "_"))
    s = s:gsub("^%.+", "")
    if s == "" then
        return fallback or "book"
    end
    return s
end

return Util
