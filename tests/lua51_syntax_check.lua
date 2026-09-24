-- Lua 5.1 / LuaJIT syntax gate.
--
-- lupa ships Lua 5.4, which accepts operators LuaJIT rejects (bitwise &, |, //,
-- goto). The game runs LuaJIT, so the addon must parse under a real 5.1 host.
-- This only compiles the source; it does not run it.
--
-- Run: work\host\lua51host.exe FOV-Unlock\tests\lua51_syntax_check.lua

local path = [[C:\Users\kugua\Desktop\hd2-mod\FOV-Unlock\Source\mods\hd2\fov_unlock.lua]]
local f = assert(io.open(path, 'r'), 'cannot open ' .. path)
local src = f:read('*a')
f:close()

local chunk, err = loadstring(src, path)
if not chunk then
    io.stderr:write('SYNTAX ERROR: ' .. tostring(err) .. '\n')
    os.exit(1)
end

-- A few operators/APIs LuaJIT does not have; catch them textually as a
-- belt-and-braces check in case a future edit slips one in. Compiling alone is
-- not enough: a token can sit inside a branch the simulation never runs.
local FORBIDDEN = {
    '//', 'goto ', 'math.type', 'string.pack', 'string.unpack',
    'table.move', '::', '& 0x', '| 0x',
}
for _, bad in ipairs(FORBIDDEN) do
    if src:find(bad, 1, true) then
        io.stderr:write('LUAJIT-INCOMPATIBLE TOKEN: ' .. bad .. '\n')
        os.exit(1)
    end
end

-- Mutation self-test for this gate: the same scan must reject floor division
-- when it is present, otherwise the gate itself is unverified.
do
    local canary = 'local x = 5 // 2'
    local caught = false
    for _, bad in ipairs(FORBIDDEN) do
        if canary:find(bad, 1, true) then caught = true end
    end
    if not caught then
        io.stderr:write('GATE SELF-TEST FAILED: floor division not caught\n')
        os.exit(1)
    end
end

print('lua51 syntax OK (' .. #src .. ' bytes)')
