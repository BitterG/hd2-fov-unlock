-- HD2-Addon: mods/hd2/fov_unlock
--
-- FOV Unlock — 让《绝地潜兵2》的垂直视野（Vertical Field of View）突破 90 上限。
--
-- 背景（全部来自对本机 build 的实测，不是猜的）：
--   * 游戏把垂直视野存在纯文本设置文件
--       %APPDATA%\Arrowhead\Helldivers2\user_settings.config
--     里，键名就是 vertical_fov（默认 55）。
--   * 设置菜单里的滑条只给 45..90，而且引擎在**读取设置时**会把值硬夹到
--     [45, 90]：把文件改成 130 再进游戏，退出后游戏会把 90 写回去。
--     （实机验证：vertical_fov 从 130 被改写回 90。）
--   * 夹取发生在 game.dll 的设置反序列化里，反汇编如下：
--
--         movss xmm2, [rbx+0x2c]      ; 设置结构里的 vertical_fov
--         lea   rdx, "vertical_fov"   ; 键名
--         call  [r8+0x88]             ; 从配置后端读一个 float
--         movss xmm1, [45.0f]
--         comiss xmm1, xmm0
--         ja    +0x0c                 ; 下限：<45 就取 45
--         movss xmm1, [90.0f]
--         minss xmm1, xmm0            ; ★ 上限：>90 就取 90
--         movss [rbx+0x2c], xmm1
--
-- 本 mod 做的事：
--   1. 在 game.dll 里按**指令形状**定位这条 minss（同时校验它引用的两个常量
--      确实是 45.0f 和 90.0f），把它换成 movaps xmm1, xmm0 —— 上限消失，
--      下限 45 保留。签名里所有位移都是通配的，所以换构建/换基址也认得出来。
--   2. 把 fov_unlock.cfg 里你要的值写进 user_settings.config 的
--      vertical_fov（先备份），这样只要引擎再读一次设置就是你的值。
--   3. 把结论写进 FOV_UNLOCK_STATUS.txt，第一行就是结论。
--
-- 配置：%APPDATA%\Arrowhead\Helldivers2\fov_unlock.cfg
-- 日志：%LOCALAPPDATA%\CowboyBingus\Helldivers2\FOVUnlock.log
-- 状态：%LOCALAPPDATA%\CowboyBingus\Helldivers2\FOV_UNLOCK_STATUS.txt
--
-- 注意：这是本地内存补丁。别人看不到你的视野；联机用有反作弊风险。

local MOD_NAME = 'FOVUnlock'
local VERSION = '2.2.0'

-- ---------------------------------------------------------------------------
-- 单实例守卫
-- ---------------------------------------------------------------------------
local existing = rawget(_G, MOD_NAME)
if type(existing) == 'table' then
    return existing
end

local M = {
    version = VERSION,
    status = 'starting',
    phase = 'init',
    clamp_found = false,
    clamp_rva = nil,
    clamp_verified = nil,
    patch_applied = false,
    config_written = false,
    desired_fov = nil,
    refusals = 0,
    notes = {},
}
_G[MOD_NAME] = M

-- ---------------------------------------------------------------------------
-- 路径工具
-- ---------------------------------------------------------------------------
local function appdata_dir()
    local d = os.getenv('APPDATA')
    if not d then return nil end
    return d .. '/Arrowhead/Helldivers2'
end

local function localappdata_dir()
    local d = os.getenv('LOCALAPPDATA')
    if not d then return nil end
    return d .. '/CowboyBingus/Helldivers2'
end

local log_handle = nil

local function ensure_log()
    if log_handle then return log_handle end
    local dir = localappdata_dir()
    if not dir then return nil end
    pcall(function() os.execute('mkdir "' .. dir .. '" >NUL 2>NUL') end)
    local ok, h = pcall(io.open, dir .. '/FOVUnlock.log', 'a')
    if ok and h then log_handle = h end
    return log_handle
end

local last_log = nil

local function emit(message)
    message = tostring(message)
    M.status = message
    local h = ensure_log()
    if h then
        pcall(function()
            h:write(os.date('!%Y-%m-%dT%H:%M:%SZ') .. ' [v' .. VERSION .. '] ' .. message .. '\n')
            h:flush()
        end)
    end
    -- 限流：同一条消息不重复刷屏
    if message ~= last_log then
        pcall(print, '[FOVUnlock] ' .. message)
        last_log = message
    end
end

