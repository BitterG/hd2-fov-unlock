#!/usr/bin/env python3
"""Find every SSE float access to [reg+disp] with a given displacement.

Used to find who reads/writes the settings struct field vertical_fov (+0x2c).

Usage: python scan_disp.py <disp_hex> [--loads] [--stores] [--limit N]
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pe_scan import PE  # noqa: E402

DEFAULT_DUMP = (r'C:\Users\kugua\Desktop'
                r'\Helldivers2-HD2-nProtect-Bypass-And-Dumper\game.dll_dump.dll')

LOADS = [(b'\xf3\x0f\x10', 'movss-load'), (b'\xf3\x0f\x5c', 'subss'),
         (b'\xf3\x0f\x58', 'addss'), (b'\xf3\x0f\x59', 'mulss')]
STORES = [(b'\xf3\x0f\x11', 'movss-store')]


def main():
    disp = int(sys.argv[1], 16)
    want_loads = '--loads' in sys.argv or '--stores' not in sys.argv
    want_stores = '--stores' in sys.argv or '--loads' not in sys.argv
    limit = int(sys.argv[sys.argv.index('--limit') + 1]) if '--limit' in sys.argv else 60

    pe = PE(DEFAULT_DUMP)
    forms = []
    if want_loads:
        forms += LOADS
    if want_stores:
        forms += STORES

    hits = []
    for s in pe.sections:
        if s['rawsize'] < 0x10000:
            continue
        base_va = pe.image_base + s['va']
        blob = pe.data[s['raw']:s['raw'] + s['rawsize']]
        for prefix, kind in forms:
            start = 0
            while True:
                i = blob.find(prefix, start)
                if i < 0:
                    break
                start = i + 1
                modrm_at = i + len(prefix)
                if modrm_at + 5 > len(blob):
                    continue
                modrm = blob[modrm_at]
                # rm == 100 needs a SIB byte, rm == 101 with mod 00 is RIP-rel
                if (modrm & 0x07) == 0x04:
                    continue
                mod = modrm >> 6
                base = modrm & 7
                if mod == 0x01:                     # disp8
                    d = struct.unpack_from('<b', blob, modrm_at + 1)[0]
                elif mod == 0x02:                   # disp32
                    d = struct.unpack_from('<i', blob, modrm_at + 1)[0]
                else:
                    continue
                if d == disp and base != 5:
                    hits.append((base_va + i, kind, modrm, s['name'] or 'sec'))

    print(f'[disp {disp:#x}] {len(hits)} hit(s)')
    for va, kind, modrm, name in hits[:limit]:
        reg = (modrm >> 3) & 7
        base = modrm & 7
        print(f'  {va:#014x}  {kind:12s} reg={reg} base=r{base} [{name}]')
    if len(hits) > limit:
        print(f'  ... {len(hits) - limit} more')


if __name__ == '__main__':
    main()
