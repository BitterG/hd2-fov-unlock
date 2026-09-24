#!/usr/bin/env python3
"""Offline simulation tests for FOV Unlock.

Runs the real addon source under lupa against a synthetic in-memory image of
`game.dll` that contains the exact FOV clamp instruction sequence, with a
deliberately strict Win32/FFI stub (a raw number where a pointer belongs is an
error, see the skill's lesson 6.4).

The point of the negative fixtures is that they must be REFUSED. If the addon
ever patches one of them, a test fails - that is what makes the suite mean
something.

Usage: python tests/test_fov_unlock.py [--verbose]
"""
import struct
import sys
from pathlib import Path

# 6.20: the game runs LuaJIT, so the simulation must too. Running on the
# default CPython-bundled Lua (5.4/5.5) hides `//`, `goto`, `math.type`,
# `string.pack` and friends, which the game rejects at load.
try:
    from lupa.luajit21 import LuaRuntime
    LUA_FLAVOUR = 'luajit21'
except ImportError:                                   # pragma: no cover
    from lupa import LuaRuntime
    LUA_FLAVOUR = 'lua'

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / 'Source/mods/hd2/fov_unlock.lua'
ARCHIVE = ROOT / 'Addon/9ba626afa44a3aa3.patch_0'

sys.path.insert(0, str(ROOT / 'scripts'))
import build_addon  # noqa: E402


def test_source_has_no_bom(verbose=False):
    """A UTF-8 BOM before the marker made the packer prepend a SECOND marker and
    left the BOM on body line 2, where LuaJIT rejects the chunk. The mod then
    never loaded and it looked like 'no effect'."""
    raw = ADDON.read_bytes()
    assert not raw.startswith(b'\xef\xbb\xbf'), 'addon source starts with a BOM'
    assert b'\xef\xbb\xbf' not in raw, 'addon source contains a BOM'
    return True


def test_packer_refuses_a_bom(verbose=False):
    """Mutation test for the packer guard: if a BOM sneaks back in, packing
    must fail loudly instead of shipping a broken addon."""
    good = ADDON.read_bytes()
    build_addon.envelope('mods/hd2/fov_unlock', good)   # baseline must pass
    try:
        build_addon.envelope('mods/hd2/fov_unlock', b'\xef\xbb\xbf' + good)
    except ValueError as exc:
        assert 'BOM' in str(exc), exc
        return True
    raise AssertionError('packer accepted a BOM-prefixed source')


def test_packer_refuses_duplicate_declaration(verbose=False):
    good = ADDON.read_bytes()
    doubled = good.replace(b'-- HD2-Addon: mods/hd2/fov_unlock',
                           b'-- HD2-Addon: mods/hd2/fov_unlock\n'
                           b'-- HD2-Addon: mods/hd2/fov_unlock', 1)
    try:
        build_addon.envelope('mods/hd2/fov_unlock', doubled)
    except ValueError as exc:
        assert 'exactly one' in str(exc), exc
        return True
    raise AssertionError('packer accepted a duplicate declaration')


def test_packaged_body_compiles(verbose=False):
    """Compile the exact bytes the loader compiles - not just the source file.
    luaL_loadfile skips a leading BOM, so a source-level gate cannot see a BOM
    that survives into the archive body."""
    assert ARCHIVE.is_file(), 'run scripts/build_addon.py first'
    data = ARCHIVE.read_bytes()
    at = data.find(b'-- HD2-Addon: ')
    assert at >= 8, 'no declaration in the archive'
    # The resource is <u32 body_len><u32 version=2><body>, so the declared
    # length is authoritative - do NOT guess with a NUL-run search, which
    # truncates the body and turns into a bogus syntax error at EOF.
    assert struct.unpack_from('<I', data, at - 4)[0] == 2, 'unexpected envelope version'
    body_len = struct.unpack_from('<I', data, at - 8)[0]
    body = data[at:at + body_len]
    assert len(body) == body_len, f'declared {body_len}, got {len(body)}'
    assert body.count(b'-- HD2-Addon: ') == 1, 'duplicate declaration in the body'
    assert b'\xef\xbb\xbf' not in body, 'BOM inside the packaged body'
    verdict = build_addon.compile_body(body, 'mods/hd2/fov_unlock')
    assert verdict == 'OK', verdict
    if verbose:
        print('   packaged body compiles,', len(body), 'bytes')
    return True

# Tokens LuaJIT (5.1) does not accept. The syntax gate scans for these in
# addition to compiling the source, because a token can hide inside a branch
# that the simulation never executes.
LUAJIT_FORBIDDEN = ['//', 'goto ', 'math.type', 'string.pack', 'string.unpack',
                    'table.move', '::', '& 0x', '| 0x']

IMAGE_BASE = 0x7FF900000000
TEXT_VA = IMAGE_BASE + 0x1000
TEXT_SIZE = 0x4000
RDATA_VA = IMAGE_BASE + 0x10000
RDATA_SIZE = 0x1000

# Stage-2 territory: a data page holding the global singleton pointer, and a
# heap block holding the persistent settings object.
DATA_VA = IMAGE_BASE + 0x1900000
DATA_SIZE = 0x40000
HEAP_VA = 0x244B0000000
HEAP_SIZE = 0xC0000

GLOBAL_SINGLETON_RVA = 0x192B2D0      # matches KNOWN.global_rva in the addon
SETTINGS_OFF = 0xAB884                # matches KNOWN.settings_off
FOV_OFF = 0x2C                        # matches KNOWN.fov_off

# Byte patterns that must match the addon's expectations.
MOVSS_XMM1_RIP = bytes([0xF3, 0x0F, 0x10, 0x0D])
COMISS_XMM1_XMM0 = bytes([0x0F, 0x2F, 0xC8])
JA_SHORT_0C = bytes([0x77, 0x0C])
MINSS_XMM1_XMM0 = bytes([0xF3, 0x0F, 0x5D, 0xC8])
CLAMP_PATCH = bytes([0x0F, 0x28, 0xC8, 0x90])


