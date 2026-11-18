--[[
Knihovnicek for KOReader: borrow e-books from Czech and Slovak libraries that
lend through Palmknihy, download them to the reader and delete expired ones.
Standalone: it talks to Palmknihy and the library catalogue directly.
]]

local DataStorage = require("datastorage")
local Device = require("device")
local ButtonDialog = require("ui/widget/buttondialog")
local ConfirmBox = require("ui/widget/confirmbox")
local InfoMessage = require("ui/widget/infomessage")
local InputDialog = require("ui/widget/inputdialog")
local LuaSettings = require("luasettings")
local Menu = require("ui/widget/menu")
local NetworkMgr = require("ui/network/manager")
local UIManager = require("ui/uimanager")
local WidgetContainer = require("ui/widget/container/widgetcontainer")
local lfs = require("libs/libkoreader-lfs")

local Katalog = require("nk_katalog")
local L = require("nk_i18n").T
local Palm = require("nk_palmknihy")
local Util = require("nk_util")

local Screen = Device.screen

local Knihovnicek = WidgetContainer:extend{
    name = "knihovnicek",
    is_doc_only = false,
}

-- --- settings and state -----------------------------------------------------

function Knihovnicek:init()
    local dir = DataStorage:getSettingsDir()
    local path = dir .. "/knihovnicek.lua"
    -- the plugin used to be called "neknihy": keep its settings and loans
    if not lfs.attributes(path, "mode") and lfs.attributes(dir .. "/neknihy.lua", "mode") then
        local old = LuaSettings:open(dir .. "/neknihy.lua")
        self.settings = LuaSettings:open(path)
        for key, value in pairs(old.data) do
            self.settings:saveSetting(key, value)
        end
        self.settings:flush()
    else
        self.settings = LuaSettings:open(path)
    end
    self.ui.menu:registerToMainMenu(self)
end

function Knihovnicek:get(key, default)
    local value = self.settings:readSetting(key)
    if value == nil then
        return default
    end
    return value
end

function Knihovnicek:set(key, value)
    self.settings:saveSetting(key, value)
    self.settings:flush()
end

--- Loans known to the plugin, keyed by Palmknihy ID:
--- { title, authors, end_time, palm_id, variant_palm_id, status, filename, dir }
--- status: "new" (not downloaded), "ok" (downloaded), "deleted" (hidden until it expires)
function Knihovnicek:books()
    local books = self:get("books")
    if type(books) ~= "table" then
        books = {}
    end
    return books
end

function Knihovnicek:saveBooks(books)
    self:set("books", books or self:books())
end

function Knihovnicek:downloadDir()
    local dir = self:get("dir")
    if not dir or dir == "" then
        local home = G_reader_settings:readSetting("home_dir") or DataStorage:getDataDir()
        dir = home:gsub("/$", "") .. "/Knihovnicek"
    end
    lfs.mkdir(dir)
    return dir
end

function Knihovnicek:filePath(book)
    if not book.filename or book.filename == "" then
        return nil
    end
    return (book.dir or self:downloadDir()) .. "/" .. book.filename
end

function Knihovnicek:fileExists(book)
    local path = self:filePath(book)
    return path ~= nil and lfs.attributes(path, "mode") == "file"
end

function Knihovnicek:isExpired(book)
    local t = Util.parseTime(book.end_time)
    return t ~= nil and t < os.time()
end

function Knihovnicek:removeFile(book)
    if self:fileExists(book) then
        os.remove(self:filePath(book))
    end
    book.filename = ""
end

-- --- helpers ------------------------------------------------------------------

local function refreshFileManager()
    local ok, FileManager = pcall(require, "apps/filemanager/filemanager")
    if ok and FileManager and FileManager.instance then
        pcall(FileManager.instance.onRefresh, FileManager.instance)
    end
end

local function errorText(err)
    if type(err) == "string" and err:sub(1, 5) == "login" then
        return L("bad_login") .. "\n" .. err
    elseif err == "pending" then
        return L("pending")
    elseif err == "wantread" or err == "timeout" then
        return L("timeout")
    end
    return L("error", tostring(err))
end

function Knihovnicek:showMessage(text)
    UIManager:show(InfoMessage:new{ text = text })
end

--- Run fn() (it returns result, error) with a "busy" message once the network
--- is up, then call done(result, error).
function Knihovnicek:work(message, fn, done)
    NetworkMgr:runWhenOnline(function()
        local info = InfoMessage:new{ text = message, dismissable = false }
        UIManager:show(info)
        UIManager:forceRePaint()
        local ok, result, err = pcall(fn)
        UIManager:close(info)
        if not ok then
            result, err = nil, tostring(result)
        end
        done(result, err)
    end)
