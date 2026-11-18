-- Palmknihy mobile-app API: login, list of loans, download of e-books.
local Http = require("nk_http")
local Util = require("nk_util")

local Palm = {}
Palm.__index = Palm

local BASE = "https://prodapp.palmknihy.cz"

function Palm.new(email, password)
    return setmetatable({ email = email, password = password, token = nil }, Palm)
end

function Palm:_headers()
    return {
        ["Content-Type"] = "application/json",
        ["Accept"] = "application/json",
        ["Authorization"] = "Bearer " .. tostring(self.token),
    }
end

--- true or nil, error message
function Palm:login()
    local JSON = require("json")
    local r, err = Http.request(nil, {
        url = BASE .. "/users/login/",
        method = "POST",
        headers = { ["Content-Type"] = "application/json", ["Accept"] = "application/json" },
        body = JSON.encode({ email = self.email, password = self.password }),
        timeout = 60, total_timeout = 120,
    })
    if not r then
        return nil, err
    end
    if r.code >= 500 then
        return nil, "Palmknihy: HTTP " .. r.code
    end
    if r.code >= 300 then
        return nil, string.format("login: HTTP %d %s", r.code, r.body:sub(1, 200))
    end
    local ok, data = pcall(JSON.decode, r.body)
    if not ok or type(data) ~= "table" or not data.token then
        return nil, "Palmknihy: invalid answer"
    end
    self.token = data.token
    return true
end

--- List of loaned e-books (audiobooks are skipped: KOReader cannot play them).
function Palm:rents()
    -- the server refreshes the loans from the libraries first, which can take a while
    local data, err = Http.json(nil, {
        url = BASE .. "/rents/?refresh", headers = self:_headers(),
        timeout = 120, total_timeout = 300,
    })
    if not data then
        return nil, err
    end
    local books = {}
    for _, book in ipairs(data.results or {}) do
        if book.item_type ~= "audiobook" and book.palm_id ~= nil then
            books[#books + 1] = book
        end
    end
    return books
end

--- "Firstname Surname, ..." from the loan's "persons".
function Palm.authors(book)
    local names, seen = {}, {}
    for _, person in ipairs(book.persons or {}) do
        local a = person.author
        if person.type == "author" and type(a) == "table" then
            local full = Util.trim((a.firstname or "") .. " " .. (a.surname or ""))
            if full ~= "" and not seen[full] then
                seen[full] = true
                names[#names + 1] = full
            end
        end
    end
    return table.concat(names, ", ")
end

local function exists(path)
    local fh = io.open(path, "rb")
    if fh then
        fh:close()
        return true
    end
    return false
end

local function extension(headers)
    local ctype = (headers["content-type"] or ""):lower()
    if ctype:find("/epub", 1, true) then
        return ".epub"
    elseif ctype:find("/pdf", 1, true) then
        return ".pdf"
    end
    local disposition = headers["content-disposition"] or ""
    local ext = disposition:match('filename="?[^";]+(%.[^";%.]+)"?')
    if ext then
        return ext:lower()
    end
    return ".epub"
end

--- Download the loan into dir. Returns the file name, or nil, error message
--- ("pending" when Palmknihy is still preparing the file).
function Palm:download(book, dir)
    local info, err = Http.json(nil, {
        url = string.format("%s/v3/books/file/%s/%s/", BASE, book.palm_id,
            book.variant_palm_id or 1),
        headers = self:_headers(),
    })
    if not info then
        return nil, err
    end
    if not info.status or not info.file then
        return nil, "pending"
    end
    local query = info.file:match("%?(.*)$") or ""
    local params = Util.parseQuery(query)
    local basename = Util.safeName(params.filename, "book-" .. tostring(book.palm_id))
    local url = info.file
    if params.url and params.url:lower():find("^https://cdn%.palmknihy%.cz") then
        url = params.url
    end

    local part = dir .. "/" .. basename .. ".part"
    local fh, ferr = io.open(part, "wb")
    if not fh then
        return nil, tostring(ferr)
    end
    local r, rerr = Http.request(nil, {
        url = url, sink = Http.fileSink(fh), total_timeout = 600,
    })
    -- the sink closes the file when the body is complete; closing twice is harmless
    pcall(fh.close, fh)
    if not r then
        os.remove(part)
        return nil, rerr
    end
    if r.code >= 300 then
        os.remove(part)
        return nil, "HTTP " .. r.code
    end
    local filename = basename .. extension(r.headers)
    local final = dir .. "/" .. filename
    if exists(final) then
        os.remove(final)
    end
    local ok, mverr = os.rename(part, final)
    if not ok then
        os.remove(part)
        return nil, tostring(mverr)
    end
    return filename
end

return Palm
