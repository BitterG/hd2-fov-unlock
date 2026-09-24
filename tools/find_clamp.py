#!/usr/bin/env python3
"""Find the vertical-FOV clamp in the decrypted game.dll image dump.

The settings key `vertical_fov` is clamped to [45, 90]. This scans .text for
SSE instructions whose RIP-relative memory operand points at a float constant
equal to 45.0 or 90.0, then reports sites where BOTH bounds are used close
together - the signature of a clamp.

Usage: python find_clamp.py [dump_path] [--all]
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pe_scan import PE  # noqa: E402

DEFAULT_DUMP = (r'C:\Users\kugua\Desktop'
                r'\Helldivers2-HD2-nProtect-Bypass-And-Dumper\game.dll_dump.dll')

# (bytes, length, mnemonic) - SSE forms store their RIP-relative disp32 last.
FORMS = [
    (b'\xf3\x0f\x5d', 'minss'),
    (b'\xf3\x0f\x5f', 'maxss'),
    (b'\xf3\x0f\x10', 'movss'),
    (b'\xf3\x0f\x59', 'mulss'),
    (b'\xf3\x0f\x5c', 'subss'),
    (b'\xf3\x0f\x58', 'addss'),
    (b'\x0f\x2f', 'comiss'),
    (b'\x0f\x2e', 'ucomiss'),
]
WANTED_F = {45.0: '45.0f', 90.0: '90.0f'}
WANTED_I = {45: '45', 90: '90'}


def main():
    dump = DEFAULT_DUMP
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if args:
        dump = args[0]
    show_all = '--all' in sys.argv
    pe = PE(dump)
    print(f'[dump] {dump}')
    print(f'[dump] image_base={pe.image_base:#x} sections={len(pe.sections)}')

    float_sites = []   # (insn_va, kind, const_va, value)
    int_sites = []

    for s in pe.sections:
        if s['rawsize'] < 0x10000:
            continue
        base_va = pe.image_base + s['va']
        blob = pe.data[s['raw']:s['raw'] + s['rawsize']]
        name = s['name'] or f"sec{s['va']:x}"
        for prefix, kind in FORMS:
            start = 0
            while True:
                i = blob.find(prefix, start)
                if i < 0:
                    break
                start = i + 1
                modrm_at = i + len(prefix)
                if modrm_at >= len(blob):
                    continue
                modrm = blob[modrm_at]
                if (modrm & 0xC7) != 0x05:      # mod=00 rm=101 -> RIP-relative
                    continue
                disp_at = modrm_at + 1
                if disp_at + 4 > len(blob):
                    continue
                disp = struct.unpack_from('<i', blob, disp_at)[0]
                end_va = base_va + disp_at + 4
                tgt = end_va + disp
                off = pe.va_to_off(tgt)
                if off is None or off + 4 > len(pe.data):
                    continue
                f = struct.unpack_from('<f', pe.data, off)[0]
                iv = struct.unpack_from('<i', pe.data, off)[0]
                insn_va = base_va + i
                if f in WANTED_F:
                    float_sites.append((insn_va, kind, tgt, f, name))
                if iv in WANTED_I:
                    int_sites.append((insn_va, kind, tgt, iv, name))

    print(f'\n[float] {len(float_sites)} SSE site(s) referencing 45.0f/90.0f')
    float_sites.sort()
    for va, kind, tgt, f, name in float_sites:
        print(f'  insn={va:#014x} {kind:8s} const@{tgt:#014x} = {f:g} [{name}]')

    print(f'\n[int] {len(int_sites)} SSE site(s) referencing 45/90 as int32')
    for va, kind, tgt, iv, name in int_sites[:40]:
        print(f'  insn={va:#014x} {kind:8s} const@{tgt:#014x} = {iv} [{name}]')

    # Clusters: a 45-bound and a 90-bound instruction within 0x100 bytes.
    print('\n=== candidate clamp clusters (45 and 90 within 0x100) ===')
    found = 0
    for i, (va1, k1, _, f1, _) in enumerate(float_sites):
        if f1 != 45.0:
            continue
        for va2, k2, _, f2, _ in float_sites:
            if f2 != 90.0:
                continue
            if 0 < abs(va2 - va1) <= 0x100:
                lo, hi = min(va1, va2), max(va1, va2)
                print(f'  45@{va1:#014x}({k1})  90@{va2:#014x}({k2})  gap={hi-lo:#x}')
                found += 1
    if not found:
        print('  (none)')

    if show_all:
        print('\n=== all 45 sites ===')
        for va, kind, tgt, f, _ in float_sites:
            if f == 45.0:
                print(f'  insn={va:#014x} {kind} const@{tgt:#014x}')
        print('\n=== all 90 sites ===')
        for va, kind, tgt, f, _ in float_sites:
            if f == 90.0:
                print(f'  insn={va:#014x} {kind} const@{tgt:#014x}')


if __name__ == '__main__':
    main()
