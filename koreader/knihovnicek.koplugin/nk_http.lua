-- Minimal HTTP(S) client on top of LuaSocket / LuaSec with a cookie jar.
local ltn12 = require("ltn12")
local Util = require("nk_util")

local Http = {}

Http.USER_AGENT = "Knihovnicek-KOReader (+https://github.com/misko903/neknihy)"
-- Tests replace this: function(reqt) -> ok, code, headers, status
Http.transport = nil

local function socketutil()
    local ok, su = pcall(require, "socketutil")
    return ok and su or nil
end

local function send(reqt)
    if Http.transport then
        return Http.transport(reqt)
    end
    if reqt.url:match("^https:") then
        return require("ssl.https").request(reqt)
    end
    return require("socket.http").request(reqt)
end

--- A session is just a cookie jar.
function Http.newSession()
    return { cookies = {} }
end

local function cookieHeader(session)
    local parts = {}
    for name, value in pairs(session.cookies) do
        parts[#parts + 1] = name .. "=" .. value
    end
    table.sort(parts)
    return table.concat(parts, "; ")
end

--- LuaSocket joins several Set-Cookie headers with ", "; a cookie starts
--- after ", " with name=value (an Expires date has no "=" right after its
--- first word, so it is not mistaken for one).
local function storeCookies(session, header)
    if type(header) ~= "string" then
        return
    end
    for name, value in (", " .. header):gmatch(", ([%w_%.%-]+)=([^;,]*)") do
        if value == "" or value == "deleted" then
            session.cookies[name] = nil
        else
            session.cookies[name] = value
        end
    end
end

--- Sink that writes into an open file (honours KOReader's total timeout).
function Http.fileSink(fh)
    local su = socketutil()
    if su and su.file_sink then
        return su.file_sink(fh)
    end
    return ltn12.sink.file(fh)
end

--- opts: url, method, headers, body, form (table, sent urlencoded), sink,
--- redirect (default true), timeout, total_timeout (seconds).
--- Returns {code, headers, body} or nil, error message.
function Http.request(session, opts)
    local headers = { ["User-Agent"] = Http.USER_AGENT }
    for k, v in pairs(opts.headers or {}) do
        headers[k] = v
    end
    local body = opts.body
    if opts.form then
        body = Util.encodeForm(opts.form)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    end
    if body then
        headers["Content-Length"] = tostring(#body)
    end
    if session then
        local cookies = cookieHeader(session)
        if cookies ~= "" then
            headers["Cookie"] = cookies
        end
    end

    local su = socketutil()
    local chunks = {}
    local sink = opts.sink
    if not sink then
        sink = (su and su.table_sink) and su.table_sink(chunks) or ltn12.sink.table(chunks)
    end
    local reqt = {
        url = opts.url,
        method = opts.method or (body and "POST" or "GET"),
        headers = headers,
        sink = sink,
        source = body and ltn12.source.string(body) or nil,
    }
    -- LuaSec rejects a "redirect" field ("redirect not supported"), so it is
    -- never sent. LuaSocket follows a redirect only while nredirects < 5,
    -- which switches it off without that field.
    if opts.redirect == false then
        reqt.nredirects = 5
    end

    if su then
        pcall(su.set_timeout, su, opts.timeout or 30, opts.total_timeout or 120)
    end
    local called, sent, code, rheaders = pcall(send, reqt)
    if su then
        pcall(su.reset_timeout, su)
    end
    if not called then
        return nil, tostring(sent)
    end
    -- LuaSocket: sent == 1 and code is the status; on failure sent == nil and code the error
    if not sent or type(code) ~= "number" then
        return nil, tostring(code or "connection failed")
    end
    rheaders = rheaders or {}
    if session then
        storeCookies(session, rheaders["set-cookie"])
    end
    return { code = code, headers = rheaders, body = table.concat(chunks) }
end

--- Request that expects JSON. Returns the decoded value or nil, error message.
function Http.json(session, opts)
    local r, err = Http.request(session, opts)
    if not r then
        return nil, err
    end
    if r.code >= 300 then
        return nil, "HTTP " .. r.code, r
    end
    local JSON = require("json")
    local ok, data = pcall(JSON.decode, r.body)
    if not ok or data == nil then
        return nil, "invalid JSON", r
    end
    return data, nil, r
end

return Http