class Memory:
    """Sparse byte address space with regions, like a process address space."""

    def __init__(self):
        self.data = {}
        self.regions = []

    def add_region(self, base, size, protect=0x20):
        self.regions.append(dict(base=base, size=size, protect=protect,
                                 cur=protect, state=0x1000))

    def is_writable(self, addr, size):
        """Model Windows: WriteProcessMemory fails unless the page is writable."""
        for i in range(size):
            r = self.region_at(addr + i)
            if r is None:
                return False
            if r['cur'] not in (0x04, 0x08, 0x40, 0x80):
                return False
        return True

    def set_protect(self, addr, size, protect):
        for i in range(size):
            r = self.region_at(addr + i)
            if r is not None:
                r['cur'] = protect

    def protect_of(self, addr):
        r = self.region_at(addr)
        return None if r is None else r['cur']

    def write(self, addr, blob):
        for i, b in enumerate(blob):
            self.data[addr + i] = b

    def read(self, addr, size):
        out = bytearray()
        for i in range(size):
            a = addr + i
            b = self.data.get(a)
            if b is None:
                # Unmapped holes must fail the read; mapped-but-untouched bytes
                # are genuine zeros.
                if self.region_at(a) is None:
                    return None
                b = 0
            out.append(b)
        return bytes(out)

    def region_at(self, addr):
        for r in self.regions:
            if r['base'] <= addr < r['base'] + r['size']:
                return r
        return None


def build_image(*, real_site=True, real_45=45.0, decoy_first=True,
                decoy_45=46.0, decoy_has_comiss=False, real_xmm=1, decoy_xmm=1):
    """Synthesise a game.dll-like image carrying the clamp sequence."""
    mem = Memory()
    mem.add_region(IMAGE_BASE, 0x1000, protect=0x02)      # image headers
    mem.add_region(TEXT_VA, TEXT_SIZE, protect=0x20)      # PAGE_EXECUTE_READ
    mem.add_region(RDATA_VA, RDATA_SIZE, protect=0x02)    # PAGE_READONLY

    # --- PE headers so the addon can walk the section table -------------
    mem.write(IMAGE_BASE, b'MZ')
    e_lfanew = 0x80
    mem.write(IMAGE_BASE + 0x3C, struct.pack('<I', e_lfanew))
    mem.write(IMAGE_BASE + e_lfanew, b'PE\0\0')
    nsec = 2
    opt_size = 0xF0
    mem.write(IMAGE_BASE + e_lfanew + 6, struct.pack('<H', nsec))
    mem.write(IMAGE_BASE + e_lfanew + 20, struct.pack('<H', opt_size))
    sec = IMAGE_BASE + e_lfanew + 24 + opt_size
    mem.write(sec + 8, struct.pack('<II', TEXT_SIZE, 0x1000))
    mem.write(sec + 36, struct.pack('<I', 0x60000020))      # CNT_CODE|EXEC|READ
    sec2 = sec + 40
    mem.write(sec2 + 8, struct.pack('<II', RDATA_SIZE, 0x10000))
    mem.write(sec2 + 36, struct.pack('<I', 0x40000040))     # INIT_DATA|READ

    # --- constants ------------------------------------------------------
    const45_va = RDATA_VA + 0x100
    const90_va = RDATA_VA + 0x200
    mem.write(const45_va, struct.pack('<f', 45.0))
    mem.write(const90_va, struct.pack('<f', 90.0))
    decoy45_va = RDATA_VA + 0x300
    decoy90_va = RDATA_VA + 0x400
    mem.write(decoy45_va, struct.pack('<f', decoy_45))
    mem.write(decoy90_va, struct.pack('<f', 90.0))

    def movss(at, target_va, xmm=1):
        body = bytes([0xF3, 0x0F, 0x10, 0x05 | (xmm << 3)])
        disp = target_va - (at + 8)
        return body + struct.pack('<i', disp)

    def emit_site(minss_va, c45_va, c90_va, *, comiss=True, ja=True, xmm=1):
        modrm = 0xC0 | (xmm << 3)
        mem.write(minss_va - 0x15, movss(minss_va - 0x15, c45_va, xmm))
        if comiss:
            mem.write(minss_va - 0x0D, bytes([0x0F, 0x2F, modrm]))
        if ja:
            mem.write(minss_va - 0x0A, JA_SHORT_0C)
        mem.write(minss_va - 0x08, movss(minss_va - 0x08, c90_va, xmm))
        mem.write(minss_va, bytes([0xF3, 0x0F, 0x5D, modrm]))

    # A decoy first in address order, so the scanner must reject it and keep
    # looking rather than take the first minss it sees.
    decoy_va = TEXT_VA + 0x400
    if decoy_first:
        emit_site(decoy_va, decoy45_va, decoy90_va,
                  comiss=decoy_has_comiss, ja=decoy_has_comiss, xmm=decoy_xmm)

    real_va = TEXT_VA + 0x1000
    if real_site:
        mem.write(const45_va, struct.pack('<f', real_45))
        emit_site(real_va, const45_va, const90_va, comiss=True, ja=True, xmm=real_xmm)

    return mem, (real_va if real_site else None), decoy_va


def add_stage2(mem, *, global_rva=GLOBAL_SINGLETON_RVA, scanned_site=False,
               live_fov=90.0, heap_slot=FOV_OFF, settings_off=SETTINGS_OFF):
    """Give the image a persistent settings object the addon can locate.

    Mirrors the real chain: [game.dll+0x192B2D0] -> singleton, and the settings
    object lives at singleton + 0xAB884 with vertical_fov at +0x2C.

    Pass a different `global_rva` to exercise the shape scan: the known-path
    address is then left holding 0 while the scanned instruction points at the
    real pointer.
    """
    mem.add_region(DATA_VA, DATA_SIZE, protect=0x02)
    mem.add_region(HEAP_VA, HEAP_SIZE, protect=0x04)
    singleton = HEAP_VA + 0x1000
    settings = singleton + settings_off
    mem.write(settings + heap_slot, struct.pack('<f', live_fov))
    mem.write(IMAGE_BASE + global_rva, struct.pack('<Q', singleton))
    if scanned_site:
        at = TEXT_VA + 0x2000
        d32 = (IMAGE_BASE + global_rva) - (at + 7)
        mem.write(at, bytes([0x48, 0x8B, 0x0D]) + struct.pack('<i', d32)
                  + bytes([0x48, 0x81, 0xC1]) + struct.pack('<I', settings_off))
    return singleton, settings