local function note(message)
    M.notes[#M.notes + 1] = tostring(message)
end

-- ---------------------------------------------------------------------------
-- 6.36：不要用 FFI 类型双关去读 float。
--
-- `ffi.copy` 进一个 `float[1]` 再读 [0] 是类型双关；LuaJIT 在热循环里会把那次
-- float 读缓存进寄存器，于是"同一个函数连续调用、结果时对时错"。本 mod 的扫描器
-- 正好要在几十万条指令里反复读 float，所以这里用纯 Lua 解 IEEE-754，并且**开机自检**：
-- 解错了就停手，绝不带着坏解码器去写内存。
-- ---------------------------------------------------------------------------
local function bits_to_f32(bits)
    local sign = 1
    if bits >= 2147483648 then
        sign = -1
        bits = bits - 2147483648
    end
    local exp = math.floor(bits / 8388608)
    local mant = bits - exp * 8388608
    if exp == 255 then
        if mant == 0 then return sign * math.huge end
        return 0 / 0
    end
    if exp == 0 then
        if mant == 0 then return sign * 0.0 end
        return sign * mant * 2 ^ -149
    end
    return sign * (1 + mant / 8388608) * 2 ^ (exp - 127)
end

local FLOAT_SELF_CHECK = {
    { 0x3F800000, 1.0 },
    { 0x43AF0000, 350.0 },
    { 0x42C80000, 100.0 },
    { 0x3E800000, 0.25 },
    { 0x42340000, 45.0 },
    { 0x42B40000, 90.0 },
}

local function float_decoder_ok()
    for i = 1, #FLOAT_SELF_CHECK do
        local bits, want = FLOAT_SELF_CHECK[i][1], FLOAT_SELF_CHECK[i][2]
        local got = bits_to_f32(bits)
        if got ~= want then
            return false, string.format('0x%08X -> %s, want %s', bits, tostring(got), tostring(want))
        end
    end
    return true
end

-- float32 -> 4 字节小端。6.29：编码完必须自己解回来核对，否则"回读校验"只是
-- 证明"我编了两遍同样的东西"。解不回来就返回 nil，调用方拒绝写入。
local function f32_bytes(value)
    local v = value
    local sign = 0
    if v < 0 then
        sign = 0x80000000
        v = -v
    end
    local bits
    if v == 0 then
        bits = sign
    else
        local exp = math.floor(math.log(v) / math.log(2))
        local mant = v / (2 ^ exp) - 1
        local m = math.floor(mant * 8388608 + 0.5)
        if m >= 8388608 then
            m = m - 8388608
            exp = exp + 1
        end
        bits = sign + (exp + 127) * 8388608 + m
    end
    if bits_to_f32(bits) ~= value then return nil end
    return string.char(
        bits % 256,
        math.floor(bits / 256) % 256,
        math.floor(bits / 65536) % 256,
        math.floor(bits / 16777216) % 256)
end

-- ---------------------------------------------------------------------------
-- 状态文件：第一行永远是结论，用户一眼能看懂（技能 6.16）
-- ---------------------------------------------------------------------------
local function write_status()
    local dir = localappdata_dir()
    if not dir then return end
    pcall(function() os.execute('mkdir "' .. dir .. '" >NUL 2>NUL') end)
    local ok, h = pcall(io.open, dir .. '/FOV_UNLOCK_STATUS.txt', 'w')
    if not ok or not h then return end
    pcall(function()
        local first
        if M.settings_written and M.patch_applied then
            first = 'OK - 已写入 vertical_fov=' .. tostring(M.desired_fov)
                .. '（活设置对象 + 内存上限补丁）；'
                .. '注意本 mod 无法验证屏幕结果，请进游戏确认视野是否变宽'
        elseif M.settings_written then
            if M.code_clamp == 'enabled_by_config' then
                first = 'OK(部分) - 已写入活设置对象 vertical_fov='
                    .. tostring(M.desired_fov) .. '，但代码段上限补丁没打上'
            else
                first = 'OK - 已写入 vertical_fov=' .. tostring(M.desired_fov)
                    .. '（纯数据模式，game.dll 代码段未被改动）；'
                    .. '本 mod 无法验证屏幕结果，请进游戏确认视野是否变宽'
            end
        elseif M.patch_applied then
            first = 'PARTIAL - 只打上了内存上限补丁，没定位到活设置对象'
                .. '（本局可能看不到变化，见 settings_detail）'
        elseif M.clamp_found then
            first = 'FAILED - 找到夹取点但补丁没打上，见下面 refusals/notes'
        else
            first = 'FAILED - 既没定位到夹取点，也没定位到活设置对象（构建可能变了）'
        end
        h:write(first .. '\n')
        h:write('version=' .. VERSION .. '\n')
        h:write('phase=' .. tostring(M.phase) .. '\n')
        h:write('desired_fov=' .. tostring(M.desired_fov) .. '\n')
        h:write('clamp_found=' .. tostring(M.clamp_found) .. '\n')
        h:write('clamp_via=' .. tostring(M.clamp_via) .. '\n')
        h:write('clamp_rva=' .. tostring(M.clamp_rva) .. '\n')
        h:write('clamp_verified=' .. tostring(M.clamp_verified) .. '\n')
        h:write('float_decoder=' .. tostring(M.float_decoder) .. '\n')
        h:write('snapshot_fov=' .. tostring(M.snapshot_fov) .. '\n')
        h:write('config_source=' .. tostring(M.config_source) .. '\n')
        h:write('last_write=' .. tostring(M.last_write) .. '\n')
        h:write('last_write_verdict=' .. tostring(M.last_write_verdict) .. '\n')
        h:write('settings_phase=' .. tostring(M.settings_phase) .. '\n')
        h:write('settings_how=' .. tostring(M.settings_how) .. '\n')
        h:write('settings_before=' .. tostring(M.settings_before) .. '\n')
        h:write('settings_written=' .. tostring(M.settings_written) .. '\n')
        h:write('reasserts=' .. tostring(M.reasserts) .. '\n')
        h:write('settings_detail=' .. tostring(M.settings_detail) .. '\n')
        h:write('code_clamp=' .. tostring(M.code_clamp) .. '\n')
        h:write('patch_applied=' .. tostring(M.patch_applied) .. '\n')
        h:write('config_written=' .. tostring(M.config_written) .. '\n')
        h:write('game_dll_base=' .. tostring(M.game_dll_base) .. '\n')
        h:write('refusals=' .. tostring(M.refusals) .. '\n')
        h:write('loader_api=' .. tostring(M.loader_api) .. '\n')
        h:write('loader_version=' .. tostring(M.loader_version) .. '\n')
        h:write('notes:\n')
        for i = 1, #M.notes do
            h:write('  - ' .. M.notes[i] .. '\n')
        end
        h:write('\n说明：本补丁只改本进程内存。禁用本 mod（Purge）后完全还原。\n')
        h:close()
    end)
end

-- ---------------------------------------------------------------------------
-- 6.18 环境闸门：loader 太旧时 addon 照样会被加载，只是什么都做不成
-- ---------------------------------------------------------------------------
local function environment_gate()
    local loader = rawget(_G, 'CowboyBingusModLoader')
    if type(loader) == 'table' and tonumber(loader.api) then
        M.loader_api = tonumber(loader.api)
        M.loader_version = tonumber(loader.version)
        emit('loader api=' .. tostring(M.loader_api)
            .. ' version=' .. tostring(M.loader_version)
            .. ' (CowboyBingusModLoader)')
        if M.loader_api < 1 then
            M.phase = 'refused_old_loader'
            note('loader API ' .. tostring(M.loader_api) .. ' < 1：需要 Bingus Shared Loader v15 或更新')
            return false
        end
        return true
    end
    -- 读不到 global 不要直接拒绝（将来可能有别的 loader），退化去读日志首行
    local dir = localappdata_dir()
    if dir then
        local ok, line = pcall(function()
            local f = io.open(dir .. '/Logs/BingusSharedLoader.log', 'r')
            if not f then return nil end
            local t = f:read('*l')
            f:close()
            return t
        end)
        if ok and line then
            local v, a = line:match('loader%-v(%d+);%s*API%s*(%d+)')
            if a then
                M.loader_api, M.loader_version = tonumber(a), tonumber(v)
                emit('loader api=' .. tostring(M.loader_api)
                    .. ' version=' .. tostring(M.loader_version) .. ' (from log)')
                return M.loader_api >= 1
            end
        end
    end
    M.phase = 'refused_no_loader'
    note('找不到 CowboyBingusModLoader，也读不到 BingusSharedLoader.log：无法确认环境')
    return false
end

-- ---------------------------------------------------------------------------
-- 用户配置
-- ---------------------------------------------------------------------------
local DEFAULT_FOV = 100
local FOV_MIN = 45
local FOV_MAX = 175

local function parse_cfg(text)
    local cfg = {}
    for raw in text:gmatch('[^\r\n]+') do
        local line = raw:gsub('#.*$', ''):gsub(';.*$', '')
        local key, value = line:match('^%s*([%w_]+)%s*=%s*(.-)%s*$')
        if key then
            cfg[key:lower()] = value
        end
    end
    return cfg
end

-- 首次启动时写出来的模板。管理器只会部署 Addon/，不会把这个 cfg 放到
-- %APPDATA%，所以没有它就在这里补一份带注释的，省得玩家自己去猜键名。
local CONFIG_TEMPLATE = [[# FOV Unlock —— 绝地潜兵2 垂直视野解锁配置
#
# 这份文件是 mod 第一次启动时自动生成的，改完保存、重启游戏即可生效。
# 注释以 '#' 或 ';' 开头；键名不区分大小写。删掉它也能跑（用默认 fov = 100）。

enabled = true

# Options -> Visuals -> Vertical Field of View 那个滑条的值，本体只允许 45..90。
# 100 已经明显更宽；110-120 最舒服；超过 140 边缘拉伸很重。
fov = 100

# 是否把 fov 同步写进 user_settings.config（会先备份成 .fov_unlock.bak）。
write_config_file = true

# ===========================================================================
# patch_code_clamp —— 默认 false，强烈建议保持 false
#
# true  = 额外去改 game.dll 可执行页里那条把视野夹到 90 的指令。
#         改代码段是 nProtect GameGuard 最典型的特征，
#         **实测会触发 GG，然后游戏直接关闭。**
# false = 只做纯数据写入，game.dll 的代码段一个字节都不碰。
# ===========================================================================
patch_code_clamp = false
]]

local function load_config()
    -- patch_code_clamp 默认 false：改 game.dll 代码段会触发 GameGuard（实测会
    -- 直接关游戏），所以默认只走纯数据写入。
    local cfg = { enabled = true, fov = DEFAULT_FOV, write_config_file = true,
                  patch_code_clamp = false }
    local dir = appdata_dir()
    if not dir then return cfg, 'no_appdata' end
    local path = dir .. '/fov_unlock.cfg'
    local ok, text = pcall(function()
        local f = io.open(path, 'r')
        if not f then return nil end
        local t = f:read('*a')
        f:close()
        return t
    end)
    if not ok or not text then
        -- 没有就生成一份模板（写失败也不影响运行，照旧用默认值）
        local wrote = pcall(function()
            local f = io.open(path, 'w')
            if not f then error('cannot create') end
            f:write(CONFIG_TEMPLATE)
            f:close()
        end)
        if wrote then
            return cfg, 'generated'
        end
        return cfg, 'no_config_file'
    end
    local user = parse_cfg(text)
    if user.enabled ~= nil then
        cfg.enabled = (user.enabled == 'true' or user.enabled == '1')
    end
    if user.write_config_file ~= nil then
        cfg.write_config_file = (user.write_config_file == 'true' or user.write_config_file == '1')
    end
    if user.patch_code_clamp ~= nil then
        cfg.patch_code_clamp = (user.patch_code_clamp == 'true' or user.patch_code_clamp == '1')
    end
    if user.fov then
        local n = tonumber(user.fov)
        if n then cfg.fov = n end
    end
    return cfg, 'ok'
end

-- ---------------------------------------------------------------------------
-- 跨启动判定：引擎会不会把我们写进 user_settings.config 的值改掉？
--
-- 用户第一次实测已经证明：把 vertical_fov 改成 130，退出后文件里是 90。
-- 所以"下次启动时配置里还是不是我们写的那个值"是一个**决定性**证据：
--   * 还是        -> 引擎接受了（上限通路成立）
--   * 被改回 <=90 -> 引擎在启动时夹取了，说明那一趟上限补丁没赶在反序列化之前
-- 把它自动写进 STATUS，用户不用手动对比文件。
-- ---------------------------------------------------------------------------
local function last_write_path()
    local dir = localappdata_dir()
    if not dir then return nil end
    return dir .. '/fov_last_write.txt'
end

local function read_last_write()
    local p = last_write_path()
    if not p then return nil end
    local ok, v = pcall(function()
        local f = io.open(p, 'r')
        if not f then return nil end
        local t = f:read('*a')
        f:close()
        if not t then return nil end
        return tonumber(t:match('fov%s*=%s*([%d%.]+)'))
    end)
    if ok then return v end
    return nil
end

local function write_last_write(v)
    local p = last_write_path()
    if not p then return end
    pcall(function()
        local f = io.open(p, 'w')
        if not f then return end
        f:write('fov = ' .. tostring(v) .. '\n')
        f:close()
    end)
end

-- ---------------------------------------------------------------------------
-- 读取 user_settings.config 里**当前**的 vertical_fov。
-- 必须在改写它之前取快照：引擎在启动时已经把值夹到 [45,90] 了，所以内存里那个
-- 值等于 clamp(快照)。这个快照就是阶段二的身份校验依据。
-- ---------------------------------------------------------------------------
local function read_game_fov()
    local dir = appdata_dir()
    if not dir then return nil end
    local ok, text = pcall(function()
        local f = io.open(dir .. '/user_settings.config', 'r')
        if not f then return nil end
        local t = f:read('*a')
        f:close()
        return t
    end)
    if not ok or not text then return nil end
    local v = text:match('vertical_fov%s*=%s*([%d%.]+)')
    return tonumber(v)
end

-- ---------------------------------------------------------------------------
-- 写入 user_settings.config 的 vertical_fov
-- ---------------------------------------------------------------------------
local function write_game_setting(desired)
    local dir = appdata_dir()
    if not dir then return false, 'no_appdata' end
    local path = dir .. '/user_settings.config'

    local ok_read, text = pcall(function()
        local f = io.open(path, 'r')
        if not f then return nil end
        local t = f:read('*a')
        f:close()
        return t
    end)
    if not ok_read or not text then return false, 'settings_unreadable' end

    -- 备份一次就够，不要每次启动都覆盖掉真正的原始值
    local bak = path .. '.fov_unlock.bak'
    pcall(function()
        local probe = io.open(bak, 'r')
        if probe then probe:close() return end
        local b = io.open(bak, 'w')
        if b then b:write(text) b:close() end
    end)

    local value = string.format('vertical_fov = %d', math.floor(desired + 0.5))
    local replaced, count = text:gsub('vertical_fov%s*=%s*[%d%.]+', value)
    if count == 0 then
        -- 没有这个键（全新档）：追加一行
        if text:sub(-1) ~= '\n' then text = text .. '\n' end
        replaced = text .. value .. '\n'
    end
    if replaced == text then return true, 'already_set' end

    local ok_write = pcall(function()
        local f = io.open(path, 'w')
        if not f then error('open_failed') end
        f:write(replaced)
        f:close()
    end)
    if not ok_write then return false, 'settings_write_failed' end

    -- 回读验证
    local ok_v, after = pcall(function()
        local f = io.open(path, 'r')
        if not f then return nil end
        local t = f:read('*a')
        f:close()
        return t
    end)
    if not ok_v or not after then return false, 'settings_verify_unreadable' end
    local got = after:match('vertical_fov%s*=%s*([%d%.]+)')
    if tonumber(got) ~= math.floor(desired + 0.5) then
        return false, 'settings_verify_mismatch:' .. tostring(got)
    end
    return true, 'written'
end

-- ---------------------------------------------------------------------------
-- FFI / Win32
-- ---------------------------------------------------------------------------
local function build_api()
    local ffi = require('ffi')
    if ffi.os ~= 'Windows' or not ffi.abi('64bit') then
        return nil, 'windows_x64_required'
    end
    -- 只声明真正需要的符号，而且**绝不声明 VirtualQuery**。
    --
    -- 实测事故：别的内存类 mod 也会 ffi.cdef 一个 `VirtualQuery`，但它们的
    -- MEMORY_BASIC_INFORMATION 是**另一个匿名 struct**。LuaJIT 的 ffi.cdef 是
    -- 累积的，函数原型按声明顺序决定归属，于是 `VirtualQuery` 的第二参数变成了
    -- 别人的 struct，我们传自己的就炸：
    --
    --   bad argument #2 to 'VirtualQuery'
    --   (cannot convert 'struct 230 [1]' to 'struct 185 *')
    --
    -- 这个错误只在**两边的 addon 都加载**时出现，所以症状是"换个加载顺序就
    -- 生效/不生效"。默认数据路径现在完全不需要 VirtualQuery：
    -- 活设置对象在堆上（PAGE_READWRITE），直接 WriteProcessMemory 就行。
    ffi.cdef [[
        void *GetModuleHandleA(const char *);
        void *GetCurrentProcess(void);
        int ReadProcessMemory(void *, const void *, void *, size_t, size_t *);
        int WriteProcessMemory(void *, void *, const void *, size_t, size_t *);
        uint64_t GetTickCount64(void);
        int VirtualProtect(void *, size_t, uint32_t, uint32_t *);
    ]]
    local k = ffi.load('kernel32')
    local process = k.GetCurrentProcess()
    local api = { ffi = ffi, k = k, process = process }

    function api.time()
        return tonumber(k.GetTickCount64()) / 1000
    end

    function api.module(name)
        local h = k.GetModuleHandleA(name)
        if h == nil then return nil end
        local v = tonumber(ffi.cast('uintptr_t', h))
        if v == 0 then return nil end
        return v
    end

    function api.read(address, size)
        if type(address) ~= 'number' or address < 65536 or size <= 0 then return nil end
        local out = ffi.new('uint8_t[?]', size)
        local count = ffi.new('size_t[1]')
        local ok = k.ReadProcessMemory(process, ffi.cast('const void *', address), out, size, count)
        if ok == 0 or tonumber(count[0]) ~= size then return nil end
        return ffi.string(out, size)
    end

    function api.u8(address)
        local s = api.read(address, 1)
        if not s then return nil end
        return s:byte(1)
    end

    function api.u16(address)
        local s = api.read(address, 2)
        if not s then return nil end
        return s:byte(1) + s:byte(2) * 256
    end

    function api.u32(address)
        local s = api.read(address, 4)
        if not s then return nil end
        local a, b, c, d = s:byte(1), s:byte(2), s:byte(3), s:byte(4)
        return a + b * 256 + c * 65536 + d * 16777216
    end

    function api.u64(address)
        local lo = api.u32(address)
        local hi = api.u32(address + 4)
        if not lo or not hi then return nil end
        return lo + hi * 4294967296
    end

    function api.f32(address)
        local s = api.read(address, 4)
        if not s then return nil end
        local a, b, c, d = s:byte(1), s:byte(2), s:byte(3), s:byte(4)
        local bits = a + b * 256 + c * 65536 + d * 16777216
        return bits_to_f32(bits)
    end

    -- 数据写入路径。
    --
    -- **不调用 VirtualQuery、也不调用 VirtualProtect** —— 直接 WriteProcessMemory。
    -- 两个好处：
    --   1. 不会再和其它 mod 的 `ffi.cdef` 撞车（见上面那段注释）；
    --   2. 可执行页本来就是只读的，WriteProcessMemory 会**直接失败**，
    --      所以"绝不改代码段"这条保证变成了**内核层面的**，而不是靠我们判断
    --      页属性。活设置对象在堆上（PAGE_READWRITE），不需要任何保护属性改动。
    function api.write(address, bytes)
        local size = #bytes
        if type(address) ~= 'number' or address < 65536 or size <= 0 or size > 64 then
            return false, 'invalid_write'
        end
        local before = api.read(address, size)
        if before == nil then return false, 'read_before_failed' end
        if before == bytes then return true, 'already_applied' end

        local buffer = ffi.new('uint8_t[?]', size)
        ffi.copy(buffer, bytes, size)
        local written = ffi.new('size_t[1]')
        local ok = k.WriteProcessMemory(process, ffi.cast('void *', address), buffer,
            size, written)
        if ok == 0 or tonumber(written[0]) ~= size then
            -- 页面不可写（只读数据页 / 可执行页）时就是走到这里
            return false, 'write_denied_page_not_writable'
        end
        local after = api.read(address, size)
        if after ~= bytes then
            return false, 'read_back_mismatch'
        end
        return true, 'written'
    end

    -- 代码写入路径。**只给 patch_code_clamp=true 的显式选择用**，默认永远不走。
    -- 它会 VirtualProtect 成 PAGE_EXECUTE_READWRITE，也就是 GG 看的那个动作。
    function api.write_code(address, bytes)
        local size = #bytes
        if type(address) ~= 'number' or address < 65536 or size <= 0 or size > 64 then
            return false, 'invalid_write'
        end
        local before = api.read(address, size)
        if before == nil then return false, 'read_before_failed' end
        if before == bytes then return true, 'already_applied' end
        local old = ffi.new('uint32_t[1]')
        local unused = ffi.new('uint32_t[1]')
        if k.VirtualProtect(ffi.cast('void *', address), size, 0x40, old) == 0 then
            return false, 'virtualprotect_failed'
        end
        local buffer = ffi.new('uint8_t[?]', size)
        ffi.copy(buffer, bytes, size)
        local written = ffi.new('size_t[1]')
        local ok = k.WriteProcessMemory(process, ffi.cast('void *', address), buffer, size, written)
        k.VirtualProtect(ffi.cast('void *', address), size, old[0], unused)
        if ok == 0 or tonumber(written[0]) ~= size then
            return false, 'writeprocessmemory_failed'
        end
        local after = api.read(address, size)
        if after ~= bytes then
            return false, 'read_back_mismatch'
        end
        return true, 'written'
    end

    return api, 'ok'
end

-- ---------------------------------------------------------------------------
-- 在 game.dll 里找 FOV 夹取点
--
-- 目标的指令形状（相对 minss 的偏移，位移全部通配）：
--   -0x15  F3 0F 10 0D <d32>   movss xmm1,[rip+d]  -> 必须是 45.0f
--   -0x0D  0F 2F C8            comiss xmm1, xmm0
--   -0x0A  77 0C               ja +0x0C
--   -0x08  F3 0F 10 0D <d32>   movss xmm1,[rip+d]  -> 必须是 90.0f
--    0x00  F3 0F 5D C8         minss xmm1, xmm0    <-- 要打的就是这条
-- ---------------------------------------------------------------------------
local NEEDLE_MINSS = string.char(0xF3, 0x0F, 0x5D, 0xC8)
local NEEDLE_MINSS_ANY = string.char(0xF3, 0x0F, 0x5D)
local P_MOVSS = string.char(0xF3, 0x0F, 0x10, 0x0D)
local P_MOVSS_PREFIX = string.char(0xF3, 0x0F, 0x10)
local P_COMISS = string.char(0x0F, 0x2F, 0xC8)
local P_COMISS_ANY = string.char(0x0F, 0x2F)
local P_UCOMISS_ANY = string.char(0x0F, 0x2E)
local P_JA = string.char(0x77, 0x0C)
local CLAMP_PATCH = string.char(0x0F, 0x28, 0xC8, 0x90)   -- movaps xmm1, xmm0 ; nop

local CHUNK = 1024 * 1024
local OVERLAP = 64

-- Signed little-endian int32 out of an already-read chunk (1-based position).
local function signed_i32(chunk, pos)
    local a, b, c, d = chunk:byte(pos, pos + 3)
    if not a then return nil end
    local v = a + b * 256 + c * 65536 + d * 16777216
    if v >= 0x80000000 then v = v - 0x100000000 end
    return v
end

local function to_hex(blob)
    if not blob then return '(unreadable)' end
    local out = {}
    for i = 1, #blob do out[#out + 1] = string.format('%02x', blob:byte(i)) end
    return table.concat(out, ' ')
end

-- `movss xmmN, [rip+disp32]` with ANY destination register: mod=00, rm=101.
local function is_movss_rip(chunk, pos)
    if chunk:sub(pos, pos + 2) ~= P_MOVSS_PREFIX then return false end
    local modrm = chunk:byte(pos + 3)
    if not modrm then return false end
    if math.floor(modrm / 64) ~= 0 then return false end
    return (modrm % 8) == 5
end

-- 6.19 / 6.30：失败路径必须自带证据。签名找不到时把"看起来像但没全中"的点的
-- 原始字节落盘，这样一次实机就能把新构建的真实指令形状带回来，不用再赌一轮。
local SIG_DUMP_LIMIT = 48

local function write_signature_dump(sites, reason)
    local dir = localappdata_dir()
    if not dir then return end
    pcall(function() os.execute('mkdir "' .. dir .. '" >NUL 2>NUL') end)
    local ok, h = pcall(io.open, dir .. '/FOV_SIGNATURE_DUMP.txt', 'w')
    if not ok or not h then return end
    pcall(function()
        h:write('# FOV Unlock signature diagnostics\n')
        h:write('reason=' .. tostring(reason) .. '\n')
        h:write('version=' .. VERSION .. '\n')
        h:write('game_dll_base=' .. tostring(M.game_dll_base) .. '\n')
        h:write('float_decoder=' .. tostring(M.float_decoder) .. '\n')
        h:write('sites=' .. tostring(#sites) .. '\n')
        h:write('# each entry: rva, constants seen near a minss, then raw bytes\n\n')
        for i = 1, #sites do
            local s = sites[i]
            h:write(string.format('rva=0x%x c45=%s c90=%s movss45=%s movss90=%s comiss=%s ja=%s\n',
                s.rva, tostring(s.c45), tostring(s.c90),
                tostring(s.movss45), tostring(s.movss90),
                tostring(s.comiss), tostring(s.ja)))
            h:write('  ctx[-0x20..+0x10]: ' .. s.hex .. '\n')
        end
        h:close()
    end)
    emit('signature diagnostics written: FOV_SIGNATURE_DUMP.txt')
end

-- Resolve a `movss xmm1, [rip + disp32]` and read the float it points at. The
-- displacement is relative to the END of the 8-byte instruction, and the result
-- is an absolute address, so the caller must supply the chunk's base address.
local function movss_target_float(api, chunk, chunk_base, pos)
    local disp = signed_i32(chunk, pos + 4)
    if not disp then return nil end
    return api.f32(chunk_base + (pos - 1) + 8 + disp)
end

local function sections_of(api, base)
    -- 自己在内存里解一遍 PE 头，避免依赖任何外部库
    local e_lfanew = api.u32(base + 0x3C)
    if not e_lfanew then return nil, 'no_pe_header' end
    local sig = api.read(base + e_lfanew, 4)
    if sig ~= 'PE\0\0' then return nil, 'bad_pe_signature' end
    local nsec = api.u16(base + e_lfanew + 6)
    local opt_size = api.u16(base + e_lfanew + 20)
    if not nsec or not opt_size then return nil, 'bad_pe_fields' end
    local sec = base + e_lfanew + 24 + opt_size
    local out = {}
    for i = 0, nsec - 1 do
        local s = sec + i * 40
        local vsize = api.u32(s + 8)
        local vaddr = api.u32(s + 12)
        local chars = api.u32(s + 36)
        if vsize and vaddr and chars then
            -- LuaJIT has no bitwise operators, so test IMAGE_SCN_MEM_EXECUTE
            -- (0x20000000) arithmetically.
            local exec_bit = math.floor(chars / 0x20000000) % 2
            if exec_bit == 1 and vsize > 0 then
                out[#out + 1] = { va = base + vaddr, size = vsize }
            end
        end
    end
    return out, 'ok'
end

local function find_clamp(api)
    local base = api.module('game.dll')
    if not base then return nil, 'game_dll_not_loaded' end
    M.game_dll_base = base

    local secs, err = sections_of(api, base)
    if not secs then return nil, err end
    if #secs == 0 then return nil, 'no_executable_section' end

    local partial = {}
    local sites = {}
    local minss_total = 0

    for si = 1, #secs do
        local s = secs[si]
        local scanned = 0
        while scanned < s.size do
            local want = CHUNK
            if scanned + want > s.size then want = s.size - scanned end
            if want <= 0 then break end
            local chunk = api.read(s.va + scanned, want)
            if chunk then
                local chunk_base = s.va + scanned
                local from = 1
                while true do
                    local i = chunk:find(NEEDLE_MINSS, from, true)
                    if not i then break end
                    from = i + 1
                    local v = s.va + scanned + (i - 1)
                    -- 需要的上下文必须都在这一块里
                    if i - 1 >= 0x15 then
                        -- 45.0f load sits at i-0x15, the 90.0f load at i-8
                        local n90 = movss_target_float(api, chunk, chunk_base, i - 8)
                        local n45 = movss_target_float(api, chunk, chunk_base, i - 0x15)
                        local movss90 = (chunk:sub(i - 8, i - 8 + 3) == P_MOVSS)
                        local movss45 = (chunk:sub(i - 0x15, i - 0x15 + 3) == P_MOVSS)
                        local comiss = (chunk:sub(i - 0x0D, i - 0x0D + 2) == P_COMISS)
                        local ja = (chunk:sub(i - 0x0A, i - 0x0A + 1) == P_JA)
                        local score = 0
                        if movss90 then score = score + 1 end
                        if n90 and math.abs(n90 - 90.0) < 0.0005 then score = score + 1 end
                        if movss45 then score = score + 1 end
                        if n45 and math.abs(n45 - 45.0) < 0.0005 then score = score + 1 end
                        if comiss then score = score + 1 end
                        if ja then score = score + 1 end
                        if score >= 6 then
                            M.clamp_verified = string.format(
                                '45=%.3f 90=%.3f comiss=%s ja=%s', n45 or -1, n90 or -1,
                                tostring(comiss), tostring(ja))
                            return v, 'ok'
                        end
                        if score >= 4 then
                            partial[#partial + 1] = string.format(
                                'rva=0x%x score=%d 45=%.3f 90=%.3f', v - base, score,
                                n45 or -1, n90 or -1)
                        end
                        -- 证据采集：任何"minss 前面正好是个 movss"的点都可能是新构建
                        -- 里的同一处代码，连同它引用的常量一起落盘。前若干个 minss
                        -- 无条件记下 —— 万一新构建连常量都换了，也要有原始字节可看。
                        minss_total = minss_total + 1
                        local interesting = (movss90 or movss45)
                        if #sites < SIG_DUMP_LIMIT and i - 0x20 >= 1
                            and (interesting or #sites < 12) then
                            sites[#sites + 1] = {
                                rva = v - base,
                                c45 = n45,
                                c90 = n90,
                                movss45 = movss45,
                                movss90 = movss90,
                                comiss = comiss,
                                ja = ja,
                                hex = to_hex(chunk:sub(i - 0x20, i + 0x10)),
                            }
                        end
                    end
                end
            end
            scanned = scanned + want - OVERLAP
            if scanned < 0 then scanned = 0 end
            if want < CHUNK then break end
        end
    end

    local reason = 'signature_not_found'
    if #partial > 0 then
        reason = 'only_partial_matches'
        note('部分匹配（已被拒绝，不写入）：' .. table.concat(partial, ' | '))
    end
    M.sig_sites = sites
    M.sig_reason = reason
    M.minss_total = minss_total
    M.refusals = M.refusals + 1
    return nil, reason
end

-- ---------------------------------------------------------------------------
-- 宽容匹配（构建漂移的兜底）
--
-- 严格匹配要求字节形状逐条吻合；编译器换个寄存器分配（xmm1 -> xmm2）就全不中。
-- 这里改成**按值锚定**：
--   * 任一 `minss xmmR, xmmS`（寄存器形式）
--   * 它前面 0x12 字节内有一条 movss，指向的 float 恰好是 90.0f
--   * 再往前 0x30 字节内有一条 movss，指向的 float 恰好是 45.0f
--   * 两条 movss 之间有一条 comiss/ucomiss
-- 补丁按 minss 自己的寄存器号生成 movaps xmmR, xmmS，所以寄存器变了也对。
-- **要求全进程唯一命中**，多于一条就拒绝（宁可不动）。
-- ---------------------------------------------------------------------------
local function find_clamp_fallback(api, secs, base)
    local candidates = {}
    for si = 1, #secs do
        local s = secs[si]
        local scanned = 0
        while scanned < s.size do
            local want = CHUNK
            if scanned + want > s.size then want = s.size - scanned end
            if want <= 0 then break end
            local chunk = api.read(s.va + scanned, want)
            if chunk then
                local chunk_base = s.va + scanned
                local from = 1
                while true do
                    local i = chunk:find(NEEDLE_MINSS_ANY, from, true)
                    if not i then break end
                    from = i + 1
                    if i - 0x40 >= 1 and i + 3 <= #chunk then
                        local modrm = chunk:byte(i + 3)
                        if modrm and math.floor(modrm / 64) == 3 then
                            local reg = math.floor(modrm / 8) % 8
                            local rm = modrm % 8
                            local p90, p45 = nil, nil
                            local p = i - 1
                            while p >= i - 0x40 do
                                if is_movss_rip(chunk, p) then
                                    local f = movss_target_float(api, chunk, chunk_base, p)
                                    if f and math.abs(f - 90.0) < 0.0005
                                        and p90 == nil and (i - p) <= 0x12 then
                                        p90 = p
                                    elseif f and math.abs(f - 45.0) < 0.0005
                                        and p45 == nil and p90 ~= nil and (p90 - p) <= 0x30 then
                                        p45 = p
                                    end
                                end
                                p = p - 1
                            end
                            if p90 and p45 then
                                local has_cmp = false
                                local q = p45
                                while q < i do
                                    local two = chunk:sub(q, q + 1)
                                    if two == P_COMISS_ANY or two == P_UCOMISS_ANY then
                                        has_cmp = true
                                        break
                                    end
                                    q = q + 1
                                end
                                if has_cmp then
                                    candidates[#candidates + 1] = {
                                        addr = chunk_base + (i - 1),
                                        bytes = string.char(0x0F, 0x28, 0xC0 + reg * 8 + rm, 0x90),
                                        rva = (chunk_base + (i - 1)) - base,
                                        reg = reg,
                                        rm = rm,
                                    }
                                end
                            end
                        end
                    end
                end
            end
            scanned = scanned + want - OVERLAP
            if scanned < 0 then scanned = 0 end
            if want < CHUNK then break end
        end
    end
    if #candidates == 1 then
        return candidates[1], 'ok'
    end
    return nil, (#candidates == 0 and 'fallback_no_candidate' or 'fallback_ambiguous')
end

-- ---------------------------------------------------------------------------
-- 阶段二：写「活着的」设置对象
--
-- 反序列化其实写的是一个**栈上的临时结构**（boot 函数里 `lea rcx,[rbp+0x120]`），
-- 真正的持久设置在别处。序列化器的调用点给出了它：
--
--     mov  rcx, qword ptr [rip+0xa70344]   ; 全局单例指针
--     add  rcx, 0xab884                    ; 设置结构 = 单例 + 0xAB884
--     call <serializer>                    ; 序列化(设置)
--
-- 而 vertical_fov 在这个结构里的偏移是 +0x2C（反序列化里 `movss xmm2,[rbx+0x2c]`
-- 读默认值、`movss [rbx+0x2c],xmm1` 写回，序列化里 `movss xmm2,[rsi+0x2c]`）。
--
-- 偏移是 1.8.45317.0 的。**写之前一定会用值做身份校验**：读到的 float 必须正好
-- 等于配置里那个（已被引擎夹过的）vertical_fov，否则拒绝写入并落盘诊断。
-- 另有兜底：在 .text 里按 `48 8B 0D d32 48 81 C1 imm32` 的形状扫出所有
-- 「单例 + 结构偏移」的候选，取唯一通过身份校验的那个。
-- ---------------------------------------------------------------------------
local KNOWN = {
    global_rva = 0x192B2D0,   -- 全局单例指针
    settings_off = 0xAB884,   -- 设置结构在单例里的偏移
    fov_off = 0x2C,           -- vertical_fov 在设置结构里的偏移
}

local P_MOV_RCX_RIP = string.char(0x48, 0x8B, 0x0D)
local P_ADD_RCX_IMM = string.char(0x48, 0x81, 0xC1)

-- 兜底：扫出所有 `mov rcx,[rip+d32]; add rcx,imm32` 里解析得到的设置结构地址。
-- 返回带完整来历的条目，失败时可以直接落盘给别人离线分析。
local function scan_settings_bases(api, secs, base)
    local seen, list, raw = {}, {}, {}
    for si = 1, #secs do
        local s = secs[si]
        local scanned = 0
        while scanned < s.size do
            local want = CHUNK
            if scanned + want > s.size then want = s.size - scanned end
            if want <= 0 then break end
            local chunk = api.read(s.va + scanned, want)
            if chunk then
                local chunk_base = s.va + scanned
                local from = 1
                while true do
                    local i = chunk:find(P_MOV_RCX_RIP, from, true)
                    if not i then break end
                    from = i + 1
                    if i + 13 <= #chunk and chunk:sub(i + 7, i + 9) == P_ADD_RCX_IMM then
                        local disp = signed_i32(chunk, i + 3)
                        local struct_off = signed_i32(chunk, i + 10)
                        if disp and struct_off and struct_off > 0 and struct_off < 0x400000 then
                            local global_va = chunk_base + (i - 1) + 7 + disp
                            local singleton = api.u64(global_va)
                            local ok_ptr = singleton and singleton > 0x10000
                                and singleton < 0x7FFFFFFFFFFF and singleton % 8 == 0
                            -- 原始形状命中：即使解不出指针也记下来，失败时才有东西可看
                            if #raw < 64 then
                                raw[#raw + 1] = {
                                    site = chunk_base + (i - 1),
                                    global_va = global_va,
                                    struct_off = struct_off,
                                    singleton = ok_ptr and singleton or nil,
                                }
                            end
                            if ok_ptr then
                                local settings = singleton + struct_off
                                if not seen[settings] then
                                    seen[settings] = true
                                    list[#list + 1] = {
                                        settings = settings,
                                        global_va = global_va,
                                        singleton = singleton,
                                        struct_off = struct_off,
                                        site = chunk_base + (i - 1),
                                    }
                                end
                            end
                        end
                    end
                end
            end
            scanned = scanned + want - OVERLAP
            if scanned < 0 then scanned = 0 end
            if want < CHUNK then break end
        end
    end
    return list, raw
end

-- 6.19 / 6.30：活设置对象没定位到时的证据文件。把每一条候选的来历和它前 0x40
-- 字节的原始内容都写下来，这样一次失败的实机就能让我离线把新偏移算出来。
local CHAIN_DUMP_LIMIT = 32

local function write_chain_dump(api, base, known, entries, raw, reason)
    local dir = localappdata_dir()
    if not dir then return end
    pcall(function() os.execute('mkdir "' .. dir .. '" >NUL 2>NUL') end)
    local ok, h = pcall(io.open, dir .. '/FOV_CHAIN_DUMP.txt', 'w')
    if not ok or not h then return end
    pcall(function()
        h:write('# FOV Unlock settings-object diagnostics\n')
        h:write('reason=' .. tostring(reason) .. '\n')
        h:write('version=' .. VERSION .. '\n')
        h:write('game_dll_base=' .. tostring(base) .. '\n')
        h:write('known_global_rva=0x' .. string.format('%x', KNOWN.global_rva) .. '\n')
        h:write('snapshot_fov=' .. tostring(M.snapshot_fov) .. '\n')
        h:write('desired_fov=' .. tostring(M.desired_fov) .. '\n')
        if known then
            h:write(string.format('known_chain: global@0x%x singleton=0x%x settings=0x%x\n',
                known.global_va, known.singleton, known.settings))
        else
            h:write('known_chain: global slot empty/unreadable\n')
        end
        h:write('scanned_entries=' .. tostring(#entries) .. '\n')
        h:write('raw_shape_hits=' .. tostring(#(raw or {})) .. '\n')
        for i = 1, math.min(#(raw or {}), CHAIN_DUMP_LIMIT) do
            local r = raw[i]
            h:write(string.format(
                'raw %d: site_rva=0x%x global_rva=0x%x struct_off=0x%x singleton=%s\n',
                i, r.site - base, r.global_va - base, r.struct_off,
                r.singleton and string.format('0x%x', r.singleton) or 'unreadable'))
        end
        for i = 1, math.min(#entries, CHAIN_DUMP_LIMIT) do
            local e = entries[i]
            h:write(string.format(
                'entry %d: site=0x%x global@0x%x singleton=0x%x struct_off=0x%x settings=0x%x\n',
                i, e.site, e.global_va, e.singleton, e.struct_off, e.settings))
            h:write('  head[0x00..0x40]: ' .. to_hex(api.read(e.settings, 0x40)) .. '\n')
            local parts = {}
            local off = 0
            while off <= 0x40 do
                parts[#parts + 1] = string.format('+0x%x=%s', off, tostring(api.f32(e.settings + off)))
                off = off + 4
            end
            h:write('  floats: ' .. table.concat(parts, ' ') .. '\n')
        end
        h:close()
    end)
    emit('settings-chain diagnostics written: FOV_CHAIN_DUMP.txt')
end

-- 返回 settings 地址（唯一通过身份校验的那个），外加诊断字符串。
local function locate_settings_object(api, secs, base, expected)
    local cands, origins = {}, {}
    local function add(addr, how)
        if addr and not origins[addr] then
            origins[addr] = how
            cands[#cands + 1] = addr
        end
    end

    -- 主路径：已知的全局单例链
    local known = nil
    local singleton = api.u64(base + KNOWN.global_rva)
    if singleton and singleton > 0x10000 and singleton < 0x7FFFFFFFFFFF then
        known = {
            global_va = base + KNOWN.global_rva,
            singleton = singleton,
            settings = singleton + KNOWN.settings_off,
        }
        add(known.settings, 'known')
    end

    -- 兜底：形状扫描
    local scanned, raw = scan_settings_bases(api, secs, base)
    for i = 1, #scanned do add(scanned[i].settings, 'scanned') end

    local good, detail = {}, {}
    -- ① 已知偏移优先：偏移和值同时吻合，是最强的证据
    for i = 1, #cands do
        local addr = cands[i]
        local v = api.f32(addr + KNOWN.fov_off)
        detail[#detail + 1] = string.format('%s@0x%x+0x%x=%.4f',
            origins[addr], addr, KNOWN.fov_off, v or -1)
        if v and expected[v] then
            good[#good + 1] = { settings = addr, off = KNOWN.fov_off, value = v,
                                how = origins[addr] .. '/known_off' }
        end
    end
    if #good == 1 then
        return good[1], table.concat(detail, ' '), known, scanned, raw
    end
    if #good > 1 then
        return nil, 'ambiguous_known_offset [' .. table.concat(detail, ' ') .. ']',
            known, scanned
    end

    -- ② 字段偏移漂移兜底：在结构里按值找字段，要求全进程唯一。
    --    只认一个 (base, offset) 组合，多于一个就拒绝。
    local found = {}
    for i = 1, #cands do
        local addr = cands[i]
        local off = 0
        while off <= 0x400 do
            local w = api.f32(addr + off)
            if w and expected[w] then
                found[#found + 1] = { settings = addr, off = off, value = w,
                                      how = origins[addr] .. '/scanned_off' }
            end
            off = off + 4
        end
    end
    if #found == 1 then
        return found[1], 'field_scan ' .. table.concat(detail, ' '), known, scanned, raw
    end
    return nil, string.format('field_scan candidates=%d matches=%d [%s]',
        #cands, #found, table.concat(detail, ' ')), known, scanned, raw
end

-- ---------------------------------------------------------------------------
-- 通道②：定位「活着的」设置对象并写入它的 vertical_fov。
--
-- 抽成函数是为了**启动时没赶上就重试**：addon 的加载顺序/时机可能早于引擎建好
-- 设置对象，那时扫不到任何东西。实测加载顺序会影响结果，所以启动只试一次是不够的。
-- ---------------------------------------------------------------------------
local function apply_live_settings(api, snapshot, expected)
    M.settings_phase = 'searching'
    local secs = sections_of(api, M.game_dll_base)
    if not secs then
        M.settings_phase = 'no_sections'
        note('通道②跳过：读不到节表')
        return false
    end
    if not snapshot then
        note('通道②注意：user_settings.config 里没有 vertical_fov 快照，'
            .. '身份校验只剩目标值这一条（证据变弱）')
    end
    local found, detail, known, scanned, raw =
        locate_settings_object(api, secs, M.game_dll_base, expected)
    M.settings_detail = detail
    if not found then
        M.settings_phase = 'not_located'
        emit('settings object not located: ' .. tostring(detail))
        if not M.chain_dumped then
            M.chain_dumped = true
            note('通道②没定位到活的设置对象：' .. tostring(detail))
            write_chain_dump(api, M.game_dll_base, known, scanned, raw or {}, detail)
        end
        return false
    end
    M.settings_addr = found.settings
    M.settings_off = found.off
    M.settings_how = found.how
    M.settings_before = found.value
    local target = found.settings + found.off
    local wrote, wwhy = api.write(target, f32_bytes(M.desired_fov))
    if not wrote then
        M.settings_phase = 'write_refused'
        M.refusals = M.refusals + 1
        note('通道②写入被拒：' .. tostring(wwhy))
        return false
    end
    local now = api.f32(target)
    if not (now and math.abs(now - M.desired_fov) < 0.01) then
        M.settings_phase = 'verify_failed'
        M.refusals = M.refusals + 1
        note('通道②回读不符：' .. tostring(now))
        return false
    end
    M.settings_written = true
    M.settings_phase = 'applied'
    emit(string.format('live settings vertical_fov %.2f -> %d @0x%x (%s)',
        found.value, M.desired_fov, target, found.how))
    note(string.format('活设置对象已改：0x%x（%s），%.2f -> %d',
        target, found.how, found.value, M.desired_fov))
    return true
end

-- ---------------------------------------------------------------------------
-- 主流程
-- ---------------------------------------------------------------------------
local function run()
    local cfg, cfg_status = load_config()
    M.config_source = cfg_status
    emit('config ' .. cfg_status .. ' enabled=' .. tostring(cfg.enabled)
        .. ' fov=' .. tostring(cfg.fov))

    if not cfg.enabled then
        M.phase = 'disabled'
        note('fov_unlock.cfg 里 enabled 不是 true：什么都没做')
        write_status()
        return
    end

    local fov = tonumber(cfg.fov) or DEFAULT_FOV
    if fov < FOV_MIN then fov = FOV_MIN end
    if fov > FOV_MAX then
        fov = FOV_MAX
        note('请求的 FOV 超过 ' .. FOV_MAX .. '，已按上限使用')
    end
    M.desired_fov = math.floor(fov + 0.5)

    -- 身份校验依据：改文件**之前**的快照（引擎启动时已把它夹到 [45,90]）。
    -- 也把目标值本身算进去：如果上一次运行已经写进去了（或上限补丁在反序列化前
    -- 就生效了），内存里合法地会是目标值，不该因此被判成"认错了结构"。
    local snapshot = read_game_fov()
    local expected = {}
    local function expect(v)
        if v then
            expected[v] = true
            local c = v
            if c < FOV_MIN then c = FOV_MIN end
            if c > 90 then c = 90 end
            expected[c] = true
        end
    end
    expect(snapshot)
    expect(M.desired_fov)
    M.snapshot_fov = snapshot

    -- 跨启动判定：上一次我们写的值，这次启动时还在不在文件里
    local last = read_last_write()
    M.last_write = last
    if last and snapshot then
        if math.abs(last - snapshot) < 0.5 then
            M.last_write_verdict = 'accepted: config still holds our value ' .. tostring(last)
        elseif snapshot <= 90 and last > 90 then
            M.last_write_verdict = 'clamped back by the engine: we wrote ' .. tostring(last)
                .. ', next launch read ' .. tostring(snapshot)
        else
            M.last_write_verdict = 'changed elsewhere: we wrote ' .. tostring(last)
                .. ', next launch read ' .. tostring(snapshot)
        end
        note('上次写入判定：' .. M.last_write_verdict)
        emit('last-write verdict: ' .. M.last_write_verdict)
    end

    -- ① 先写配置文件：这一步不依赖任何内存技巧
    if cfg.write_config_file then
        local ok, why = write_game_setting(fov)
        M.config_written = ok and true or false
        emit('user_settings.config: ' .. tostring(why))
        if not ok then note('写设置文件失败：' .. tostring(why)) end
        write_last_write(fov)
    else
        note('write_config_file=false：跳过写设置文件')
    end

    -- ② 再打内存补丁
    local api, api_err = build_api()
    if not api then
        M.phase = 'failed'
        note('FFI/Win32 不可用：' .. tostring(api_err))
        write_status()
        return
    end

    -- 6.36 自检：float 解码器解错就地停手，绝不带着坏解码器去判定内存
    local fok, ferr = float_decoder_ok()
    M.float_decoder = fok and 'ok' or ('broken: ' .. tostring(ferr))
    if not fok then
        M.phase = 'float_decoder_broken'
        M.refusals = M.refusals + 1
        note('float 解码器自检失败：' .. tostring(ferr))
        emit('float decoder self-check FAILED: ' .. tostring(ferr))
        write_status()
        return
    end
    emit('float decoder self-check ok')
    M.api = api

    -- game.dll 基址必须在通道①之外也拿到：通道②（默认唯一通道）要用它。
    M.game_dll_base = api.module('game.dll')
    if not M.game_dll_base then
        M.phase = 'failed'
        note('game.dll 没加载？拿不到模块基址，通道①②都做不了')
        write_status()
        return
    end
    emit(string.format('game.dll base=0x%x', M.game_dll_base))

    -- 通道①：内存上限补丁。**默认关闭** —— 它必须改 game.dll 的可执行页，
    -- 而改 .text 是 nProtect GameGuard 最典型的特征（实测：触发 GG，游戏直接
    -- 关闭）。默认只走通道②（纯数据写活设置对象）。要试就在 fov_unlock.cfg
    -- 里把 patch_code_clamp 打开，风险自负。
    local clamp, find_err = nil, 'code_clamp_disabled'
    local expect_original = NEEDLE_MINSS
    local clamp_bytes = CLAMP_PATCH
    local via_fallback = false

    if cfg.patch_code_clamp then
        M.code_clamp = 'enabled_by_config'
        emit('WARNING: code clamp patch ENABLED by config; this touches an executable page')
        clamp, find_err = find_clamp(api)
    else
        M.code_clamp = 'disabled_by_config'
        note('通道①（改代码段的上限补丁）默认关闭：改可执行页会触发 GameGuard、'
            .. '实测会导致游戏直接关闭。本次只走通道②（纯数据写活设置对象）。'
            .. '想试通道①就在 fov_unlock.cfg 里加 patch_code_clamp = true，风险自负')
        emit('code clamp patch disabled -> data-only mode')
    end

    if cfg.patch_code_clamp and not clamp then
        emit('strict signature not found (' .. tostring(find_err)
            .. '); trying value-anchored fallback')
        local secs = sections_of(api, M.game_dll_base)
        local cand, ferr = nil, 'no_sections'
        if secs then cand, ferr = find_clamp_fallback(api, secs, M.game_dll_base) end
        if cand then
            clamp = cand.addr
            clamp_bytes = cand.bytes
            expect_original = string.char(0xF3, 0x0F, 0x5D, 0xC0 + cand.reg * 8 + cand.rm)
            via_fallback = true
            M.clamp_verified = string.format(
                'fallback: unique minss xmm%d, xmm%d with 45.0f and 90.0f movss + comiss nearby',
                cand.reg, cand.rm)
            emit('fallback matched rva=0x' .. string.format('%x', cand.rva) .. ' (unique)')
            note('严格签名没中，用「按值锚定」兜底命中（唯一）；这通常意味着构建改了寄存器分配')
        else
            find_err = tostring(find_err) .. ' / ' .. tostring(ferr)
            M.sig_reason = find_err
            write_signature_dump(M.sig_sites or {}, find_err)
        end
    end

    if cfg.patch_code_clamp and not clamp then
        M.phase = 'failed'
        note('定位夹取点失败：' .. tostring(find_err))
        emit('clamp not found: ' .. tostring(find_err))
        -- 注意：不 return —— 通道②（纯数据）还要继续跑
    end

    -- 通道① 的写入：每一步失败都只是**跳过通道①**，绝不能 return，
    -- 否则通道②（纯数据，默认唯一通道）就没机会跑了。
    if clamp then
        M.clamp_found = true
        M.clamp_via = via_fallback and 'fallback' or 'strict'
        M.clamp_rva = string.format('0x%x', clamp - M.game_dll_base)
        M.clamp_addr = clamp
        M.clamp_bytes = clamp_bytes
        emit('clamp at ' .. M.clamp_rva .. ' via ' .. M.clamp_via
            .. ' (' .. tostring(M.clamp_verified) .. ')')

        local original = api.read(clamp, 4)
        local ok, why = false, 'not_attempted'
        if original ~= expect_original then
            M.refusals = M.refusals + 1
            note('写入前重读字节不符，拒绝写入：' .. tostring(original))
        else
            -- 走**代码**写入路径（会 VirtualProtect 成 RWX）；只有显式打开
            -- patch_code_clamp 才可能到这里。
            ok, why = api.write_code(clamp, clamp_bytes)
        end

        if not ok then
            M.refusals = M.refusals + 1
            note('通道①写入被拒/失败：' .. tostring(why))
            emit('code patch refused: ' .. tostring(why))
        else
            local after = api.read(clamp, 4)
            if after ~= clamp_bytes then
                M.refusals = M.refusals + 1
                note('通道①回读校验失败，补丁状态未知')
            else
                M.patch_applied = true
                M.phase = 'patched'
                emit('patched 90f upper clamp -> passthrough; desired fov = '
                    .. tostring(M.desired_fov))
                note('已用 movaps xmm1, xmm0 取代 minss xmm1, xmm0（下限 45 仍在）')
            end
        end
    end

    -- ③ 通道②：把活着的设置对象里的 vertical_fov 改成目标值。
    --    没赶上就交给 update 钩子重试（加载顺序会影响这个时机）。
    M.expected_set = expected
    M.stage2_attempts = 1
    M.stage2_deadline = api.time() + 90
    apply_live_settings(api, snapshot, expected)

    if M.settings_written then
        note('通道②已生效（纯数据）。如果你在设置里动过滑条，'
            .. 'mod 每 1.5 秒会把值补回去')
    else
        note('通道②这次没写成（settings_phase=' .. tostring(M.settings_phase)
            .. '），会在随后的帧里重试最多几次；'
            .. '如果最终仍是 not_located，请把 FOV_UNLOCK_STATUS.txt、FOVUnlock.log、'
            .. 'FOV_CHAIN_DUMP.txt 发出来')
    end
    write_status()
end

local ok, err = pcall(function()
    if not environment_gate() then
        write_status()
        return
    end
    run()
end)

if not ok then
    M.phase = 'error'
    note('未捕获错误：' .. tostring(err))
    pcall(write_status)
    pcall(emit, 'error: ' .. tostring(err))
end

-- ---------------------------------------------------------------------------
-- 维护：活设置对象会被引擎自己改写（一改设置就重新序列化/应用），所以补一次没用，
-- 要隔一会儿复查一次。
--
-- 6.43/6.45：稳态下每次只读 4~8 字节、写盘只在状态**实质变化**时发生；
-- 6.44：退钩前先确认自身仍是顶层 wrapper，否则置 retired 只旁路自己，别截断
-- 别人挂在我们之后的 update 链。
-- ---------------------------------------------------------------------------
local previous_update = rawget(_G, 'update')
if type(previous_update) ~= 'function' then
    M.recheck = 'unavailable_no_update_hook'
    note('loader 没有提供 update 钩子：不做周期性复查（补丁仍然生效）')
    pcall(write_status)
else
    M.recheck = 'armed'
    local my_update
    local next_check = 0
    local last_state = nil

    my_update = function(dt, ...)
        local step_ok, step_err = pcall(function()
            if M.retired or M.disabled then return end
            local api = M.api
            if not api then return end
            local now = api.time()
            if now < next_check then return end
            next_check = now + 1.5

            -- ⓪ 通道② 启动时没写成 → 重试。
            --    加载顺序/时机可能让 addon 早于设置对象建成，那时什么都扫不到；
            --    只在"还没找到"这类**可能自己变好**的状态上重试（写入被拒/回读
            --    不符重试没意义）。退避 8 秒，最多 5 次，90 秒后彻底停手（技能 6.14）。
            local retryable = (M.settings_phase == 'not_located'
                or M.settings_phase == 'no_sections')
            if api and M.game_dll_base and not M.settings_written and retryable
                and (M.stage2_attempts or 0) < 5
                and now < (M.stage2_deadline or 0)
                and now >= (M.stage2_next or 0) then
                M.stage2_attempts = (M.stage2_attempts or 0) + 1
                M.stage2_next = now + 8
                pcall(apply_live_settings, api, M.snapshot_fov, M.expected_set or {})
                if M.settings_written then
                    emit('channel 2 succeeded on retry ' .. tostring(M.stage2_attempts))
                end
            end

            -- ① 活设置对象还在不在、值对不对
            if M.settings_addr then
                local field = M.settings_addr + (M.settings_off or KNOWN.fov_off)
                local v = api.f32(field)
                if v == nil then
                    M.settings_phase = 'unreadable'
                elseif math.abs(v - M.desired_fov) >= 0.01 then
                    local bytes = f32_bytes(M.desired_fov)
                    if bytes then
                        local wrote = api.write(field, bytes)
                        if wrote then
                            M.reasserts = (M.reasserts or 0) + 1
                            M.settings_phase = 'reasserted'
                        end
                    end
                end
            end

            -- ② 内存上限补丁还在不在（只在显式开启通道①时才复查）
            if M.code_clamp == 'enabled_by_config'
                and M.patch_applied and M.clamp_addr and M.clamp_bytes then
                local cur = api.read(M.clamp_addr, 4)
                if cur and cur ~= M.clamp_bytes then
                    local again = api.write_code(M.clamp_addr, M.clamp_bytes)
                    M.patch_reasserts = (M.patch_reasserts or 0) + 1
                    M.patch_applied = again and true or M.patch_applied
                end
            end

            -- 6.45：只有状态实质变化才落盘
            local state = tostring(M.phase) .. '|' .. tostring(M.settings_phase)
                .. '|' .. tostring(M.settings_written) .. '|' .. tostring(M.reasserts)
            if state ~= last_state then
                last_state = state
                write_status()
            end
        end)
        if not step_ok then
            M.disabled = true
            pcall(note, 'recheck error: ' .. tostring(step_err))
            pcall(write_status)
        end
        return previous_update(dt, ...)
    end

    _G.update = my_update
    M.retire_hook = function()
        M.retired = true
        if rawget(_G, 'update') == my_update then
            _G.update = previous_update
        end
    end
end

return M