end

function Knihovnicek:askText(title, value, is_password, callback)
    local dialog
    dialog = InputDialog:new{
        title = title,
        input = value or "",
        text_type = is_password and "password" or nil,
        buttons = { {
            { text = L("cancel"), id = "close", callback = function()
                UIManager:close(dialog)
            end },
            { text = L("save"), is_enter_default = true, callback = function()
                local text = Util.trim(dialog:getInputText())
                UIManager:close(dialog)
                callback(text)
            end },
        } },
    }
    UIManager:show(dialog)
    dialog:onShowKeyboard()
end

function Knihovnicek:palm()
    return Palm.new(self:get("email", ""), self:get("password", ""))
end

function Knihovnicek:requireLogin()
    if self:get("email", "") == "" or self:get("password", "") == "" then
        self:showMessage(L("no_login"))
        return false
    end
    return true
end

-- The library login name is the same e-mail as for Palmknihy.
function Knihovnicek:catUser()
    return self:get("email", "")
end

function Knihovnicek:katalogClient()
    local lib = self:get("library", "vkmr")
    local user, pass = self:catUser(), self:get("cat_pass", "")
    local k = self.katalog
    if not k or k.library ~= lib or k.username ~= user or k.password ~= pass then
        k = Katalog.new(lib, user, pass)
        self.katalog = k
    end
    return k
end

-- --- loans --------------------------------------------------------------------

--- Fetch the loans from Palmknihy and merge them into the stored list.
function Knihovnicek:fetchLoans(done)
    self:work(L("loading"), function()
        local api = self:palm()
        local ok, err = api:login()
        if not ok then
            return nil, err
        end
        return api:rents()
    end, function(rents, err)
        if not rents then
            self:showMessage(errorText(err))
            return
        end
        local books = self:books()
        for _i, rent in ipairs(rents) do
            local key = tostring(rent.palm_id)
            local book = books[key]
            if not book then
                book = { status = "new", filename = "" }
                books[key] = book
            end
            book.palm_id = rent.palm_id
            book.variant_palm_id = rent.variant_palm_id
            book.title = rent.title or book.title or "?"
            book.authors = Palm.authors(rent)
            book.end_time = rent.end_time or book.end_time
        end
        self:saveBooks(books)
        done()
    end)
end