LUA_PRELUDE = r"""
local mem_read, mem_write, mem_region, fail_write =
    __mem_read, __mem_write, __mem_region, __fail_write

-- ---------------------------------------------------------------- ffi stub
local buffers = {}
local function is_buf(v) return type(v) == 'table' and v.__buf == true end
local function is_ptr(v) return type(v) == 'table' and v.__ptr ~= nil end

local ffi = {}
ffi.os = 'Windows'
ffi.abi = function(what) return what == '64bit' end
ffi.cdef = function(_) end
ffi.sizeof = function(ctype)
    if ctype == 'FOV_MBI' then return 48 end
    if type(ctype) == 'table' then return 48 end    -- sizeof(buf[0])
    error('sizeof not stubbed for ' .. tostring(ctype))
end
ffi.new = function(ctype, count)
    if ctype == 'uint8_t[?]' then return { __buf = true, __ctype = 'u8', n = count } end
    if ctype == 'uint32_t[1]' then return { __buf = true, __ctype = 'u32', n = 1, [0] = 0 } end
    if ctype == 'size_t[1]' then return { __buf = true, __ctype = 'u64', n = 1, [0] = 0 } end
    if ctype == 'float[1]' then return { __buf = true, __ctype = 'f32', n = 1, [0] = 0 } end
    if ctype == 'FOV_MBI[1]' then
        return { __buf = true, __ctype = 'mbi', n = 1, [0] = {} }
    end
    error('ffi.new not stubbed for ' .. tostring(ctype))
end
-- Deliberately strict: a plain number where a pointer is expected must fail.
ffi.cast = function(ctype, value)
    if ctype:find('%*') then
        if type(value) == 'number' then return { __ptr = value } end
        if is_ptr(value) then return value end
        error('ffi.cast(' .. ctype .. '): cannot cast ' .. type(value))
    end
    if is_ptr(value) then return value.__ptr end
    if is_buf(value) then return value[0] end
    if type(value) == 'number' then return value end
    error('ffi.cast(' .. ctype .. '): cannot cast ' .. type(value))
end
ffi.copy = function(dst, src, n)
    if not is_buf(dst) then error('ffi.copy destination must be a buffer') end
    if dst.__ctype == 'f32' then
        if type(src) ~= 'string' then error('float copy needs a string source') end
        local parts = {}
        for i = 1, 4 do parts[#parts + 1] = string.format('%02x', string.byte(src, i)) end
        dst[0] = __bytes_to_float(table.concat(parts))
        return
    end
    if type(src) == 'string' then
        for i = 1, n do dst[i - 1] = string.byte(src, i) end
        return
    end
    if is_buf(src) then
        for i = 0, n - 1 do dst[i] = src[i] end
        return
    end
    error('ffi.copy source must be a string or buffer')
end
ffi.string = function(buf, n)
    if not is_buf(buf) then error('ffi.string expects a buffer') end
    local parts = {}
    for i = 0, n - 1 do parts[#parts + 1] = string.char(buf[i]) end
    return table.concat(parts)
end
ffi.load = function(name)
    if name ~= 'kernel32' then error('unexpected library ' .. tostring(name)) end
    return __kernel32
end

local function require_ptr(value, where)
    if not is_ptr(value) then
        error(where .. ': expected a pointer, got ' .. type(value))
    end
    return value.__ptr
end

__kernel32 = {}
function __kernel32.GetCurrentProcess() return { __ptr = 0x1000 } end
function __kernel32.GetTickCount64() return tonumber(__now_ms) end
function __kernel32.GetModuleHandleA(name)
    if type(name) ~= 'string' then error('GetModuleHandleA: name must be a string') end
    if name == 'game.dll' then return { __ptr = __image_base } end
    return nil
end
function __kernel32.ReadProcessMemory(proc, address, buf, size, count)
    require_ptr(proc, 'ReadProcessMemory(proc)')
    local addr = require_ptr(address, 'ReadProcessMemory(address)')
    if not is_buf(buf) then error('ReadProcessMemory: buf must be a buffer') end
    local hex = mem_read(addr, size)
    if hex == nil then return 0 end
    for i = 1, size do
        buf[i - 1] = tonumber(hex:sub((i - 1) * 2 + 1, (i - 1) * 2 + 2), 16)
    end
    count[0] = size
    return 1
end
function __kernel32.WriteProcessMemory(proc, address, buf, size, written)
    require_ptr(proc, 'WriteProcessMemory(proc)')
    local addr = require_ptr(address, 'WriteProcessMemory(address)')
    if not is_buf(buf) then error('WriteProcessMemory: buf must be a buffer') end
    if fail_write() then return 0 end
    -- Like the real API: a page that is not writable makes this fail.
    if not __is_writable(addr, size) then return 0 end
    local parts = {}
    for i = 0, size - 1 do parts[#parts + 1] = string.format('%02x', buf[i]) end
    mem_write(addr, table.concat(parts))
    written[0] = size
    return 1
end
function __kernel32.VirtualProtect(address, size, protect, old)
    local addr = require_ptr(address, 'VirtualProtect(address)')
    __vp_count = __vp_count + 1
    old[0] = __protect_of(addr) or 0x02
    __set_protect(addr, size, protect)
    return 1
end
function __kernel32.VirtualQuery(address, mbi, size)
    local addr = require_ptr(address, 'VirtualQuery(address)')
    local hex = mem_region(addr)
    if hex == nil then return 0 end
    local base, regionsize, prot = hex:match('^(%d+):(%d+):(%d+)$')
    mbi[0].BaseAddress = tonumber(base)
    mbi[0].RegionSize = tonumber(regionsize)
    mbi[0].Protect = tonumber(prot)
    mbi[0].State = 0x1000
    return 48
end

-- ------------------------------------------------------------- file system
local fs = {}
__fs = fs
local real_open = io.open
io.open = function(path, mode)
    if mode == 'w' then
        fs[path] = ''
        return {
            write = function(self, s) fs[path] = (fs[path] or '') .. s end,
            close = function() end,
            flush = function() end,
        }
    end
    local content = fs[path]
    if content == nil then return nil end
    local pos = 1
    return {
        read = function(self, fmt)
            if fmt == '*a' then
                local out = content:sub(pos)
                pos = #content + 1
                return out
            end
            local line = content:match('([^\n]*)\n?', pos)
            if line == nil or pos > #content then return nil end
            pos = pos + #line + 1
            return line
        end,
        close = function() end,
    }
end

local env = __env
os.getenv = function(name) return env[name] end
os.date = function() return '1970-01-01T00:00:00Z' end
os.execute = function() return 0 end
print = function() end

ffi = ffi
require = function(name)
    if name == 'ffi' then return ffi end
    error('unexpected require: ' .. tostring(name))
end
"""


