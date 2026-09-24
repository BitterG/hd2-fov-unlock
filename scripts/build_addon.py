"""Build the packaged addon archives for a Bingus Shared Loader mod.

Each `Source/<resource-name>.lua` becomes one plaintext Lua resource. The
loader discovers resources from deployed `data/9ba626afa44a3aa3.patch_<n>`
files, and the reference archives carry exactly one resource each, so this
script emits one patch file per resource (`9ba626afa44a3aa3.patch_0`,
`_1`, ...). A manager deploy renumbers them into the game's data directory.

Resource encoding:
    <u32 body length> <u32 version=2> <body>
where <body> is the source WITHOUT its first-line `-- HD2-Addon: <name>`
marker. The resource key is the seed-zero MurmurHash64A of the resource name.
"""
import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
from hd2patch import make_archive, read_archive, resource_hash  # noqa: E402

MARKER = '-- HD2-Addon: '


def envelope(name, source_bytes):
    # A UTF-8 BOM before the marker broke discovery in the field: the marker
    # check silently failed, a SECOND marker got prepended, and the BOM landed
    # on body line 2 where LuaJIT rejects the chunk
    # ("mods/hd2/fov_unlock.lua:39: '=' expected near 'local'").
    # The mod never loaded, and the symptom looked like "no effect".
    # Refuse loudly instead of tolerating it.
    if source_bytes.startswith(b'\xef\xbb\xbf'):
        raise ValueError(f'{name}: source starts with a UTF-8 BOM; '
                         'the addon must be BOM-less plaintext')
    first, separator, rest = source_bytes.partition(b'\n')
    if not first.startswith(MARKER.encode()):
        raise ValueError(
            f'{name}: first line must start with {MARKER!r}, got {first[:60]!r}')
    declared = first[len(MARKER):].strip().decode('utf-8', 'replace')
    if declared != name:
        raise ValueError(f'{name}: marker declares {declared!r}')
    if not separator:
        raise ValueError(f'{name}: no newline after the marker')
    marker = (MARKER + name + '\n').encode()
    body = marker + rest
    if len(marker) > 256:
        raise ValueError(f'{name}: declaration longer than 256 bytes')
    if b'\0' in body:
        raise ValueError(f'{name}: body contains a NUL byte')
    if b'\xef\xbb\xbf' in body:
        raise ValueError(f'{name}: body contains a UTF-8 BOM '
                         '(LuaJIT will reject the chunk)')
    text = body.decode('utf-8')
    if '\ufeff' in text:
        raise ValueError(f'{name}: body contains U+FEFF')
    declarations = text.count('-- HD2-Addon: ')
    if declarations != 1:
        raise ValueError(f'{name}: expected exactly one "HD2-Addon:" declaration, '
                         f'found {declarations}')
    return struct.pack('<II', len(body), 2) + body


def compile_body(body, name):
    """Compile the exact bytes the loader will compile, using LuaJIT.

    Compiling the *source file* is not enough: luaL_loadfile skips a leading
    BOM, so a BOM that survives into the archive body is invisible to a
    source-level check and fatal in the field. Returns None when lupa is
    unavailable (the test suite still covers this).
    """
    try:
        from lupa.luajit21 import LuaRuntime
    except ImportError:                                   # pragma: no cover
        return None
    lua = LuaRuntime()
    lua.globals().__body = body.decode('utf-8')
    verdict = lua.execute(
        "local loader = loadstring or load\n"
        "local f, err = loader(__body)\n"
        "if f then return 'OK' end\n"
        "return 'ERR: ' .. tostring(err)\n")
    return verdict


def build(source_root, output_dir, archive_name='9ba626afa44a3aa3.patch'):
    source_root = Path(source_root)
    files = sorted(source_root.rglob('*.lua'))
    if not files:
        raise SystemExit(f'no .lua sources under {source_root}')
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # entries: (primary first) -- the entry resource must be declared so the
    # loader can discover the mod; the impl resource is loaded by require().
    encoded = []
    for path in files:
        name = path.relative_to(source_root).with_suffix('').as_posix()
        if not name.startswith('mods/'):
            raise SystemExit(f'{path}: resource must live under mods/')
        blob = envelope(name, path.read_bytes())
        encoded.append((name, resource_hash(name), blob))
    encoded.sort(key=lambda item: (item[0].endswith('_impl'), item[0]))

    written = []
    for index, (name, digest, blob) in enumerate(encoded):
        verdict = compile_body(blob[8:], name)
        if verdict is not None and verdict != 'OK':
            raise SystemExit(f'{name}: the packaged body does not compile: {verdict}')
        target = output_dir / f'{archive_name}_{index}'
        target.write_bytes(make_archive({digest: blob}))
        # Every shipped archive is accompanied by empty `.stream` and
        # `.gpu_resources` sidecars (see the reference mods and the example mod).
        # The game creates them for its own archives and the managers lay the
        # directory out the same way, so an Addon folder without them is not the
        # layout the reference packages use.
        for suffix in ('.stream', '.gpu_resources'):
            target.with_name(target.name + suffix).write_bytes(b'')
        written.append((name, digest, target))
        print(f'  {name}  hash={digest:#018x}  lua={len(blob) - 8} bytes  -> {target.name}')

    for name, digest, target in written:
        data = target.read_bytes()
        assert resource_hash(name) == digest
        # Re-validate the way the loader does (see tools/validate_like_loader.py),
        # without depending on the reader in tools/hd2patch.py.
        import struct as _struct
        types = _struct.unpack_from('<I', data, 4)[0]
        table_start = 72 + 32 * types
        row = data[table_start:table_start + 80]
        offset = _struct.unpack_from('<I', data, table_start + 16)[0] \
            + _struct.unpack_from('<I', data, table_start + 20)[0] * 4294967296
        length = _struct.unpack_from('<I', data, table_start + 56)[0]
        assert row[8:16] == bytes([0xE2, 0x17, 0xD1, 0x2C, 0xFA, 0x8D, 0x4E, 0xA1]), target
        assert row[0:8] == _struct.pack('<Q', digest), target
        assert table_start + len(data) >= 0 and offset >= table_start + 80, (target, offset)
        assert length >= 8 and offset + length <= len(data), (target, offset, length, len(data))
        head = data[offset:offset + 8]
        body_len, body_ver = _struct.unpack_from('<II', head, 0)
        assert body_ver == 2 and body_len <= length - 8, (target, body_len, body_ver)
        body = data[offset + 8:offset + 8 + body_len]
        assert body.startswith(MARKER.encode()), body[:32]
        declared = body[len(MARKER):body.find(b'\n')].decode()
        assert declared == name, (declared, name)
        assert resource_hash(declared) == digest
    print(f'built and verified {len(written)} archive(s) in {output_dir}')
    return written


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    build(args.source, args.output_dir)


if __name__ == '__main__':
    main()