function Knihovnicek:visibleBooks()
    local list = {}
    for _key, book in pairs(self:books()) do
        if book.status ~= "deleted" then
            list[#list + 1] = book
        end
    end
    table.sort(list, function(a, b)
        return (a.title or ""):lower() < (b.title or ""):lower()
    end)
    return list
end

function Knihovnicek:statusText(book)
    local parts = {}
    if self:fileExists(book) then
        parts[#parts + 1] = L("downloaded")
    elseif not self:isExpired(book) then
        parts[#parts + 1] = L("new")
    end
    local t = Util.parseTime(book.end_time)
    if self:isExpired(book) then
        parts[#parts + 1] = L("expired")
    elseif t then
        parts[#parts + 1] = L("expires", os.date("%d.%m.%Y", t))
    end
    return table.concat(parts, " · ")
end

function Knihovnicek:showLoans()
    if self.loans_menu then
        UIManager:close(self.loans_menu)
        self.loans_menu = nil
    end
    local items = {}
    for _i, book in ipairs(self:visibleBooks()) do
        local text = book.title or "?"
        if book.authors and book.authors ~= "" then
            text = text .. " — " .. book.authors
        end
        items[#items + 1] = {
            text = text,
            mandatory = self:statusText(book),
            callback = function()
                self:bookDialog(book)
            end,
        }
    end
    if #items == 0 then
        self:showMessage(L("no_loans"))
        return
    end
    self.loans_menu = Menu:new{
        title = L("my_loans"),
        item_table = items,
        is_borderless = true,
        is_popout = false,
        covers_fullscreen = true,
        width = Screen:getWidth(),
        height = Screen:getHeight(),
        close_callback = function()
            self.loans_menu = nil
        end,
    }
    UIManager:show(self.loans_menu)
end

function Knihovnicek:openLoans()
    if not self:requireLogin() then
        return
    end
    self:fetchLoans(function()
        self:showLoans()
    end)
end

function Knihovnicek:bookDialog(book)
    local dialog
    local function close()
        UIManager:close(dialog)
    end
    local buttons = {}
    if self:fileExists(book) then
        buttons[#buttons + 1] = { {
            text = L("open"),
            callback = function()
                close()
                if self.loans_menu then
                    UIManager:close(self.loans_menu)
                    self.loans_menu = nil
                end
                require("apps/reader/readerui"):showReader(self:filePath(book))
            end,
        } }
    elseif not self:isExpired(book) then
        buttons[#buttons + 1] = { {
            text = L("download"),
            callback = function()
                close()
                self:downloadBooks({ book })
            end,
        } }
    end
    buttons[#buttons + 1] = { {
        text = L("delete_file"),
        callback = function()
            close()
            UIManager:show(ConfirmBox:new{
                text = L("confirm_delete", book.title or "?"),
                ok_text = L("delete_file"),
                cancel_text = L("cancel"),
                ok_callback = function()
                    self:deleteBook(book)
                end,
            })
        end,
    } }
    buttons[#buttons + 1] = { { text = L("close"), callback = close } }
    dialog = ButtonDialog:new{ title = book.title or "?", buttons = buttons }
    UIManager:show(dialog)
end

--- Delete the file. An expired loan is forgotten; an active one stays hidden
--- ("deleted") so it is not downloaded again before it expires.
function Knihovnicek:deleteBook(book)
    self:removeFile(book)
    local books = self:books()
    if self:isExpired(book) then
        books[tostring(book.palm_id)] = nil
    else
        book.status = "deleted"
    end
    self:saveBooks(books)
    refreshFileManager()
    self:showLoans()
end

--- Delete the files of expired loans and forget them.
function Knihovnicek:cleanExpired()
    local books = self:books()
    local removed = 0
    for key, book in pairs(books) do
        if self:isExpired(book) then
            if self:fileExists(book) then
                self:removeFile(book)
                removed = removed + 1
            end
            books[key] = nil
        end
    end
    self:saveBooks(books)
    refreshFileManager()
    self:showMessage(removed > 0 and L("deleted_n", removed) or L("nothing_expired"))
end

function Knihovnicek:booksToDownload()
    local list = {}
    for _i, book in ipairs(self:visibleBooks()) do
        if not self:isExpired(book) and not self:fileExists(book) then
            list[#list + 1] = book
        end
    end
    return list
end

function Knihovnicek:downloadNew()
    if not self:requireLogin() then
        return
    end
    self:fetchLoans(function()
        local list = self:booksToDownload()
        if #list == 0 then
            self:showMessage(L("nothing_new"))
            return
        end
        self:downloadBooks(list)
    end)
end

--- Download the given loans one by one with a progress message.
function Knihovnicek:downloadBooks(list)
    if not self:requireLogin() then
        return
    end
    NetworkMgr:runWhenOnline(function()
        local api = self:palm()
        local ok, err = api:login()
        if not ok then
            self:showMessage(errorText(err))
            return
        end
        local dir = self:downloadDir()
        local done_n, failed = 0, {}
        local info
        for i, book in ipairs(list) do
            if info then
                UIManager:close(info)
            end
            info = InfoMessage:new{
                text = L("downloading", i, #list, book.title or "?"), dismissable = false,
            }
            UIManager:show(info)
            UIManager:forceRePaint()
            local called, filename, derr = pcall(api.download, api, book, dir)
            if called and filename then
                book.filename, book.dir, book.status = filename, dir, "ok"
                done_n = done_n + 1
            else
                failed[#failed + 1] = (book.title or "?") .. ": "
                    .. errorText(called and derr or filename)
            end
        end
        if info then
            UIManager:close(info)
        end
        self:saveBooks()
        refreshFileManager()
        local text = L("downloaded_n", done_n)
        if #failed > 0 then
            text = text .. "\n" .. L("failed_n", #failed, table.concat(failed, "\n"))
        end
        self:showMessage(text)
        if self.loans_menu then
            self:showLoans()
        end
    end)
end

-- --- catalogue ----------------------------------------------------------------

function Knihovnicek:searchDialog()
    self:askText(L("search_title"), self.last_query or "", false, function(query)
        if query ~= "" then
            self.last_query = query
            self:runSearch(query, 1)
        end
    end)
end

function Knihovnicek:runSearch(query, page)
    self:work(L("searching"), function()
        return self:katalogClient():search(query, page)
    end, function(data, err)
        if not data then
            self:showMessage(errorText(err))
        elseif #data.results == 0 and page == 1 then
            self:showMessage(L("no_results"))
        else
            self:showResults(data)
        end
    end)
end

function Knihovnicek:showResults(data)
    if self.results_menu then
        UIManager:close(self.results_menu)
        self.results_menu = nil
    end
    local items = {}
    if data.page > 1 then
        items[#items + 1] = {
            text = L("prev_page"),
            callback = function()
                self:runSearch(data.query, data.page - 1)
            end,
        }
    end
    for _i, r in ipairs(data.results) do
        items[#items + 1] = {
            text = r.title ~= "" and r.title or "?",
            mandatory = table.concat(r.authors, "; "),
            callback = function()
                self:confirmBorrow(r)
            end,
        }
    end
    if data.page < data.pages then
        items[#items + 1] = {
            text = L("next_page"),
            callback = function()
                self:runSearch(data.query, data.page + 1)
            end,
        }
    end
    self.results_menu = Menu:new{
        title = L("results", data.total),
        item_table = items,
        is_borderless = true,
        is_popout = false,
        covers_fullscreen = true,
        width = Screen:getWidth(),
        height = Screen:getHeight(),
        close_callback = function()
            self.results_menu = nil
        end,
    }
    UIManager:show(self.results_menu)
end

function Knihovnicek:confirmBorrow(result)
    if not result.ebook then
        self:showMessage(L("cannot_borrow"))
        return
    end
    if self:catUser() == "" or self:get("cat_pass", "") == "" then
        self:showMessage(L("no_cat_login"))
        return
    end
    UIManager:show(ConfirmBox:new{
        text = L("borrow_q", result.title),
        ok_text = L("borrow"),
        cancel_text = L("cancel"),
        ok_callback = function()
            self:borrow(result)
        end,
    })
end

function Knihovnicek:borrow(result)
    self:work(L("borrowing"), function()
        return self:katalogClient():borrow(result.work_id)
    end, function(ok, err)
        if ok then
            self:showMessage(L("borrowed"))
        elseif err == "login" then
            self:showMessage(L("bad_cat_login"))
        else
            self:showMessage(errorText(err))
        end
    end)
end

-- --- menu ---------------------------------------------------------------------

local function secret(value)
    return (value and value ~= "") and "●●●●" or L("unset")
end

function Knihovnicek:textSetting(key, label, is_password)
    return {
        text_func = function()
            local value = self:get(key, "")
            return label .. ": " .. (is_password and secret(value)
                or (value ~= "" and value or L("unset")))
        end,
        keep_menu_open = true,
        callback = function(touchmenu_instance)
            self:askText(label, is_password and "" or self:get(key, ""), is_password,
                function(text)
                    self:set(key, text)
                    self.katalog = nil
                    if touchmenu_instance then
                        touchmenu_instance:updateItems()
                    end
                end)
        end,
    }
end

function Knihovnicek:libraryItems()
    local items = {}
    for _i, lib in ipairs(Katalog.LIBRARIES) do
        local code, name = lib[1], lib[2]
        items[#items + 1] = {
            text = name,
            radio = true,
            checked_func = function()
                return self:get("library", "vkmr") == code
            end,
            callback = function()
                self:set("library", code)
                self.katalog = nil
            end,
        }
    end
    return items
end

function Knihovnicek:settingsItems()
    return {
        self:textSetting("email", L("email"), false),
        self:textSetting("password", L("password"), true),
        {
            text_func = function()
                local code = self:get("library", "vkmr")
                for _i, lib in ipairs(Katalog.LIBRARIES) do
                    if lib[1] == code then
                        return L("library") .. ": " .. lib[2]
                    end
                end
                return L("library") .. ": " .. code
            end,
            sub_item_table = self:libraryItems(),
        },
        self:textSetting("cat_pass", L("cat_pass"), true),
        {
            text_func = function()
                return L("folder") .. ": " .. self:downloadDir()
            end,
            keep_menu_open = true,
            callback = function(touchmenu_instance)
                local PathChooser = require("ui/widget/pathchooser")
                UIManager:show(PathChooser:new{
                    title = L("folder"),
                    select_directory = true,
                    select_file = false,
                    path = self:downloadDir(),
                    onConfirm = function(path)
                        self:set("dir", path)
                        if touchmenu_instance then
                            touchmenu_instance:updateItems()
                        end
                    end,
                })
            end,
        },
    }
end

function Knihovnicek:addToMainMenu(menu_items)
    menu_items.knihovnicek = {
        text = L("title"),
        sorting_hint = "tools",
        sub_item_table = {
            { text = L("my_loans"), callback = function() self:openLoans() end },
            { text = L("download_new"), callback = function() self:downloadNew() end },
            { text = L("clean_expired"), callback = function() self:cleanExpired() end },
            { text = L("search"), callback = function() self:searchDialog() end },
            { text = L("settings"), sub_item_table = self:settingsItems() },
        },
    }
end

return Knihovnicek