def run_mod(mem, files=None, env=None, fail_write=False, source_override=None,
            with_update=False, globals_out=None, stats_out=None):
    lua = LuaRuntime(unpack_returned_tuples=True)
    files = dict(files or {})
    env = dict(env or {
        'APPDATA': 'C:/fake',
        'LOCALAPPDATA': 'C:/fake/Local',
    })

    def mem_read(addr, size):
        addr = int(addr)
        size = int(size)
        blob = mem.read(addr, size)
        return None if blob is None else blob.hex()

    def mem_write(addr, hexstr):
        addr = int(addr)
        blob = bytes.fromhex(str(hexstr))
        mem.write(addr, blob)
        return True

    def mem_region(addr):
        r = mem.region_at(int(addr))
        if r is None:
            return None
        return f"{r['base']}:{r['size']}:{r['protect']}"

    g = lua.globals()
    g.__mem_read = mem_read
    g.__mem_write = mem_write
    g.__mem_region = mem_region
    g.__fail_write = lambda: bool(fail_write)
    g.__bytes_to_float = lambda h: struct.unpack('<f', bytes.fromhex(str(h)))[0]
    g.__is_writable = lambda a, n: mem.is_writable(int(a), int(n))
    g.__protect_of = lambda a: mem.protect_of(int(a))
    g.__set_protect = lambda a, n, p: mem.set_protect(int(a), int(n), int(p))
    g.__vp_count = 0
    g.__now_ms = 12345
    g.__env = lua.table_from(env)
    g.__image_base = IMAGE_BASE
    g.__fs_table = lua.table_from(files)
    lua.execute(LUA_PRELUDE)
    # The file-system stub table is created inside Lua; seed it from Python.
    lua.execute("__fs_seed = __fs")
    inner = g.__fs
    for k, v in files.items():
        inner[k] = v
    g.CowboyBingusModLoader = lua.table_from({'api': 1, 'version': 15})
    if with_update:
        lua.execute("update = function(dt) return dt end")

    source = source_override if source_override is not None else ADDON.read_text(encoding='utf-8')
    lua.execute(source)
    if globals_out is not None:
        globals_out['g'] = g
    if stats_out is not None:
        stats_out['vp_count'] = int(g.__vp_count or 0)
    result = g.FOVUnlock
    written = {}
    for k, v in inner.items():
        written[k] = v
    return result, written


def get(table, key):
    value = table[key]
    return value


def cfg_text(fov=120, enabled='true', code_clamp=True):
    """Test cfgs opt IN to the code patch, because most of the suite exercises
    the clamp path. The shipped default (data-only) is asserted separately by
    test_default_is_data_only_no_code_touch."""
    return (f"enabled = {enabled}\nfov = {fov}\nwrite_config_file = true\n"
            f"patch_code_clamp = {'true' if code_clamp else 'false'}\n")


def settings_text(fov=55):
    return f"audio_volume = 1\nversion = 15\nvertical_fov = {fov}\nreflex_mode = 2\n"


# --------------------------------------------------------------------------
# positive case
# --------------------------------------------------------------------------
def test_patches_real_site_and_ignores_decoy(verbose=False):
    mem, real_va, decoy_va = build_image()
    result, files = run_mod(mem, files={
        'C:/fake/Arrowhead/Helldivers2/fov_unlock.cfg': cfg_text(120),
        'C:/fake/Arrowhead/Helldivers2/user_settings.config': settings_text(55),
    })
    assert get(result, 'patch_applied') is True, get(result, 'status')
    assert get(result, 'clamp_found') is True
    assert get(result, 'config_written') is True
    assert mem.read(real_va, 4) == CLAMP_PATCH, 'real site not patched'
    assert mem.read(decoy_va, 4) == MINSS_XMM1_XMM0, 'decoy must NOT be patched'
    settings = files['C:/fake/Arrowhead/Helldivers2/user_settings.config']
    assert 'vertical_fov = 120' in settings, settings
    assert 'vertical_fov = 55' not in settings, settings
    if verbose:
        print('   clamp_rva =', get(result, 'clamp_rva'), 'verified =', get(result, 'clamp_verified'))
    return True


def test_backup_written_once(verbose=False):
    mem, _, _ = build_image()
    original = settings_text(55)
    result, files = run_mod(mem, files={
        'C:/fake/Arrowhead/Helldivers2/fov_unlock.cfg': cfg_text(130),
        'C:/fake/Arrowhead/Helldivers2/user_settings.config': original,
    })
    assert get(result, 'config_written') is True
    bak = files.get('C:/fake/Arrowhead/Helldivers2/user_settings.config.fov_unlock.bak')
    assert bak == original, 'backup must hold the pre-mod file verbatim'
    return True


def test_status_file_first_line(verbose=False):
    mem, _, _ = build_image()
    # the config says vertical_fov = 55, so the live object must too
    add_stage2(mem, live_fov=55.0)
    result, files = run_mod(mem, files={
        'C:/fake/Arrowhead/Helldivers2/fov_unlock.cfg': cfg_text(120),
        'C:/fake/Arrowhead/Helldivers2/user_settings.config': settings_text(55),
    })
    status = files.get('C:/fake/Local/CowboyBingus/Helldivers2/FOV_UNLOCK_STATUS.txt')
    assert status, 'status file missing'
    first = status.splitlines()[0]
    assert first.startswith('OK -'), first
    if verbose:
        print('   status[0] =', first)
    return True


# --------------------------------------------------------------------------
# negative fixtures - these must be REFUSED
# --------------------------------------------------------------------------
def test_refuses_when_45_constant_is_wrong(verbose=False):
    """The 45.0f verification is what proves this is the FOV clamp, not some
    other 90.0f minss. A site whose lower bound is 46.0f must not be patched."""
    mem, real_va, _ = build_image(real_45=46.0)
    result, files = run_mod(mem, files={
        'C:/fake/Arrowhead/Helldivers2/fov_unlock.cfg': cfg_text(120),
        'C:/fake/Arrowhead/Helldivers2/user_settings.config': settings_text(55),
    })
    assert get(result, 'patch_applied') is False, 'must not patch an unverified site'
    assert mem.read(real_va, 4) == MINSS_XMM1_XMM0, 'memory was modified anyway!'
    assert get(result, 'phase') == 'failed'
    if verbose:
        print('   status =', get(result, 'status'))
    return True


def test_refuses_decoy_without_comiss_and_ja(verbose=False):
    mem, _, decoy_va = build_image(real_site=False, decoy_has_comiss=False)
    result, _ = run_mod(mem, files={
        'C:/fake/Arrowhead/Helldivers2/fov_unlock.cfg': cfg_text(120),
        'C:/fake/Arrowhead/Helldivers2/user_settings.config': settings_text(55),
    })
    assert get(result, 'patch_applied') is False
    assert mem.read(decoy_va, 4) == MINSS_XMM1_XMM0, 'decoy was patched!'
    return True


def test_refuses_when_signature_absent(verbose=False):
    mem, _, _ = build_image(real_site=False, decoy_first=False)
    result, files = run_mod(mem, files={
        'C:/fake/Arrowhead/Helldivers2/fov_unlock.cfg': cfg_text(120),
        'C:/fake/Arrowhead/Helldivers2/user_settings.config': settings_text(55),
    })
    assert get(result, 'clamp_found') is False
    assert get(result, 'patch_applied') is False
    status = files.get('C:/fake/Local/CowboyBingus/Helldivers2/FOV_UNLOCK_STATUS.txt')
    assert status.splitlines()[0].startswith('FAILED -'), status
    return True


def test_readback_failure_is_detected(verbose=False):
    """If WriteProcessMemory silently does nothing, the read-back check must
    catch it and report patch_applied = false."""
    mem, real_va, _ = build_image()
    result, _ = run_mod(mem, files={
        'C:/fake/Arrowhead/Helldivers2/fov_unlock.cfg': cfg_text(120),
        'C:/fake/Arrowhead/Helldivers2/user_settings.config': settings_text(55),
    }, fail_write=True)
    assert get(result, 'patch_applied') is False, 'read-back check did not fire'
    assert mem.read(real_va, 4) == MINSS_XMM1_XMM0
    if verbose:
        print('   status =', get(result, 'status'))
    return True


def test_disabled_config_does_nothing(verbose=False):
    mem, real_va, _ = build_image()
    result, files = run_mod(mem, files={
        'C:/fake/Arrowhead/Helldivers2/fov_unlock.cfg': cfg_text(120, enabled='false'),
        'C:/fake/Arrowhead/Helldivers2/user_settings.config': settings_text(55),
    })
    assert get(result, 'patch_applied') is False
    assert get(result, 'config_written') is False
    assert mem.read(real_va, 4) == MINSS_XMM1_XMM0
    settings = files['C:/fake/Arrowhead/Helldivers2/user_settings.config']
    assert 'vertical_fov = 55' in settings
    return True


def test_fov_is_bounded(verbose=False):
    mem, _, _ = build_image()
    result, files = run_mod(mem, files={
        'C:/fake/Arrowhead/Helldivers2/fov_unlock.cfg': cfg_text(9999),
        'C:/fake/Arrowhead/Helldivers2/user_settings.config': settings_text(55),
    })
    assert get(result, 'desired_fov') == 175, get(result, 'desired_fov')
    settings = files['C:/fake/Arrowhead/Helldivers2/user_settings.config']
    assert 'vertical_fov = 175' in settings
    return True


def test_old_loader_is_refused(verbose=False):
    """Loader API 0 (v14) must stop before touching anything."""
    mem, real_va, _ = build_image()
    lua_result = run_mod_with_loader_api(mem, 0)
    assert lua_result['patch_applied'] is False
    assert lua_result['phase'] == 'refused_old_loader'
    assert mem.read(real_va, 4) == MINSS_XMM1_XMM0
    return True


def run_mod_with_loader_api(mem, api):
    lua = LuaRuntime(unpack_returned_tuples=True)
    files = {
        'C:/fake/Arrowhead/Helldivers2/fov_unlock.cfg': cfg_text(120),
        'C:/fake/Arrowhead/Helldivers2/user_settings.config': settings_text(55),
    }
    env = {'APPDATA': 'C:/fake', 'LOCALAPPDATA': 'C:/fake/Local'}

    def mem_read(addr, size):
        blob = mem.read(int(addr), int(size))
        return None if blob is None else blob.hex()

    def mem_write(addr, hexstr):
        mem.write(int(addr), bytes.fromhex(str(hexstr)))
        return True

    def mem_region(addr):
        r = mem.region_at(int(addr))
        return None if r is None else f"{r['base']}:{r['size']}:{r['protect']}"

    g = lua.globals()
    g.__mem_read = mem_read
    g.__mem_write = mem_write
    g.__mem_region = mem_region
    g.__fail_write = lambda: False
    g.__bytes_to_float = lambda h: struct.unpack('<f', bytes.fromhex(str(h)))[0]
    g.__env = lua.table_from(env)
    g.__image_base = IMAGE_BASE
    lua.execute(LUA_PRELUDE)
    inner = g.__fs
    for k, v in files.items():
        inner[k] = v
    g.CowboyBingusModLoader = lua.table_from({'api': api, 'version': 15})
    lua.execute(ADDON.read_text(encoding='utf-8'))
    out = g.FOVUnlock
    return {'patch_applied': out['patch_applied'], 'phase': out['phase']}


def test_fallback_matches_when_register_allocation_differs(verbose=False):
    """Build drift: the compiler picks xmm2 instead of xmm1, so the strict byte
    shape stops matching. The value-anchored fallback must still locate the
    clamp and emit the movaps for that register."""
    mem, real_va, _ = build_image(real_xmm=2)
    result, _ = run_mod(mem, files={
        'C:/fake/Arrowhead/Helldivers2/fov_unlock.cfg': cfg_text(120),
        'C:/fake/Arrowhead/Helldivers2/user_settings.config': settings_text(55),
    })
    assert get(result, 'clamp_via') == 'fallback', get(result, 'status')
    assert get(result, 'patch_applied') is True, get(result, 'status')
    assert mem.read(real_va, 4) == bytes([0x0F, 0x28, 0xD0, 0x90]), \
        mem.read(real_va, 4).hex()
    if verbose:
        print('   verified =', get(result, 'clamp_verified'))
    return True


def test_fallback_refuses_when_ambiguous(verbose=False):
    """Two equally plausible clamps means we do not know which is the FOV one.
    The fallback must refuse rather than guess."""
    mem, real_va, decoy_va = build_image(real_xmm=2, decoy_xmm=2,
                                         decoy_has_comiss=True, decoy_45=45.0)
    result, _ = run_mod(mem, files={
        'C:/fake/Arrowhead/Helldivers2/fov_unlock.cfg': cfg_text(120),
        'C:/fake/Arrowhead/Helldivers2/user_settings.config': settings_text(55),
    })
    assert get(result, 'patch_applied') is False, 'patched an ambiguous match'
    assert mem.read(real_va, 4) == bytes([0xF3, 0x0F, 0x5D, 0xD0])
    assert mem.read(decoy_va, 4) == bytes([0xF3, 0x0F, 0x5D, 0xD0])
    # The refusal must come from the ambiguity guard, not from finding nothing.
    # (M.status gets overwritten by the later stage-2 diagnostic, so assert on
    # the clamp scan's own recorded reason.)
    reason = str(get(result, 'sig_reason'))
    assert 'ambiguous' in reason, f'refused for the wrong reason: {reason}'
    if verbose:
        print('   sig_reason =', reason)
    return True


def stage2_files(snapshot=90, fov=120):
    # shipped default: data-only, no code patch
    return {
        'C:/fake/Arrowhead/Helldivers2/fov_unlock.cfg': cfg_text(fov, code_clamp=False),
        'C:/fake/Arrowhead/Helldivers2/user_settings.config': settings_text(snapshot),
    }


def test_default_is_data_only_no_code_touch(verbose=False):
    """The shipped default must never modify an executable page: changing .text
    is what triggers nProtect GameGuard and closes the game."""
    mem, real_va, _ = build_image()
    add_stage2(mem, live_fov=90.0)
    files = {
        # no patch_code_clamp key at all -> shipped default
        'C:/fake/Arrowhead/Helldivers2/fov_unlock.cfg':
            'enabled = true\nfov = 120\nwrite_config_file = true\n',
        'C:/fake/Arrowhead/Helldivers2/user_settings.config': settings_text(90),
    }
    result, _ = run_mod(mem, files=files)
    assert get(result, 'code_clamp') == 'disabled_by_config', get(result, 'code_clamp')
    assert not get(result, 'patch_applied'), 'code page was patched by default!'
    assert mem.read(real_va, 4) == MINSS_XMM1_XMM0, 'code page was modified!'
    assert get(result, 'settings_written') is True, get(result, 'status')
    if verbose:
        print('   status =', str(get(result, 'status'))[:60])
    return True


def test_code_clamp_opt_in_still_works(verbose=False):
    """Opting in must still patch, so the flag is a real choice and not a
    silently dead branch."""
    mem, real_va, _ = build_image()
    result, _ = run_mod(mem, files={
        'C:/fake/Arrowhead/Helldivers2/fov_unlock.cfg': cfg_text(120, code_clamp=True),
        'C:/fake/Arrowhead/Helldivers2/user_settings.config': settings_text(55),
    })
    assert get(result, 'code_clamp') == 'enabled_by_config'
    assert get(result, 'patch_applied') is True, get(result, 'status')
    assert mem.read(real_va, 4) == CLAMP_PATCH
    return True


def test_stage2_writes_live_settings_object(verbose=False):
    """The boot deserializer writes a stack temporary, so the value that is
    actually applied lives in the persistent settings object. Stage 2 must
    find it and write there."""
    mem, _, _ = build_image()
    _, settings = add_stage2(mem, live_fov=90.0)
    result, _ = run_mod(mem, files=stage2_files(90, 120))
    assert get(result, 'settings_written') is True, get(result, 'status')
    assert str(get(result, 'settings_how')).startswith('known'), get(result, 'settings_detail')
    assert get(result, 'settings_phase') == 'applied'
    got = struct.unpack('<f', mem.read(settings + FOV_OFF, 4))[0]
    assert abs(got - 120.0) < 0.01, got
    if verbose:
        print('   detail =', get(result, 'settings_detail'))
    return True


def test_stage2_refuses_when_identity_check_fails(verbose=False):
    """If the field at the assumed offset does not hold what the config says,
    this is not the settings object (build drift). Refuse, do not write."""
    mem, _, _ = build_image()
    _, settings = add_stage2(mem, live_fov=77.0)
    result, _ = run_mod(mem, files=stage2_files(90, 120))
    assert not get(result, 'settings_written'), 'wrote through a failed identity check'
    assert get(result, 'settings_phase') == 'not_located', get(result, 'settings_phase')
    got = struct.unpack('<f', mem.read(settings + FOV_OFF, 4))[0]
    assert abs(got - 77.0) < 0.01, f'wrote through a failed identity check: {got}'
    if verbose:
        print('   detail =', get(result, 'settings_detail'))
    return True


def test_stage2_scanned_fallback_locates_object(verbose=False):
    """No usable known global, but the `mov rcx,[rip]; add rcx,imm32` shape is
    present: the shape scan must find the object."""
    mem, _, _ = build_image()
    add_stage2(mem, global_rva=0x1930000, scanned_site=True, live_fov=90.0)
    result, _ = run_mod(mem, files=stage2_files(90, 120))
    assert get(result, 'settings_written') is True, get(result, 'status')
    assert str(get(result, 'settings_how')).startswith('scanned'), get(result, 'settings_detail')
    return True


def test_recheck_reasserts_when_game_reverts_value(verbose=False):
    """The engine rewrites the settings object when settings are applied, so a
    one-shot write is not enough; the update hook must put it back."""
    mem, _, _ = build_image()
    _, settings = add_stage2(mem, live_fov=90.0)
    holder = {}
    result, _ = run_mod(mem, files=stage2_files(90, 120), with_update=True,
                        globals_out=holder)
    assert get(result, 'settings_written') is True, get(result, 'status')
    assert get(result, 'recheck') == 'armed'

    # the game reverts our write...
    mem.write(settings + FOV_OFF, struct.pack('<f', 90.0))
    # ...and time advances past the 1.5s throttle
    holder['g'].__now_ms = 12345 + 2000
    holder['g'].update(0.016)

    got = struct.unpack('<f', mem.read(settings + FOV_OFF, 4))[0]
    assert abs(got - 120.0) < 0.01, f'recheck did not re-assert: {got}'
    assert get(result, 'reasserts') >= 1, get(result, 'reasserts')
    return True


def test_stage2_finds_field_when_offset_drifted(verbose=False):
    """The +0x2C field offset is also build-specific. If it moves, the value
    scan must discover the new offset - and must not touch the old one."""
    mem, _, _ = build_image()
    _, settings = add_stage2(mem, live_fov=90.0, heap_slot=0x34)
    result, _ = run_mod(mem, files=stage2_files(90, 120))
    assert get(result, 'settings_written') is True, get(result, 'status')
    assert get(result, 'settings_off') == 0x34, get(result, 'settings_how')
    got = struct.unpack('<f', mem.read(settings + 0x34, 4))[0]
    assert abs(got - 120.0) < 0.01, got
    leftover = struct.unpack('<f', mem.read(settings + FOV_OFF, 4))[0]
    assert leftover == 0.0, f'wrote at the assumed offset instead: {leftover}'
    if verbose:
        print('   how =', get(result, 'settings_how'))
    return True


def test_chain_dump_is_written_when_object_not_located(verbose=False):
    """A failed live run must hand back enough to fix the offsets offline."""
    mem, _, _ = build_image()          # no settings-object data at all
    result, files = run_mod(mem, files=stage2_files(90, 120))
    assert not get(result, 'settings_written')
    assert get(result, 'settings_phase') == 'not_located', get(result, 'settings_phase')
    dump = files.get('C:/fake/Local/CowboyBingus/Helldivers2/FOV_CHAIN_DUMP.txt')
    assert dump, 'chain dump missing'
    assert 'reason=' in dump, dump[:200]
    assert 'known_chain:' in dump, dump[:300]
    assert 'snapshot_fov=90' in dump, dump[:300]
    assert 'desired_fov=120' in dump, dump[:300]
    if verbose:
        print('   dump[3] =', dump.splitlines()[3])
    return True


def test_clamp_dump_has_content_when_constants_moved(verbose=False):
    """If a future build changed the clamp bounds, the constants are unknown.
    The dump must still carry raw minss bytes so the shape can be re-derived
    offline instead of costing another game launch."""
    mem, _, _ = build_image(real_site=False, decoy_first=False)
    at = TEXT_VA + 0x800
    mem.write(at, bytes([0xF3, 0x0F, 0x5D, 0xC8]))     # a bare minss, no constants
    # the clamp-signature dump only exists on the code-patch path
    result, files = run_mod(mem, files={
        'C:/fake/Arrowhead/Helldivers2/fov_unlock.cfg': cfg_text(120, code_clamp=True),
        'C:/fake/Arrowhead/Helldivers2/user_settings.config': settings_text(90),
    })
    assert not get(result, 'patch_applied')
    dump = files.get('C:/fake/Local/CowboyBingus/Helldivers2/FOV_SIGNATURE_DUMP.txt')
    assert dump, 'signature dump missing'
    assert 'sites=1' in dump, dump[:300]
    assert 'ctx[-0x20..+0x10]:' in dump, dump[:400]
    if verbose:
        print('   minss_total =', get(result, 'minss_total'))
    return True


def test_cross_launch_verdict_detects_engine_clamp(verbose=False):
    """We wrote 120 last run; this launch the file reads 90. That proves the
    engine clamped us and the in-time patch did not land - the decisive datum,
    delivered automatically instead of asking the player to diff files."""
    mem, _, _ = build_image()
    add_stage2(mem, live_fov=90.0)
    files = stage2_files(90, 120)
    files['C:/fake/Local/CowboyBingus/Helldivers2/fov_last_write.txt'] = 'fov = 120\n'
    result, written = run_mod(mem, files=files)
    verdict = str(get(result, 'last_write_verdict'))
    assert 'clamped back' in verdict, verdict
    # and our own write is recorded for the next launch
    state = written.get('C:/fake/Local/CowboyBingus/Helldivers2/fov_last_write.txt')
    assert state and '120' in state, state
    if verbose:
        print('   verdict =', verdict)
    return True


def test_cross_launch_verdict_reports_acceptance(verbose=False):
    """If the file still holds what we wrote, the ceiling path is working."""
    mem, _, _ = build_image()
    add_stage2(mem, live_fov=120.0)
    files = stage2_files(120, 120)
    files['C:/fake/Local/CowboyBingus/Helldivers2/fov_last_write.txt'] = 'fov = 120\n'
    result, _ = run_mod(mem, files=files)
    verdict = str(get(result, 'last_write_verdict'))
    assert 'accepted' in verdict, verdict
    return True


def test_data_only_mode_never_touches_page_protections(verbose=False):
    """The default path must not call VirtualProtect at all: that call is the
    cross-mod ffi.cdef hazard AND the GameGuard-visible action. A writable heap
    page needs no protection change."""
    mem, _, _ = build_image()
    add_stage2(mem, live_fov=90.0)
    stats = {}
    result, _ = run_mod(mem, files=stage2_files(90, 120), stats_out=stats)
    assert get(result, 'settings_written') is True, get(result, 'status')
    assert stats['vp_count'] == 0, \
        f'data-only mode called VirtualProtect {stats["vp_count"]} time(s)'
    if verbose:
        print('   vp_count =', stats['vp_count'])
    return True


def test_data_write_to_a_code_page_is_refused(verbose=False):
    """WriteProcessMemory is the only gate now, so prove a read-only/exec page
    really is refused by the model - otherwise the guarantee is untested."""
    mem, real_va, _ = build_image()
    assert not mem.is_writable(TEXT_VA, 4), 'fixture: TEXT must be non-writable'
    # the addon never writes there in data-only mode, so assert on the model
    assert mem.read(real_va, 4) == MINSS_XMM1_XMM0
    return True


def test_retry_applies_when_object_appears_late(verbose=False):
    """Load order decides whether the settings object exists when the addon
    runs. If it is not there yet, the update hook must retry and succeed -
    that is what makes placement stop mattering."""
    mem, _, _ = build_image()          # no settings object at load time
    holder, stats = {}, {}
    result, _ = run_mod(mem, files=stage2_files(90, 120), with_update=True,
                        globals_out=holder, stats_out=stats)
    assert not get(result, 'settings_written')
    assert get(result, 'settings_phase') == 'not_located', get(result, 'settings_phase')

    # the engine finishes initialising and the object appears
    _, settings = add_stage2(mem, live_fov=90.0)
    holder['g'].__now_ms = 12345 + 9000          # past the 8s retry backoff
    holder['g'].update(0.016)

    assert get(result, 'settings_written') is True, get(result, 'status')
    got = struct.unpack('<f', mem.read(settings + FOV_OFF, 4))[0]
    assert abs(got - 120.0) < 0.01, f'retry did not apply: {got}'
    if verbose:
        print('   attempts =', get(result, 'stage2_attempts'))
    return True


def syntax_gate(source):
    """Compile `source` with LuaJIT and scan for 5.1-incompatible tokens.

    Returns None when the source is acceptable, else a reason string.
    """
    bad = [tok for tok in LUAJIT_FORBIDDEN if tok in source]
    if bad:
        return f'forbidden token(s): {bad}'
    lua = LuaRuntime()
    g = lua.globals()
    g.__src = source
    verdict = lua.execute(
        "local loader = loadstring or load\n"
        "local chunk, err = loader(__src)\n"
        "if chunk then return 'OK' end\n"
        "return 'ERR: ' .. tostring(err)\n"
    )
    if verdict != 'OK':
        return str(verdict)
    return None


def test_syntax_gate_accepts_the_shipped_source(verbose=False):
    reason = syntax_gate(ADDON.read_text(encoding='utf-8'))
    assert reason is None, f'shipped source failed the gate: {reason}'
    return True


def test_syntax_gate_catches_luajit_incompatible_token(verbose=False):
    """Mutation test for the gate itself: if floor division sneaks back in,
    the gate must fail. Without this, the gate is unverified and could itself
    be the thing that lets a build fail on load."""
    good = ADDON.read_text(encoding='utf-8')
    assert syntax_gate(good) is None, 'baseline must pass before mutating'
    mutated = good.replace('local MOD_NAME', 'local _half = 5 // 2\nlocal MOD_NAME', 1)
    reason = syntax_gate(mutated)
    assert reason is not None, 'gate accepted floor division - gate is unverified'
    assert '//' in reason, reason
    return True


def test_signature_dump_is_written_on_failure(verbose=False):
    """6.19/6.30: a failed run must leave the raw bytes behind, so the next
    round can be fixed offline instead of burning another game launch."""
    mem, _, _ = build_image(real_site=False, decoy_has_comiss=False, decoy_45=45.0)
    result, files = run_mod(mem, files={
        'C:/fake/Arrowhead/Helldivers2/fov_unlock.cfg': cfg_text(120),
        'C:/fake/Arrowhead/Helldivers2/user_settings.config': settings_text(55),
    })
    assert get(result, 'patch_applied') is False
    dump = files.get('C:/fake/Local/CowboyBingus/Helldivers2/FOV_SIGNATURE_DUMP.txt')
    assert dump, 'signature dump not written on failure'
    assert 'reason=' in dump
    assert 'ctx[-0x20..+0x10]:' in dump, dump[:400]
    return True


def test_float_self_check_blocks_broken_decoder(verbose=False):
    """6.36: if the pure-Lua float decoder is wrong, the mod must refuse to run
    rather than score memory with garbage constants."""
    good = ADDON.read_text(encoding='utf-8')
    mutated = good.replace('return sign * (1 + mant / 8388608) * 2 ^ (exp - 127)',
                           'return sign * (1 + mant / 8388608) * 2 ^ (exp - 128)', 1)
    assert mutated != good, 'mutation did not apply'
    mem, _, _ = build_image()
    result, _ = run_mod(mem, files={
        'C:/fake/Arrowhead/Helldivers2/fov_unlock.cfg': cfg_text(120),
        'C:/fake/Arrowhead/Helldivers2/user_settings.config': settings_text(55),
    }, source_override=mutated)
    assert get(result, 'patch_applied') is False
    assert get(result, 'phase') == 'float_decoder_broken', get(result, 'phase')
    return True


TESTS = [
    test_source_has_no_bom,
    test_packer_refuses_a_bom,
    test_packer_refuses_duplicate_declaration,
    test_packaged_body_compiles,
    test_default_is_data_only_no_code_touch,
    test_data_only_mode_never_touches_page_protections,
    test_data_write_to_a_code_page_is_refused,
    test_retry_applies_when_object_appears_late,
    test_code_clamp_opt_in_still_works,
    test_patches_real_site_and_ignores_decoy,
    test_backup_written_once,
    test_status_file_first_line,
    test_refuses_when_45_constant_is_wrong,
    test_refuses_decoy_without_comiss_and_ja,
    test_refuses_when_signature_absent,
    test_fallback_matches_when_register_allocation_differs,
    test_fallback_refuses_when_ambiguous,
    test_signature_dump_is_written_on_failure,
    test_stage2_writes_live_settings_object,
    test_stage2_refuses_when_identity_check_fails,
    test_stage2_scanned_fallback_locates_object,
    test_stage2_finds_field_when_offset_drifted,
    test_chain_dump_is_written_when_object_not_located,
    test_clamp_dump_has_content_when_constants_moved,
    test_cross_launch_verdict_detects_engine_clamp,
    test_cross_launch_verdict_reports_acceptance,
    test_recheck_reasserts_when_game_reverts_value,
    test_readback_failure_is_detected,
    test_disabled_config_does_nothing,
    test_fov_is_bounded,
    test_old_loader_is_refused,
    test_float_self_check_blocks_broken_decoder,
    test_syntax_gate_accepts_the_shipped_source,
    test_syntax_gate_catches_luajit_incompatible_token,
]


def main():
    verbose = '--verbose' in sys.argv
    print(f'(lua flavour: {LUA_FLAVOUR})')
    failures = []
    for test in TESTS:
        name = test.__name__
        try:
            test(verbose=verbose)
            print(f'PASS  {name}')
        except Exception as exc:                      # noqa: BLE001
            failures.append((name, exc))
            print(f'FAIL  {name}: {type(exc).__name__}: {exc}')
    print()
    if failures:
        print(f'{len(failures)} of {len(TESTS)} tests FAILED')
        return 1
    print(f'all {len(TESTS)} tests passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
